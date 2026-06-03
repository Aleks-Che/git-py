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
conflict_state_changed(dict)
    Emitted whenever the repository enters or leaves a merge / rebase /
    cherry-pick / revert conflict state. The dict payload has the
    keys ``in_progress`` (bool), ``conflicting_paths`` (``list[str]``),
    ``operation`` (``"merge" | "rebase" | "cherry-pick" | "revert" | None``),
    and operation-specific context (``source``, ``target``, ``upstream``,
    ``sha``).
busy_changed(bool)
    Emitted when a long-running operation (rebase, large merge, push,
    pull, fetch, clone) starts or finishes. UI uses this to show a
    spinner and disable buttons.
error_occurred(str)
    Emitted instead of raising; payload is a human-readable error
    message (always already wrapped by :mod:`src.core.exceptions`).
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from src.core.exceptions import (
    GitError,
    MergeConflictError,
    RebaseConflictError,
    RepositoryNotFoundError,
)
from src.core.models import RemoteInfo
from src.core.repository import RepositoryManager
from src.utils.async_worker import AsyncWorker
from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel
from src.viewmodels.commands import CommandProcessor
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.graph_viewmodel import GraphViewModel


class MainViewModel(QObject):
    """Top-level ViewModel: owns the repository, processor, and child VMs."""

    repository_changed = Signal(object)  # str | None
    error_occurred = Signal(str)
    conflict_state_changed = Signal(object)  # dict
    busy_changed = Signal(bool)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        async_enabled: bool = False,
        merge_async_threshold: int = 50,
        auto_fetch_enabled: bool = False,
        auto_fetch_interval_ms: int = 60_000,
    ) -> None:
        super().__init__(parent)
        self._repo_manager: RepositoryManager | None = None
        self._command_processor = CommandProcessor(self)
        self._graph_view_model = GraphViewModel(None, self)
        self._commit_panel_view_model = CommitPanelViewModel(self)
        self._branch_panel_view_model = BranchPanelViewModel(self)
        # ``None`` means "no conflict in progress". When a dict is
        # present it carries the operation context (see class docstring).
        self._conflict_state: dict | None = None
        self._is_busy: bool = False
        # ``async_enabled`` lets tests run the VM in pure-sync mode by
        # passing ``async_enabled=False`` in the constructor. In
        # production ``MainWindow`` constructs the VM with the default
        # ``async_enabled=True`` so rebase and large merges run on a
        # background thread per the hard rule in DEVELOPMENT_RULES.md
        # section 3.
        self._async_enabled: bool = async_enabled
        self._merge_async_threshold: int = merge_async_threshold

        # Auto-fetch timer. Default off so tests do not see surprise
        # network calls. ``MainWindow`` flips this on when the user
        # enables it in the config (Stage 9).
        self._auto_fetch_enabled: bool = auto_fetch_enabled
        self._auto_fetch_interval_ms: int = auto_fetch_interval_ms
        self._auto_fetch_timer = QTimer(self)
        self._auto_fetch_timer.setInterval(auto_fetch_interval_ms)
        self._auto_fetch_timer.setSingleShot(False)
        self._auto_fetch_timer.timeout.connect(self._on_auto_fetch_tick)

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
        if undone. Any in-progress conflict state is also cleared — it
        would otherwise refer to the old repo's paths.

        The auto-fetch timer is started when a repository is opened
        (and the user has auto-fetch enabled in the config) and
        stopped when the repository is closed.
        """
        self._repo_manager = manager
        self._command_processor.clear()
        self._clear_conflict_state()
        self._graph_view_model.set_repository(manager)
        self._commit_panel_view_model.set_repository(manager)
        self._branch_panel_view_model.set_repository(manager)
        self._update_auto_fetch_timer()
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
        self._refresh_all_views()

    def redo(self) -> None:
        """Redo the most recently undone command; refreshes views on success."""
        if not self._command_processor.can_redo:
            return
        try:
            self._command_processor.redo()
        except GitError as exc:
            self.error_occurred.emit(f"Redo failed: {exc}")
            return
        self._refresh_all_views()

    # ----- branch commands ---------------------------------------------

    def checkout_branch(self, name: str) -> None:
        """Switch ``HEAD`` to ``name`` via :class:`CheckoutCommand`.

        Refreshes every view (graph + commit panel + branch panel)
        on success because a checkout changes the working tree, the
        status, and the current branch marker in the left panel all
        at once. :class:`DirtyWorkTreeError` is surfaced through
        :attr:`error_occurred` so the panel can decide whether to
        offer a forced checkout (Stage 5+).
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import CheckoutCommand  # local import: avoids cycle

        command = CheckoutCommand(self._repo_manager, name)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def create_branch(self, name: str, target_sha: str | None = None) -> None:
        """Create a local branch via :class:`CreateBranchCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import CreateBranchCommand

        command = CreateBranchCommand(self._repo_manager, name, target_sha)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete a local branch via :class:`DeleteBranchCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import DeleteBranchCommand

        command = DeleteBranchCommand(self._repo_manager, name, force=force)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def rename_branch(self, old_name: str, new_name: str, force: bool = False) -> None:
        """Rename a local branch via :class:`RenameBranchCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import RenameBranchCommand

        command = RenameBranchCommand(
            self._repo_manager,
            old_name,
            new_name,
            force=force,
        )
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    # ----- merge / rebase / cherry-pick / revert -----------------------

    def merge_branch(self, source: str, target: str | None = None) -> None:
        """Merge ``source`` into HEAD (or ``target``) via :class:`MergeCommand`.

        On a conflict the command is **not** pushed onto the undo
        stack and the VM transitions into the conflict state — the
        UI can then drive the user through conflict resolution.

        Large merges (more than ``merge_async_threshold`` files
        between HEAD and ``source``) are routed through
        :class:`AsyncWorker` so the UI stays responsive.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import MergeCommand

        command = MergeCommand(self._repo_manager, source, target=target)
        if self._async_enabled and self._estimate_merge_size(source) > self._merge_async_threshold:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
            )
            return
        self._execute_merge_sync(command, source, target)

    def rebase_branch(self, upstream: str) -> None:
        """Rebase the current branch onto ``upstream`` via :class:`RebaseCommand`.

        Rebase is always routed through :class:`AsyncWorker` when
        ``async_enabled`` is true — it shells out to the ``git`` CLI
        and can be slow on a long history.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import RebaseCommand

        command = RebaseCommand(self._repo_manager, upstream)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
            )
            return
        self._execute_rebase_sync(command, upstream)

    def _execute_merge_sync(
        self,
        command: object,
        source: str,
        target: str | None,
    ) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except MergeConflictError as exc:
            self._set_conflict_state(
                "merge",
                conflicting_paths=exc.conflicting_paths,
                source=source,
                target=target,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def _execute_push_sync(self, command: object) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def _execute_pull_sync(
        self,
        command: object,
        remote_name: str,
        refspec: str | None,
    ) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except MergeConflictError as exc:
            self._set_conflict_state(
                "merge",
                conflicting_paths=exc.conflicting_paths,
                source=(
                    f"{remote_name}/{refspec}"
                    if refspec
                    else f"{remote_name}/{self._current_branch_shorthand()}"
                ),
                target=None,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def _execute_fetch_sync(self, command: object, silent: bool) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except GitError as exc:
            if not silent:
                self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def _execute_clone_sync(self, url: str, path: str) -> None:
        try:
            manager = RepositoryManager()
            manager.clone(url, path)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self.set_repository(manager)

    def _current_branch_shorthand(self) -> str:
        """Return the current branch shorthand, or ``""`` if unborn / no repo."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            return ""
        repo = self._repo_manager.repo
        if repo.head_is_unborn:
            return ""
        return repo.head.shorthand

    def _execute_rebase_sync(
        self,
        command: object,
        upstream: str,
    ) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except RebaseConflictError as exc:
            self._set_conflict_state(
                "rebase",
                conflicting_paths=[],
                upstream=upstream,
            )
            self.error_occurred.emit(str(exc))
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._refresh_all_views()

    def cherry_pick(self, sha: str) -> None:
        """Cherry-pick ``sha`` onto HEAD via :class:`CherryPickCommand`.

        Cherry-pick only stages the change (it does not create a new
        commit) — the user follows up with a regular :meth:`commit_changes`.
        On a conflict the staging is left as-is and the VM transitions
        into the conflict state.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import CherryPickCommand

        command = CherryPickCommand(self._repo_manager, sha)
        try:
            self._command_processor.execute(command)
        except MergeConflictError as exc:
            self._set_conflict_state(
                "cherry-pick",
                conflicting_paths=exc.conflicting_paths,
                sha=sha,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._commit_panel_view_model.refresh_status()

    def revert(self, sha: str) -> None:
        """Revert ``sha`` via :class:`RevertCommand`.

        Mirrors :meth:`cherry_pick` — stages the inverse change but
        does not commit.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import RevertCommand

        command = RevertCommand(self._repo_manager, sha)
        try:
            self._command_processor.execute(command)
        except MergeConflictError as exc:
            self._set_conflict_state(
                "revert",
                conflicting_paths=exc.conflicting_paths,
                sha=sha,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._commit_panel_view_model.refresh_status()

    def abort_merge(self) -> None:
        """Abort the in-progress merge (``git merge --abort``).

        Not a :class:`GitCommand` — this is a runtime escape hatch, not
        a step in the undo history. Clears the conflict state on
        success.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.core.operations import abort_merge as core_abort_merge

        try:
            core_abort_merge(self._repo_manager)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._clear_conflict_state()
        self._refresh_all_views()

    def abort_rebase(self) -> None:
        """Abort the in-progress rebase (``git rebase --abort``)."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.core.operations import abort_rebase as core_abort_rebase

        try:
            core_abort_rebase(self._repo_manager)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._clear_conflict_state()
        self._refresh_all_views()

    # ----- remotes: push / pull / fetch / add / remove / clone ---------

    def push_changes(
        self,
        remote_name: str = "origin",
        refspec: str | None = None,
    ) -> None:
        """Push ``refspec`` to ``remote_name`` via :class:`PushCommand`.

        Always routed through :class:`AsyncWorker` when
        ``async_enabled`` is true (per DEVELOPMENT_RULES.md §3 — push
        is a network op and must not block the UI thread). On success
        the views are refreshed; on failure the error is surfaced via
        :attr:`error_occurred` and the command is not pushed.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import PushCommand

        command = PushCommand(self._repo_manager, remote_name, refspec)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
            )
            return
        self._execute_push_sync(command)

    def pull_changes(
        self,
        remote_name: str = "origin",
        refspec: str | None = None,
    ) -> None:
        """Pull ``refspec`` from ``remote_name`` via :class:`PullCommand`.

        Always routed through :class:`AsyncWorker` when
        ``async_enabled`` is true. A conflict leaves the VM in the
        conflict state and the command is not pushed onto the undo
        stack.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import PullCommand

        command = PullCommand(self._repo_manager, remote_name, refspec)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
            )
            return
        self._execute_pull_sync(command, remote_name, refspec)

    def fetch_changes(
        self,
        remote_name: str = "origin",
        refspec: str | None = None,
        *,
        silent: bool = False,
    ) -> None:
        """Fetch ``refspec`` from ``remote_name`` via :class:`FetchCommand`.

        Always routed through :class:`AsyncWorker` when
        ``async_enabled`` is true. ``silent=True`` (used by the auto-
        fetch timer) suppresses the error signal — a background fetch
        failure is logged, not flashed in front of the user.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            if not silent:
                self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            # Auto-fetch must never block on user-driven work; the next
            # tick will retry.
            return
        from src.viewmodels.commands import FetchCommand

        command = FetchCommand(self._repo_manager, remote_name, refspec)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
                silent_on_failure=silent,
            )
            return
        self._execute_fetch_sync(command, silent)

    def add_remote(self, name: str, url: str) -> None:
        """Add a remote via :class:`AddRemoteCommand` (sync — fast op)."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import AddRemoteCommand

        command = AddRemoteCommand(self._repo_manager, name, url)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._branch_panel_view_model.refresh()

    def remove_remote(self, name: str) -> None:
        """Remove a remote via :class:`RemoveRemoteCommand` (sync)."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import RemoveRemoteCommand

        command = RemoveRemoteCommand(self._repo_manager, name)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._branch_panel_view_model.refresh()

    def list_remotes(self) -> list[RemoteInfo]:
        """Return a snapshot of the configured remotes, or ``[]`` if none."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            return []
        from src.core.operations import list_remotes

        try:
            return list_remotes(self._repo_manager)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return []

    def clone_repository(self, url: str, path: str) -> None:
        """Clone ``url`` to ``path`` via :class:`RepositoryManager.clone`.

        Routed through :class:`AsyncWorker` so a slow clone does not
        freeze the UI. On success the new repository is opened
        automatically; on failure the error is surfaced via
        :attr:`error_occurred` and no repository is bound.
        """
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        if not self._async_enabled:
            self._execute_clone_sync(url, path)
            return
        self._is_busy = True
        self.busy_changed.emit(True)

        def _work() -> None:
            manager = RepositoryManager()
            manager.clone(url, path)

        def _on_success(_: object) -> None:
            self._is_busy = False
            self.busy_changed.emit(False)
            try:
                manager = RepositoryManager(path)
            except (RepositoryNotFoundError, GitError) as exc:
                self.error_occurred.emit(str(exc))
                return
            self.set_repository(manager)

        def _on_failure(message: str) -> None:
            self._is_busy = False
            self.busy_changed.emit(False)
            self.error_occurred.emit(message)

        worker = AsyncWorker(_work)
        worker.signals.result.connect(_on_success)
        worker.signals.failed.connect(_on_failure)
        worker.signals.finished.connect(self._on_async_finished)
        QThreadPool.globalInstance().start(worker)

    # ----- auto-fetch timer --------------------------------------------

    def set_auto_fetch_enabled(self, enabled: bool) -> None:
        """Enable / disable the auto-fetch timer at runtime."""
        self._auto_fetch_enabled = enabled
        self._update_auto_fetch_timer()

    def is_auto_fetch_enabled(self) -> bool:
        return self._auto_fetch_enabled

    def set_auto_fetch_interval_ms(self, interval_ms: int) -> None:
        """Update the auto-fetch interval and restart the timer if active."""
        if interval_ms <= 0:
            # Treat as "disabled" — guard against config corruption.
            self._auto_fetch_enabled = False
            self._auto_fetch_interval_ms = 60_000
        else:
            self._auto_fetch_interval_ms = interval_ms
        self._auto_fetch_timer.setInterval(self._auto_fetch_interval_ms)
        self._update_auto_fetch_timer()

    def _update_auto_fetch_timer(self) -> None:
        """Start the timer iff a repository is open and the user opted in."""
        if (
            self._auto_fetch_enabled
            and self._repo_manager is not None
            and self._repo_manager.is_open
            and self._auto_fetch_interval_ms > 0
        ):
            if not self._auto_fetch_timer.isActive():
                self._auto_fetch_timer.start()
        elif self._auto_fetch_timer.isActive():
            self._auto_fetch_timer.stop()

    def _on_auto_fetch_tick(self) -> None:
        """Auto-fetch callback: silent fetch of ``origin`` (errors logged)."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            return
        self.fetch_changes("origin", silent=True)

    def resolve_conflict(self, path: str, resolution: str) -> None:
        """Write ``resolution`` to ``path``, stage it, and check for more conflicts.

        When all conflicts in the current operation are resolved:

        * **merge** — finalize via :func:`complete_merge` (creates the
          merge commit with the standard message).
        * **rebase** — continue via :func:`complete_rebase_continue`.
          If more commits still conflict, the conflict state is left
          in place so the user can resolve the next round.
        * **cherry-pick / revert** — clear the conflict state and let
          the user commit through the normal commit panel; the staged
          change is already in the index.

        On errors the failure is surfaced through
        :attr:`error_occurred` and the conflict state is unchanged.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._conflict_state is None:
            self.error_occurred.emit("No conflict in progress.")
            return
        if self._repo_manager.path is None:
            self.error_occurred.emit("Repository has no working directory.")
            return
        from pathlib import Path

        from src.core.operations import (
            complete_merge,
            complete_rebase_continue,
            is_rebase_in_progress,
        )

        try:
            full_path = Path(self._repo_manager.path) / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(resolution, encoding="utf-8")
            self._repo_manager.repo.index.add(path)
            self._repo_manager.repo.index.write()
        except OSError as exc:
            self.error_occurred.emit(f"Failed to resolve {path!r}: {exc}")
            return

        # Drop the resolved path from the conflict list.
        paths = list(self._conflict_state.get("conflicting_paths", []))
        if path in paths:
            paths.remove(path)
        if paths:
            # Still conflicts left — update the list and keep going.
            self._conflict_state["conflicting_paths"] = paths
            self.conflict_state_changed.emit(dict(self._conflict_state))
            return

        operation = self._conflict_state.get("operation")
        if operation == "merge":
            try:
                complete_merge(
                    self._repo_manager,
                    source=self._conflict_state.get("source") or "",
                    target=self._conflict_state.get("target"),
                )
            except (GitError, MergeConflictError) as exc:
                self.error_occurred.emit(str(exc))
                return
            self._clear_conflict_state()
            self._refresh_all_views()
            return
        if operation == "rebase":
            try:
                more = complete_rebase_continue(self._repo_manager)
            except GitError as exc:
                self.error_occurred.emit(str(exc))
                return
            if more or is_rebase_in_progress(self._repo_manager):
                # Another commit conflicted. Refetch conflict list from
                # the index and keep the conflict state active.
                from src.core.operations import _collect_conflicts
                from src.core.repository import unwrap

                with unwrap(self._repo_manager) as r:
                    paths = _collect_conflicts(r)
                self._conflict_state["conflicting_paths"] = paths
                self.conflict_state_changed.emit(dict(self._conflict_state))
                return
            self._clear_conflict_state()
            self._refresh_all_views()
            return
        if operation in ("cherry-pick", "revert"):
            # Cherry-pick / revert only stage the change; the user
            # makes a follow-up commit through the commit panel. Clear
            # the conflict state and refresh so the staged file is
            # visible in the commit panel.
            self._clear_conflict_state()
            self._commit_panel_view_model.refresh_status()
            return
        # Unknown operation — just clear and let the user figure it out.
        self._clear_conflict_state()
        self._refresh_all_views()

    def conflict_state(self) -> dict | None:
        """Return a copy of the current conflict state, or ``None``."""
        return None if self._conflict_state is None else dict(self._conflict_state)

    def is_busy(self) -> bool:
        """Return ``True`` while a long-running async operation is in progress."""
        return self._is_busy

    # ----- internals ---------------------------------------------------

    def _refresh_all_views(self) -> None:
        """Refresh graph, commit panel, and branch panel after a state change."""
        self._graph_view_model.refresh_graph()
        self._commit_panel_view_model.refresh_status()
        self._branch_panel_view_model.refresh()

    def _estimate_merge_size(self, source: str) -> int:
        """Return the number of files that differ between HEAD and ``source``.

        Used to decide whether a merge should run on a background
        thread. Returns 0 on any failure (off-by-default means we
        always run async as a safe default).
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return 0
        try:
            r = self._repo_manager.repo
            if r.head_is_unborn:
                return 0
            try:
                source_commit = r.revparse_single(source).peel(__import__("pygit2").Commit)
            except (KeyError, GitError, ValueError):
                return 0
            head_tree = r[r.head.target].tree
            source_tree = source_commit.tree
            diff = r.diff(head_tree, source_tree)
            return sum(1 for _ in diff)
        except GitError:
            return 0

    def _run_async(
        self,
        command: object,
        on_success: object,
        *,
        silent_on_failure: bool = False,
    ) -> None:
        """Run ``command.execute()`` on a worker thread.

        The work is wrapped in an :class:`AsyncWorker`; the result
        signal triggers ``on_success`` on the UI thread, the failed
        signal routes the exception through the normal VM error /
        conflict paths, and the finished signal clears the busy flag.

        ``silent_on_failure=True`` suppresses the ``error_occurred``
        signal for generic :class:`GitError` failures. Conflict state
        is still surfaced because the user must resolve it. The
        auto-fetch timer uses silent mode so a dropped connection
        does not flash a status-bar error every minute.
        """
        if self._is_busy:
            return
        self._is_busy = True
        self.busy_changed.emit(True)

        def _work() -> None:
            # Runs on the worker thread. CommandProcessor.execute
            # pushes the command to the undo stack and emits
            # ``stack_changed`` — both safe because the re-entrancy
            # guard has disabled every UI button that could race.
            self._command_processor.execute(command)  # type: ignore[arg-type]

        def _on_result(_: object) -> None:
            on_success()  # type: ignore[operator]

        worker = AsyncWorker(_work)
        worker.signals.result.connect(_on_result)
        worker.signals.failed.connect(
            lambda message: self._on_async_failed(command, message, silent_on_failure),
        )
        worker.signals.finished.connect(self._on_async_finished)
        QThreadPool.globalInstance().start(worker)

    def _on_async_failed(
        self,
        command: object,
        message: str,
        silent: bool = False,
    ) -> None:
        """Map a worker exception back into the VM's error/conflict paths."""
        # ``message`` is the stringified exception raised on the
        # worker thread. We don't have the exception instance, so we
        # re-create the state by checking the repository for in-progress
        # markers and surfacing the appropriate error.
        if self._repo_manager is None:
            if not silent:
                self.error_occurred.emit(message)
            return
        from src.core.operations import is_merge_in_progress, is_rebase_in_progress

        if is_merge_in_progress(self._repo_manager):
            from src.core.operations import _collect_conflicts
            from src.core.repository import unwrap

            with unwrap(self._repo_manager) as r:
                paths = _collect_conflicts(r)
            self._set_conflict_state("merge", conflicting_paths=paths, source=None, target=None)
            return
        if is_rebase_in_progress(self._repo_manager):
            self._set_conflict_state("rebase", conflicting_paths=[], upstream=None)
            if not silent:
                self.error_occurred.emit(message)
            return
        if not silent:
            self.error_occurred.emit(message)

    def _on_async_finished(self) -> None:
        self._is_busy = False
        self.busy_changed.emit(False)

    def _set_conflict_state(
        self,
        operation: str,
        conflicting_paths: list[str],
        **context: object,
    ) -> None:
        """Enter the conflict state and emit :attr:`conflict_state_changed`."""
        self._conflict_state = {
            "in_progress": True,
            "conflicting_paths": list(conflicting_paths),
            "operation": operation,
            **context,
        }
        self.conflict_state_changed.emit(dict(self._conflict_state))

    def _clear_conflict_state(self) -> None:
        """Leave the conflict state and notify listeners."""
        if self._conflict_state is None:
            return
        self._conflict_state = None
        self.conflict_state_changed.emit(
            {
                "in_progress": False,
                "conflicting_paths": [],
                "operation": None,
            },
        )


__all__ = ["MainViewModel"]
