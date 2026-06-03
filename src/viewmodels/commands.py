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
    abort_rebase,
    checkout_branch,
    cherry_pick,
    commit_changes,
    create_branch,
    delete_branch,
    is_rebase_in_progress,
    merge_branch,
    rebase_branch,
    rename_branch,
    reset,
    revert,
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


# ----- merge / rebase / cherry-pick / revert --------------------------------


class MergeCommand(GitCommand):
    """Merge ``source`` into the current HEAD; undo by resetting back.

    Captures the pre-merge HEAD SHA on :meth:`execute` so undo can
    move the ref back. Handles three outcomes:

    * **Up-to-date** — no SHA change; undo is a no-op.
    * **Fast-forward** — the current branch ref is moved to
      ``source_oid``; undo is ``reset(_previous_head_sha, hard)``.
    * **Three-way merge** — a merge commit with two parents is
      created; undo is ``reset(_previous_head_sha, hard)``.

    A conflict (``MergeConflictError`` from the core layer) propagates
    out of :meth:`execute`; the processor therefore does *not* push
    the command onto the undo stack on failure, and the ViewModel
    picks up the conflict state from the exception.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        source: str,
        target: str | None = None,
        message: str | None = None,
    ) -> None:
        self._repo = repo
        self._source = source
        self._target = target
        self._message = message
        self._previous_head: str | None = None
        self._head_moved = False

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
        else:
            self._previous_head = None
        merge_branch(
            self._repo,
            self._source,
            target=self._target,
            message=self._message,
        )
        if self._previous_head is None:
            self._head_moved = False
        else:
            self._head_moved = str(pygit2_repo.head.target) != self._previous_head

    def undo(self) -> None:
        if self._previous_head is None or not self._head_moved:
            # Nothing moved (up-to-date) or the original HEAD was
            # unborn — undo is a silent no-op so the user is not
            # left with a half-applied state.
            return
        reset(self._repo, self._previous_head, mode="hard")

    @property
    def name(self) -> str:
        suffix = f" into {self._target}" if self._target else ""
        return f"merge {self._source}{suffix}"


class RebaseCommand(GitCommand):
    """Rebase the current branch onto ``upstream``; undo by resetting.

    Pre-rebase HEAD SHA is captured so undo can move the ref back.
    If the rebase is still in flight (conflict) when undo runs, the
    command aborts the in-progress rebase via ``git rebase --abort``
    instead of resetting — aborting leaves the tree at the pre-rebase
    state, which is exactly what the user wants.

    ``RebaseConflictError`` propagates out of :meth:`execute` so the
    processor does not push the command on failure.
    """

    def __init__(self, repo: RepositoryManager, upstream: str) -> None:
        self._repo = repo
        self._upstream = upstream
        self._previous_head: str | None = None

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
        rebase_branch(self._repo, self._upstream)

    def undo(self) -> None:
        if is_rebase_in_progress(self._repo):
            # Conflict mid-rebase: abort to roll back to the pre-rebase
            # state. ``previous_head`` is irrelevant here — the abort
            # already restores the original branch.
            abort_rebase(self._repo)
            return
        if self._previous_head is None:
            return
        reset(self._repo, self._previous_head, mode="hard")

    @property
    def name(self) -> str:
        return f"rebase onto {self._upstream}"


class CherryPickCommand(GitCommand):
    """Cherry-pick ``sha`` onto the current HEAD; undo by resetting --mixed.

    :func:`src.core.operations.cherry_pick` only *stages* the change
    (matching ``git cherry-pick --no-commit`` semantics) — the user
    makes a follow-up commit themselves, which lands on the undo
    stack as a separate :class:`CommitCommand`. Undoing this command
    clears the staged changes by resetting the index to match the
    pre-pick HEAD; combined with the ``CommitCommand`` undo, the
    cherry-pick is fully reverted.
    """

    def __init__(self, repo: RepositoryManager, sha: str) -> None:
        self._repo = repo
        self._sha = sha
        self._previous_head: str | None = None

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
        cherry_pick(self._repo, self._sha)

    def undo(self) -> None:
        if self._previous_head is None:
            return
        # ``--mixed`` resets the index to match HEAD but leaves the
        # worktree alone. The cherry-pick only touched the index, so
        # this cleanly reverts the staged change.
        reset(self._repo, self._previous_head, mode="mixed")

    @property
    def name(self) -> str:
        short = self._sha[:7] if len(self._sha) >= 7 else self._sha
        return f"cherry-pick {short}"


class RevertCommand(GitCommand):
    """Revert ``sha``; undo by resetting --mixed (mirror of cherry-pick)."""

    def __init__(self, repo: RepositoryManager, sha: str) -> None:
        self._repo = repo
        self._sha = sha
        self._previous_head: str | None = None

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
        revert(self._repo, self._sha)

    def undo(self) -> None:
        if self._previous_head is None:
            return
        reset(self._repo, self._previous_head, mode="mixed")

    @property
    def name(self) -> str:
        short = self._sha[:7] if len(self._sha) >= 7 else self._sha
        return f"revert {short}"


__all__ = [
    "CheckoutCommand",
    "CherryPickCommand",
    "CommandProcessor",
    "CommitCommand",
    "CreateBranchCommand",
    "DeleteBranchCommand",
    "GitCommand",
    "MergeCommand",
    "RebaseCommand",
    "RenameBranchCommand",
    "RevertCommand",
]
