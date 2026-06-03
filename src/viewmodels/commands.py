"""Command pattern scaffolding for undo/redo.

Per ``docs/DEVELOPMENT_RULES.md``, every mutating Git operation (commit,
merge, rebase, branch create, checkout, stash, push, pull, fetch) MUST be
a subclass of :class:`GitCommand` and routed through
:class:`CommandProcessor`. The toolbar Undo/Redo buttons bind to the
processor, never to operations directly.

The processor is the single owner of the undo/redo stacks and the only
thing that should emit ``stack_changed``. ``GitCommand`` subclasses
capture everything they need in ``__init__`` so the processor itself
holds no Git state.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque

import pygit2
from PySide6.QtCore import QObject, Signal

from src.core.operations import (
    checkout_branch,
    commit_changes,
    create_branch,
    delete_branch,
    rename_branch,
    reset,
)
from src.core.repository import RepositoryManager


class GitCommand(ABC):
    """Base class for all mutating Git operations.

    Subclasses must capture every input they need for ``execute()`` and
    ``undo()`` in ``__init__``; the processor owns no Git state.
    """

    @abstractmethod
    def execute(self) -> None:
        """Apply the command to the repository."""

    @abstractmethod
    def undo(self) -> None:
        """Reverse the command's effect on the repository."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name used in the undo history UI."""


class CommandProcessor(QObject):
    """Centralised executor of :class:`GitCommand` instances.

    The processor is the only thing the toolbar Undo/Redo buttons bind to.
    Each successful :meth:`execute` clears the redo stack; undoing and
    redoing moves commands between the two stacks.
    """

    stack_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._undo_stack: deque[GitCommand] = deque()
        self._redo_stack: deque[GitCommand] = deque()

    def execute(self, command: GitCommand) -> None:
        """Run ``command.execute()`` and push it onto the undo stack."""
        command.execute()
        self._undo_stack.append(command)
        self._redo_stack.clear()
        self.stack_changed.emit()

    def undo(self) -> None:
        """Pop the most recent command and undo it. No-op if stack is empty."""
        if not self._undo_stack:
            return
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        self.stack_changed.emit()

    def redo(self) -> None:
        """Re-apply the most recently undone command. No-op if stack is empty."""
        if not self._redo_stack:
            return
        command = self._redo_stack.pop()
        command.execute()
        self._undo_stack.append(command)
        self.stack_changed.emit()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def clear(self) -> None:
        """Drop both stacks (e.g. after opening a different repository)."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.stack_changed.emit()


class CommitCommand(GitCommand):
    """Create a commit on ``HEAD``; undo via ``git reset --soft HEAD~1``.

    Captures the pre-commit HEAD SHA on :meth:`execute` so undo can move
    the ref back. ``stage_all=False`` because :class:`CommitPanelViewModel`
    manages the index explicitly (the user picks which files to include
    in the commit), so the index is already in the right state when this
    command runs.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        message: str,
        author: pygit2.Signature | None = None,
        committer: pygit2.Signature | None = None,
    ) -> None:
        self._repo = repo
        self._message = message
        self._author = author
        self._committer = committer
        self._previous_head: str | None = None

    def execute(self) -> None:
        if not self._message or not self._message.strip():
            from src.core.exceptions import GitError

            raise GitError("Commit message must not be empty.")
        if not self._repo.repo.head_is_unborn:
            self._previous_head = str(self._repo.repo.head.target)
        else:
            self._previous_head = None
        commit_changes(
            self._repo,
            self._message,
            author=self._author,
            committer=self._committer,
            stage_all=False,
        )

    def undo(self) -> None:
        if self._previous_head is None:
            # First commit (HEAD was unborn before ``execute``). We
            # cannot ``reset --soft`` past the unborn point, so undo
            # is a no-op — the user has to clean up manually.
            return
        reset(self._repo, self._previous_head, mode="soft")

    @property
    def name(self) -> str:
        first_line = self._message.splitlines()[0] if self._message else ""
        if len(first_line) > 50:
            first_line = first_line[:49] + "…"
        suffix = f": {first_line}" if first_line else ""
        return f"commit{suffix}"


# ----- branches ---------------------------------------------------------


class CheckoutCommand(GitCommand):
    """Switch ``HEAD`` to ``target_branch``; undo by switching back.

    The previous branch shorthand is captured on :meth:`execute` and
    restored on :meth:`undo`. If the previous ``HEAD`` was unborn
    (e.g. this is the first checkout of a fresh repo) the undo is a
    no-op — there is nothing meaningful to return to.
    """

    def __init__(self, repo: RepositoryManager, target_branch: str) -> None:
        self._repo = repo
        self._target_branch = target_branch
        self._previous_branch: str | None = None

    def execute(self) -> None:
        previous = self._previous_branch_for_undo()
        checkout_branch(self._repo, self._target_branch)
        self._previous_branch = previous

    def undo(self) -> None:
        if self._previous_branch is None:
            return
        checkout_branch(self._repo, self._previous_branch)

    @property
    def name(self) -> str:
        return f"checkout {self._target_branch}"

    def _previous_branch_for_undo(self) -> str | None:
        """Snapshot the current branch *before* switching.

        ``HEAD.shorthand`` is safe to read as long as HEAD is not
        unborn; an unborn HEAD returns ``None`` so :meth:`undo` becomes
        a no-op.
        """
        repo = self._repo.repo
        if repo.head_is_unborn:
            return None
        return repo.head.shorthand


class CreateBranchCommand(GitCommand):
    """Create local branch ``name``; undo by deleting it.

    ``force=True`` is used for the undo because the branch was just
    created by us — there is no way it could have become the current
    branch or be checked out elsewhere in the small window between
    execute and undo.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        name: str,
        target_sha: str | None = None,
    ) -> None:
        self._repo = repo
        self._name = name
        self._target_sha = target_sha
        self._existed_before = False

    def execute(self) -> None:
        existing = {b.name for b in self._repo.branches}
        self._existed_before = self._name in existing
        create_branch(self._repo, self._name, self._target_sha)

    def undo(self) -> None:
        if self._existed_before:
            # We didn't create the branch (it pre-existed) — undo is
            # a no-op, otherwise we'd be destroying user data.
            return
        delete_branch(self._repo, self._name, force=True)

    @property
    def name(self) -> str:
        return f"create branch {self._name}"


class DeleteBranchCommand(GitCommand):
    """Delete local branch ``name``; undo by recreating it on its old target.

    The deleted branch's ``target_sha`` is captured on :meth:`execute`
    so :meth:`undo` can put the ref back at the same commit. If the
    target SHA can no longer be resolved (e.g. the repo was rewritten
    by another command) the undo is a silent no-op — failing loudly
    here would be more confusing than the original deletion.
    """

    def __init__(self, repo: RepositoryManager, name: str, force: bool = False) -> None:
        self._repo = repo
        self._name = name
        self._force = force
        self._target_sha: str | None = None
        self._existed_before = False

    def execute(self) -> None:
        existing = {b.name for b in self._repo.branches}
        self._existed_before = self._name in existing
        if self._existed_before:
            branch = self._repo.repo.lookup_branch(self._name)
            self._target_sha = str(branch.target)
        delete_branch(self._repo, self._name, force=self._force)

    def undo(self) -> None:
        if not self._existed_before or self._target_sha is None:
            return
        create_branch(self._repo, self._name, self._target_sha)

    @property
    def name(self) -> str:
        return f"delete branch {self._name}"


class RenameBranchCommand(GitCommand):
    """Rename ``old_name`` to ``new_name``; undo by swapping the names back.

    Undo uses ``force=True`` so it can clobber any branch the user
    created at the *old* name between execute and undo — that branch
    was created on top of the deleted one, and rolling back means we
    want the original state back regardless.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        old_name: str,
        new_name: str,
        force: bool = False,
    ) -> None:
        self._repo = repo
        self._old_name = old_name
        self._new_name = new_name
        self._force = force

    def execute(self) -> None:
        rename_branch(self._repo, self._old_name, self._new_name, force=self._force)

    def undo(self) -> None:
        rename_branch(self._repo, self._new_name, self._old_name, force=True)

    @property
    def name(self) -> str:
        return f"rename branch {self._old_name} → {self._new_name}"


__all__ = [
    "CheckoutCommand",
    "CommandProcessor",
    "CommitCommand",
    "CreateBranchCommand",
    "DeleteBranchCommand",
    "GitCommand",
    "RenameBranchCommand",
]
