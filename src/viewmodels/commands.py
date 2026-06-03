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

from src.core.operations import commit_changes, reset
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


__all__ = ["CommandProcessor", "CommitCommand", "GitCommand"]
