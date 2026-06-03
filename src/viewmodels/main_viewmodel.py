"""Central :class:`MainViewModel` dispatching UI state to the panels.

Per ``docs/ARCHITECTURE.md`` this is the only ViewModel that owns the
:class:`src.core.repository.RepositoryManager` and the
:class:`CommandProcessor`. Sub-ViewModels (:class:`GraphViewModel`,
:class:`CommitPanelViewModel`) and widgets either receive their
repository reference from here, or look it up via a public
:meth:`repository_manager` accessor. The toolbar Undo/Redo buttons
bind to :meth:`undo` / :meth:`redo` so they go through the same
processor as every other mutating command.

Signals
-------
repository_changed(str | None)
    Emitted whenever a repository is opened, initialised, cloned, or
    closed. The payload is the new repository path, or ``None`` on
    close.
error_occurred(str)
    Emitted instead of raising; payload is a human-readable error
    message (always already wrapped by :mod:`src.core.exceptions`).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.core.exceptions import GitError, RepositoryNotFoundError
from src.core.repository import RepositoryManager
from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel
from src.viewmodels.commands import CommandProcessor
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.graph_viewmodel import GraphViewModel


class MainViewModel(QObject):
    """Top-level ViewModel: owns the repository, processor, and child VMs."""

    repository_changed = Signal(object)  # str | None
    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo_manager: RepositoryManager | None = None
        self._command_processor = CommandProcessor(self)
        self._graph_view_model = GraphViewModel(None, self)
        self._commit_panel_view_model = CommitPanelViewModel(self)
        self._branch_panel_view_model = BranchPanelViewModel(self)

        # Forward errors from child VMs so the UI has a single place
        # to listen (e.g. the status bar).
        self._graph_view_model.error_occurred.connect(self.error_occurred)
        self._commit_panel_view_model.error_occurred.connect(self.error_occurred)
        self._branch_panel_view_model.error_occurred.connect(self.error_occurred)

    # ----- child ViewModels / processor (read-only accessors) ---------

    def command_processor(self) -> CommandProcessor:
        """Return the shared :class:`CommandProcessor` driving Undo/Redo."""
        return self._command_processor

    def graph_view_model(self) -> GraphViewModel:
        return self._graph_view_model

    def commit_panel_view_model(self) -> CommitPanelViewModel:
        return self._commit_panel_view_model

    def branch_panel_view_model(self) -> BranchPanelViewModel:
        return self._branch_panel_view_model

    def repository_manager(self) -> RepositoryManager | None:
        """Return the currently bound :class:`RepositoryManager`, or ``None``."""
        return self._repo_manager

    # ----- repository binding -----------------------------------------

    def open_repository(self, path: str) -> None:
        """Open the repository at ``path`` and rewire all child ViewModels.

        Domain errors from :class:`RepositoryManager` (``RepositoryNotFoundError``,
        :class:`GitError`) are forwarded to :attr:`error_occurred`; the
        state of the ViewModel is left unchanged on failure.
        """
        manager = RepositoryManager()
        try:
            manager.open(path)
        except (RepositoryNotFoundError, GitError) as exc:
            self.error_occurred.emit(str(exc))
            return
        self.set_repository(manager)

    def close_repository(self) -> None:
        """Close the currently open repository (if any)."""
        self.set_repository(None)

    def set_repository(self, manager: RepositoryManager | None) -> None:
        """Bind a new :class:`RepositoryManager` (or ``None`` to clear).

        The undo/redo stacks are always cleared on a repository change:
        a leftover command from a different repo would have a stale
        ``RepositoryManager`` reference and could corrupt the new repo
        if undone.
        """
        self._repo_manager = manager
        self._command_processor.clear()
        self._graph_view_model.set_repository(manager)
        self._commit_panel_view_model.set_repository(manager)
        self._branch_panel_view_model.set_repository(manager)
        self.repository_changed.emit(manager.path if manager is not None else None)

    # ----- verb commands ----------------------------------------------

    def commit_changes(self, message: str) -> None:
        """Create a new commit on ``HEAD`` via :class:`CommitCommand`.

        On success the graph and commit panel are refreshed and the
        commit message is cleared. On failure the error is surfaced
        via :attr:`error_occurred` and the undo stack is unchanged
        (the failed command is never pushed).
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import CommitCommand  # local import: avoids cycle

        command = CommitCommand(self._repo_manager, message)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        # Refresh downstream views; clear the message field for the next commit.
        self._graph_view_model.refresh_graph()
        self._commit_panel_view_model.refresh_status()
        self._commit_panel_view_model.set_commit_message("")

    def stage_file(self, path: str) -> None:
        """Delegate to :meth:`CommitPanelViewModel.stage_file`."""
        self._commit_panel_view_model.stage_file(path)

    def unstage_file(self, path: str) -> None:
        """Delegate to :meth:`CommitPanelViewModel.unstage_file`."""
        self._commit_panel_view_model.unstage_file(path)

    def undo(self) -> None:
        """Undo the most recent command; refreshes views on success."""
        if not self._command_processor.can_undo:
            return
        try:
            self._command_processor.undo()
        except GitError as exc:
            self.error_occurred.emit(f"Undo failed: {exc}")
            return
        self._graph_view_model.refresh_graph()
        self._commit_panel_view_model.refresh_status()

    def redo(self) -> None:
        """Redo the most recently undone command; refreshes views on success."""
        if not self._command_processor.can_redo:
            return
        try:
            self._command_processor.redo()
        except GitError as exc:
            self.error_occurred.emit(f"Redo failed: {exc}")
            return
        self._graph_view_model.refresh_graph()
        self._commit_panel_view_model.refresh_status()


__all__ = ["MainViewModel"]
