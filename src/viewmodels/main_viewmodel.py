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

import functools

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal

from src.core.diff_parser import ParsedDiffLine
from src.core.exceptions import (
    GitError,
    MergeConflictError,
    RebaseConflictError,
    RepositoryNotFoundError,
)
from src.core.models import RemoteInfo
from src.core.repository import RepositoryManager
from src.utils.async_worker import AsyncWorker
from src.utils.config import default_config_path, load_author_signature, load_config
from src.utils.debug_mode import debug_print
from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel
from src.viewmodels.commands import CommandProcessor
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.graph_viewmodel import GraphViewModel


def _guard_mutation(method):
    """Reject the verb with an ``error_occurred`` emission while async busy.

    R2.3 (H7/H8) — every mutating verb must refuse to run while a
    long-running async worker is in flight on the same VM.  Wrapping
    the verb with this decorator keeps the busy-guard check in one
    place instead of being copy-pasted at the top of each call site
    (and silently missed by new verbs).

    The decorator short-circuits the call by emitting
    :attr:`MainViewModel.error_occurred` and returning early; the
    wrapped method is **not** invoked at all.  ``undo`` / ``redo``
    historically had their own inline checks; both are also routed
    through this decorator now (R2.3 refactor).
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        if self._is_busy:
            self.error_occurred.emit(
                "Another operation is in progress — wait until it completes.",
            )
            self._log("busy", f"{method.__name__} rejected: another op in progress", level="warn")
            return None
        return method(self, *args, **kwargs)

    return wrapper


class MainViewModel(QObject):
    """Top-level ViewModel: owns the repository, processor, and child VMs."""

    repository_changed = Signal(object)  # str | None
    error_occurred = Signal(str)
    conflict_state_changed = Signal(object)  # dict
    busy_changed = Signal(bool)
    log_message = Signal(str)  # human-readable timestamped log line
    recently_created_changed = Signal(object)  # set[str] — branches newly created in this session
    selection_changed = Signal(object)  # str | None — currently selected SHA, or WIP_SHA, or None

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
        # Keep strong references to active :class:`AsyncWorker` objects so
        # they are not garbage collected while the worker thread is running.
        # Removed in :meth:`_on_async_finished`.
        self._active_workers: set[object] = set()
        # Generation token — bumped on every :meth:`set_repository` so
        # async workers started under the previous repo deliver their
        # result into a VM that no longer holds the right state (R2.2
        # C7).  Stale results are dropped silently in ``_on_result``.
        self._async_generation: int = 0
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

        # ``selected_commit_sha`` drives the right panel. ``None`` means
        # the panel is hidden; ``WIP_SHA`` means the WIP / commit-input
        # view; any other value means the commit-detail view.
        self._selected_commit_sha: str | None = None

        # Branches created in this session — used by the graph widget to
        # keep the just-created branch visually secondary when several
        # branches share a commit (the user asked for the *source*
        # branch to keep the prominent chip). Cleared on every
        # ``set_repository`` and refreshed via
        # :attr:`recently_created_changed` so widgets can re-pull it.
        self._recently_created_branches: set[str] = set()

    # ----- destructive-action confirmation ------------------------------

    def _confirm_destructive(
        self,
        title: str,
        message: str,
        *,
        default_no: bool = True,
        parent: object | None = None,
    ) -> bool:
        """Show a Yes/No confirmation dialog for a destructive action.

        ``default_no=True`` (the default) makes the *No* button the
        default so an accidental Enter / Return does not destroy data.
        Returns ``True`` iff the user explicitly clicked Yes.

        ``parent`` is forwarded to ``QMessageBox.question``; it may be
        ``None`` in tests where no widget is available.
        """
        # Local import keeps the module importable in environments where
        # QtWidgets is not (yet) loaded — see core/commands doctests.
        from PySide6.QtWidgets import QMessageBox

        flags = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        default = (
            QMessageBox.StandardButton.No if default_no else QMessageBox.StandardButton.Yes
        )
        button = QMessageBox.question(parent, title, message, flags, default)
        return button == QMessageBox.StandardButton.Yes

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

    def local_branch_exists(self, name: str) -> bool:
        """Return ``True`` if a local branch named ``name`` exists in the open repo."""
        mgr = self._repo_manager
        if mgr is None or not mgr.is_open:
            return False
        return any(b.name == name and not b.is_remote for b in mgr.branches)

    def recently_created_branches(self) -> set[str]:
        """Snapshot of branches created in this session (since last repo change).

        The graph widget uses this set to rank branches that share a
        commit: a branch in this set is treated as "newly created"
        and gets a lower priority for the prominent chip — matching
        the source-branch-first UX requirement.
        """
        return set(self._recently_created_branches)

    # ----- repository binding -----------------------------------------

    def open_repository(self, path: str) -> None:
        """Open the repository at ``path`` and rewire all child ViewModels.

        Domain errors from :class:`RepositoryManager` (``RepositoryNotFoundError``,
        :class:`GitError`) are forwarded to :attr:`error_occurred`; the
        state of the ViewModel is left unchanged on failure.
        """
        self._log("repo", f"Opening repository at {path}")
        manager = RepositoryManager()
        try:
            manager.open(path)
        except (RepositoryNotFoundError, GitError) as exc:
            self.error_occurred.emit(str(exc))
            self._log("repo", f"Open repository {path} failed: {exc}", level="error")
            return
        self.set_repository(manager)
        self._log("repo", f"Repository opened: {path}")

    def close_repository(self) -> None:
        """Close the currently open repository (if any)."""
        self._log("repo", "Closing repository")
        self.set_repository(None)

    def set_repository(
        self,
        manager: RepositoryManager | None,
        *,
        refresh: bool = True,
        force: bool = False,
    ) -> None:
        """Bind a new :class:`RepositoryManager` (or ``None`` to clear).

        The undo/redo stacks are always cleared on a repository change:
        a leftover command from a different repo would have a stale
        ``RepositoryManager`` reference and could corrupt the new repo
        if undone. Any in-progress conflict state is also cleared — it
        would otherwise refer to the old repo's paths.

        The right-panel selection is also cleared: the previously
        selected SHA refers to a commit that may not exist in the new
        repo, and ``WIP_SHA`` no longer makes sense once the WIP
        changes.

        The auto-fetch timer is started when a repository is opened
        (and the user has auto-fetch enabled in the config) and
        stopped when the repository is closed.

        Pass ``refresh=False`` to defer the heavy graph / status /
        branch enumeration. The caller must then invoke
        :meth:`load_repository_data` to populate the panels on a
        background thread.  The default ``refresh=True`` preserves
        the existing synchronous behaviour used by tests — the graph,
        commit panel and branch panel are fully populated before the
        method returns.

        When a long-running async worker is already in flight
        (``self._is_busy``) and the caller asks for a *different*
        repository, the call is refused with an error so the
        running worker's result cannot bleed into the wrong VM
        (R2.2 M8).  Pass ``force=True`` to bypass the guard — this
        is what ``clone_repository``'s success handler uses after
        the worker has already finished, and what tests use when
        they swap repositories out of band.

        Regardless of whether the call succeeds or is refused, the
        ``_async_generation`` token is always bumped — every call to
        ``set_repository`` marks the repository change boundary, and
        any async worker that captured the previous token will see
        its result dropped in ``_on_result`` (R2.2 C7).
        """
        # Bump the generation token first so even a refused call
        # invalidates pending workers whose result might otherwise
        # slip into the (unchanged) current VM state.
        self._async_generation += 1

        current_path = (
            self._repo_manager.path if self._repo_manager is not None else None
        )
        new_path = manager.path if manager is not None else None
        if (
            not force
            and self._is_busy
            and current_path is not None
            and new_path != current_path
        ):
            msg = "Another operation is in progress — wait until it completes."
            self.error_occurred.emit(msg)
            self._log("repo", f"set_repository({new_path}) refused: busy", level="warn")
            return

        self._repo_manager = manager
        self._command_processor.clear()
        self._clear_conflict_state()
        self._update_auto_fetch_timer()
        # A new repository means a brand new history — forget the
        # previous run's "newly created" set so the chip-priority
        # logic doesn't carry stale state across repositories.
        self._recently_created_branches = set()
        self.recently_created_changed.emit(set(self._recently_created_branches))
        if self._selected_commit_sha is not None:
            self._selected_commit_sha = None
            self.selection_changed.emit(None)

        if manager is None:
            self._graph_view_model.set_repository(None)
            self._commit_panel_view_model.set_repository(None)
            self._branch_panel_view_model.set_repository(None)
            self.repository_changed.emit(None)
            return

        if refresh:
            self._graph_view_model.set_repository(manager)
            self._commit_panel_view_model.set_repository(manager)
            self._branch_panel_view_model.set_repository(manager)
        else:
            self._graph_view_model.set_repository(manager, refresh=False)
            self._commit_panel_view_model.set_repository(manager, refresh=False)
            self._branch_panel_view_model.set_repository(manager, refresh=False)

        self.repository_changed.emit(manager.path)

    def load_repository_data(self) -> None:
        """Run heavy graph / status / branch enumeration on a background thread.

        Call this after :meth:`set_repository` with ``refresh=False``
        to populate the panels without freezing the UI. ``busy_changed``
        is set to ``True`` while the worker runs so the status bar
        shows a spinner and mutating toolbar actions are disabled.

        The worker opens a **separate** :class:`RepositoryManager` on
        the same path so it never shares the ``pygit2.Repository``
        object with the main thread — libgit2 repositories are not
        thread-safe and sharing them can deadlock.

        The returned data (pure dataclasses / dicts / sets) is then
        applied on the main thread through the ``result`` signal.
        """
        import time as _time
        if self._repo_manager is None or not self._repo_manager.is_open:
            return
        if not self._async_enabled:
            debug_print("[worker] async disabled, running sync...")
            _t0 = _time.monotonic()
            self._graph_view_model.refresh_graph()
            debug_print(f"[worker] sync refresh_graph took {_time.monotonic() - _t0:.2f}s")
            self._commit_panel_view_model.refresh_status()
            debug_print(f"[worker] sync refresh_status took {_time.monotonic() - _t0:.2f}s")
            self._branch_panel_view_model.refresh()
            debug_print(f"[worker] sync refresh took {_time.monotonic() - _t0:.2f}s")
            return
        if self._is_busy:
            return

        repo_path = self._repo_manager.path
        if repo_path is None:
            return

        # Capture the generation token at dispatch time — if the user
        # swaps repositories while the worker runs, the result will be
        # stale and dropped in :meth:`_on_result` (R2.2 C7).
        generation = self._async_generation

        debug_print(f"[worker] load_repository_data: starting worker for {repo_path}")
        self._is_busy = True
        self.busy_changed.emit(True)

        def _work(repo_path: str = repo_path) -> dict | None:
            """Open a worker-owned RepositoryManager, read all data,
            and return a result dict.  The worker's pygit2.Repository is
            never accessed from the main thread, avoiding libgit2's
            thread-safety issues."""
            import time as _wt
            debug_print(f"[worker::bg] _work started, opening repo: {repo_path}")
            _t0 = _wt.monotonic()
            worker_repo = RepositoryManager()
            worker_repo.open(repo_path)
            debug_print(f"[worker::bg] open took {_wt.monotonic() - _t0:.2f}s")
            try:
                debug_print("[worker::bg] _compute_graph...")
                _t1 = _wt.monotonic()
                rows, err = GraphViewModel._compute_graph(worker_repo)
                _elapsed = _wt.monotonic() - _t1
                _nrows = len(rows) if rows else 0
                debug_print(f"[worker::bg] _compute_graph took {_elapsed:.2f}s, rows={_nrows}")
                if err is not None:
                    return {"error": err}
                debug_print("[worker::bg] _compute_status_data...")
                _t2 = _wt.monotonic()
                file_changes, staged, raw_status = (
                    CommitPanelViewModel._compute_status_data(worker_repo)
                )
                _elapsed2 = _wt.monotonic() - _t2
                _nchanges = len(file_changes)
                debug_print(
                    f"[worker::bg] _compute_status_data took {_elapsed2:.2f}s, "
                    f"changes={_nchanges}"
                )
                debug_print("[worker::bg] _compute_branch_data...")
                _t3 = _wt.monotonic()
                branch_data = (
                    BranchPanelViewModel._compute_branch_data(worker_repo)
                )
                _elapsed3 = _wt.monotonic() - _t3
                _nbranches = len(branch_data.get("local_branches", []))
                debug_print(
                    f"[worker::bg] _compute_branch_data took {_elapsed3:.2f}s, "
                    f"branches={_nbranches}"
                )
                debug_print(f"[worker::bg] total work time: {_wt.monotonic() - _t0:.2f}s")
            finally:
                worker_repo.close()
                debug_print(f"[worker::bg] worker_repo closed, total: {_wt.monotonic() - _t0:.2f}s")
            return {
                "rows": rows,
                "file_changes": file_changes,
                "staged": staged,
                "raw_status": raw_status,
                "branch_data": branch_data,
            }

        def _on_result(result: object) -> None:
            if generation != self._async_generation:
                # Stale result — the user opened a different repo
                # while the worker was in flight.  Drop silently (R2.2
                # C7/M8).  The lifespan_finished handler still runs
                # below to release the strong reference.
                return
            debug_print("[worker::ui] _on_result called")
            data: dict = result  # type: ignore[assignment]
            error = data.get("error")
            if error is not None:
                self._on_repo_load_failed(str(error))
                return
            # Apply the pre-computed data on the main thread — signal
            # emissions happen here, not in the worker.
            debug_print("[worker::ui] applying data to VMs...")
            rows: list = data["rows"]
            file_changes: list = data["file_changes"]
            staged: set = data["staged"]
            raw_status: dict = data["raw_status"]
            branch_data: dict = data["branch_data"]

            self._graph_view_model.graph_updated.emit(rows)
            self._commit_panel_view_model._file_changes = file_changes
            self._commit_panel_view_model._staged_files = staged
            self._commit_panel_view_model._raw_status = raw_status
            self._commit_panel_view_model.file_changes_changed.emit()
            self._commit_panel_view_model.staged_files_changed.emit(
                sorted(staged),
            )
            self._branch_panel_view_model._apply_branch_data(branch_data)
            debug_print("[worker::ui] data applied, calling _on_repo_load_finished")
            self._on_repo_load_finished()

        def _on_failure(exc: object) -> None:
            if generation != self._async_generation:
                # Stale failure — same logic as ``_on_result``.
                return
            self._on_repo_load_failed(str(exc))

        worker = AsyncWorker(_work)
        worker.signals.finished.connect(_on_result)
        worker.signals.failed.connect(_on_failure)
        self._active_workers.add(worker)
        worker.signals.lifespan_finished.connect(
            lambda w=worker: self._on_async_finished(w),
        )
        debug_print("[worker] dispatching to thread pool...")
        QThreadPool.globalInstance().start(worker)
        debug_print("[worker] dispatched")

    # ----- verb commands ----------------------------------------------

    @_guard_mutation
    def commit_changes(self, message: str) -> None:
        """Create a new commit on ``HEAD`` via :class:`CommitCommand`.

        On success the graph and commit panel are refreshed, the
        commit message is cleared, and the new commit is auto-selected
        so the right panel switches from the commit-input view to the
        commit-detail view of the freshly-created commit.

        On failure the error is surfaced via :attr:`error_occurred` and
        the undo stack is unchanged (the failed command is never
        pushed). The selection is left untouched.

        The author signature is taken from the app config (or from
        ``git config --global`` when ``use_default_git_credentials`` is
        ``True``).
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("commit", "Commit failed: no repository open", level="error")
            return
        from src.viewmodels.commands import CommitCommand  # local import: avoids cycle

        self._log("commit", f"Committing staged changes — message: {message[:80]}")
        config = load_config(default_config_path())
        author = load_author_signature(config)
        command = CommitCommand(self._repo_manager, message, author=author)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("commit", f"Commit failed: {exc}", level="error")
            return
        # Refresh downstream views; clear the message field for the next commit.
        self._graph_view_model.refresh_graph()
        self._commit_panel_view_model.refresh_status()
        self._commit_panel_view_model.set_commit_summary("")
        self._commit_panel_view_model.set_commit_description("")
        new_sha = str(self._repo_manager.repo.head.target)
        self.set_selected_commit(new_sha)
        self._log("commit", "Commit succeeded")

    def stage_file(self, path: str) -> None:
        """Delegate to :meth:`CommitPanelViewModel.stage_file`."""
        self._commit_panel_view_model.stage_file(path)

    def unstage_file(self, path: str) -> None:
        """Delegate to :meth:`CommitPanelViewModel.unstage_file`."""
        self._commit_panel_view_model.unstage_file(path)

    def stage_diff_line(self, path: str, line: ParsedDiffLine) -> None:
        """Stage one added or deleted row from the index-to-worktree diff."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import StageDiffLineCommand

        try:
            self._command_processor.execute(
                StageDiffLineCommand(self._repo_manager, path, line),
            )
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("staging", f"Stage line failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._commit_panel_view_model.refresh_selected_diff()
        self._log("staging", f"Staged one line in {path}")

    def unstage_diff_line(self, path: str, line: ParsedDiffLine) -> None:
        """Unstage one added or deleted row from the HEAD-to-index diff."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import UnstageDiffLineCommand

        try:
            self._command_processor.execute(
                UnstageDiffLineCommand(self._repo_manager, path, line),
            )
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("staging", f"Unstage line failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._commit_panel_view_model.refresh_selected_diff()
        self._log("staging", f"Unstaged one line in {path}")

    def stage_all_unstaged(self) -> None:
        """Stage every currently-unstaged file in one call.

        Used by the right panel's *Stage All Changes* button. Errors
        from individual ``stage_file`` calls are routed through
        :attr:`error_occurred` (the VM's normal error path) so a
        single bad file does not abort the rest of the batch.

        R3.2 (P5): the inner ``stage_file`` calls now share a
        single trailing :meth:`refresh_status`, which keeps a 1000-
        file batch O(n) pygit2 ops instead of O(n²) (each per-file
        ``stage_file`` used to refresh the full status independently).
        """
        unstaged = self._commit_panel_view_model.unstaged_paths()
        try:
            self._commit_panel_view_model.set_batch_refresh(True)
            for path in unstaged:
                self._commit_panel_view_model.stage_file(path)
        finally:
            self._commit_panel_view_model.set_batch_refresh(False)
        # Single trailing refresh — replaces the N refreshes that
        # ``stage_file`` used to emit (R3.2 P5).
        self._commit_panel_view_model.refresh_status()
        # Re-emit the selected file's diff so the preview pane tracks
        # the new staged/unstaged side once.  Without this the side
        # would remain stale until the user re-selects the file.
        self._commit_panel_view_model.recompute_selected_diff()

    def unstage_all_staged(self) -> None:
        """Unstage every currently-staged file in one call.

        Used by the right panel's red *Unstage All Changes* button.
        Each file is reset via :meth:`CommitPanelViewModel.unstage_file`;
        individual errors are surfaced through :attr:`error_occurred`
        but do not abort the batch.

        R3.2 (P5): trailing refresh is hoisted out of the loop (see
        :meth:`stage_all_unstaged` for the same change).
        """
        staged = self._commit_panel_view_model.staged_files()
        try:
            self._commit_panel_view_model.set_batch_refresh(True)
            for path in staged:
                self._commit_panel_view_model.unstage_file(path)
        finally:
            self._commit_panel_view_model.set_batch_refresh(False)
        # Single trailing refresh — replaces the N refreshes.
        self._commit_panel_view_model.refresh_status()
        self._commit_panel_view_model.recompute_selected_diff()

    # ----- commit-graph selection (drives the right panel) ------------

    def select_commit(self, sha: str) -> None:
        """Toggle selection of ``sha`` in the commit graph.

        Re-selecting the currently-selected SHA clears the selection
        (right panel hides). Selecting a different SHA replaces the
        previous one. ``WIP_SHA`` is a valid value — it switches the
        right panel to the commit-input view.

        Emits :attr:`selection_changed` with the new selection (or
        ``None`` when toggled off).
        """
        if self._selected_commit_sha == sha:
            self.set_selected_commit(None)
            return
        self.set_selected_commit(sha)

    def set_selected_commit(self, sha: str | None) -> None:
        """Force-set the selected commit (bypasses toggle behaviour).

        Used internally after a fresh commit is created so the new
        commit becomes the selected node without first clearing it.
        """
        if sha == self._selected_commit_sha:
            return
        self._selected_commit_sha = sha
        self.selection_changed.emit(sha)

    def selected_commit_sha(self) -> str | None:
        """Return the currently selected commit SHA, ``WIP_SHA``, or ``None``."""
        return self._selected_commit_sha

    def refresh_state(self) -> None:
        """Re-read the repository state from disk and refresh every panel.

        Used by the main window when the application becomes active
        so changes made in another Git client (CLI, IDE, GitKraken…)
        show up in this UI without the user having to switch tabs.
        Also a useful escape hatch for manual refresh — the toolbar
        or a keyboard shortcut can be wired to it later.

        No-op when no repository is open or when a long-running async
        operation is in flight. In either case the current panel state is
        preserved; callers can retry after the operation completes.

        Child ViewModels route expected :class:`GitError` instances through
        :attr:`error_occurred`; this method catches those errors and emits a
        user-facing refresh failure while leaving existing state intact.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return
        if self._is_busy:
            return
        self._log("refresh", "Refreshing repository state from disk")
        try:
            self._refresh_all_views()
        except GitError as exc:
            self.error_occurred.emit(f"Failed to refresh: {exc}")
            self._log("refresh", f"Refresh failed: {exc}", level="error")

    @_guard_mutation
    def undo(self) -> None:
        """Undo the most recent command; refreshes views on success.

        R2.2 M25: when a long-running async worker (push / pull /
        fetch / clone / rebase) is in flight, ``self._repo_manager``
        is the same underlying ``pygit2.Repository`` the worker is
        about to mutate.  Calling ``CommandProcessor.undo`` here
        would launch a UI-thread mutating Git op on the same
        libgit2 state, deadlocking or corrupting the index.  Reject
        the call until the worker has finished.

        R2.3 — the busy-guard is now centralised in
        :func:`_guard_mutation`; the inline check from R2.2 has
        been removed for consistency with the other verbs.
        """
        if not self._command_processor.can_undo:
            return
        try:
            self._command_processor.undo()
        except GitError as exc:
            self.error_occurred.emit(f"Undo failed: {exc}")
            self._log("undo", f"Undo failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._commit_panel_view_model.refresh_selected_diff()
        self._log("undo", "Undo succeeded")

    @_guard_mutation
    def redo(self) -> None:
        """Redo the most recently undone command; refreshes views on success.

        Same busy-guard rationale as :meth:`undo` (now via
        :func:`_guard_mutation`).

        Special cases:
        - For commands that ran on the worker thread (Push, Pull, Fetch,
          AddRemote, RemoveRemote), the redo also runs via ``_run_async``
          — network re-do cannot block the UI thread.
        - For ``MergeCommand`` / ``PullCommand`` whose previous execute
          left a conflict-state, emit ``error_occurred("Resolve conflicts
          before redoing merge.")`` and skip the redo.
        """
        if not self._command_processor.can_redo:
            return
        cmd = self._command_processor.peek_redo_command()
        # Conflict redo rejection (M6)
        if cmd is not None and getattr(cmd, "_had_conflict_in_execute", False):
            self.error_occurred.emit(
                "Resolve conflicts before redoing merge."
            )
            return
        try:
            self._command_processor.redo()
        except GitError as exc:
            self.error_occurred.emit(f"Redo failed: {exc}")
            self._log("redo", f"Redo failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._commit_panel_view_model.refresh_selected_diff()
        self._log("redo", "Redo succeeded")

    # ----- commit checkout (detached HEAD) ----------------------------

    def checkout_commit(self, sha: str) -> bool:
        """Switch ``HEAD`` to a specific commit (detached HEAD) via :class:`CheckoutCommitCommand`.

        Refreshes every view on success. :class:`DirtyWorkTreeError` is
        surfaced through :attr:`error_occurred`.

        Returns ``True`` on success, ``False`` on failure.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("checkout", f"Checkout commit {sha[:7]!r} failed: no repo", level="error")
            return False
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return False
        if sha == "WIP":
            self.error_occurred.emit("Cannot checkout the WIP node.")
            self._log("checkout", "Checkout WIP node rejected", level="warn")
            return False
        from src.viewmodels.commands import CheckoutCommitCommand  # local import: avoids cycle

        self._log("checkout", f"Checkout commit {sha[:7]!r} — detached HEAD")
        self._is_busy = True
        self.busy_changed.emit(True)
        command = CheckoutCommitCommand(self._repo_manager, sha)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("checkout", f"Checkout commit {sha[:7]!r} failed: {exc}", level="error")
            return False
        finally:
            self._is_busy = False
            self.busy_changed.emit(False)
        self._refresh_all_views()
        self._log("checkout", f"Checkout commit {sha[:7]!r} succeeded — detached HEAD")
        return True

    def get_commit_diff_text(self, sha: str) -> str:
        """Return the full unified diff for ``sha`` vs its first parent.

        Returns an empty string when no repository is open or the SHA
        cannot be resolved. Used by the graph widget's "Copy diff"
        context-menu action.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return ""
        try:
            return self._repo_manager.get_commit_diff_text(sha)
        except GitError:
            return ""

    def get_stash_diff_text(self, sha: str) -> str:
        """Return the unified diff for a stash commit.

        Returns an empty string when no repository is open or the SHA
        cannot be resolved. Used by the graph widget's "Copy diff"
        context-menu action on stash nodes.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return ""
        try:
            return self._repo_manager.get_stash_diff_text(sha)
        except GitError:
            return ""

    def get_commit_file_diff_text(self, sha: str, path: str) -> str:
        """Return the unified diff of ``path`` in commit (or stash) ``sha``.

        Works for both regular commits and stash entries — both are
        commits whose tree diffs against their first parent's tree.

        Returns the empty string when no repository is open, the SHA
        cannot be resolved, the file was not touched by the commit, or
        computing the diff fails for any other reason. Used by the
        commit-detail panel's "Copy Diff" right-click action.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return ""
        try:
            return self._repo_manager.get_commit_file_diff_text(sha, path)
        except GitError:
            return ""

    def copy_commit_file_diff(self, sha: str, path: str) -> None:
        """Copy the per-file diff of ``path`` in commit (or stash) ``sha``.

        Used by the *Copy Diff* right-click action on a file row in the
        commit-detail panel — the read-only view shown for regular
        commits and stash entries alike. Routes through
        :meth:`get_commit_file_diff_text` so the result is the same
        unified-diff text the user would get by selecting the file in
        the diff view.

        On failure (no repository open, unknown SHA, Git error) the
        clipboard is left untouched and :attr:`error_occurred` is
        emitted — never a raw exception.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        text = self.get_commit_file_diff_text(sha, path)
        if not text:
            self.error_occurred.emit(
                f"No diff available for {path!r} in {sha[:7]}",
            )
            return
        self.copy_to_clipboard(text)

    def copy_commit_files_diff(self, sha: str, paths: list[str]) -> None:
        """Copy the concatenated diffs of several files in commit ``sha``.

        Multi-file counterpart of :meth:`copy_commit_file_diff`. Each
        per-file patch is preceded by a ``# path: <p>`` header so the
        result stays readable when pasted. Files with no diff are
        silently skipped (the same SHA may legitimately touch a file
        that produces no per-file patch under the current context).

        An empty *paths* list is a no-op. If every file is skipped the
        clipboard is left untouched and :attr:`error_occurred` is
        emitted once with a summary — otherwise the concatenated text
        is copied.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if not paths:
            return
        pieces: list[str] = []
        for path in paths:
            try:
                text = self.get_commit_file_diff_text(sha, path)
            except GitError as exc:
                self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
                return
            if text:
                pieces.append(f"# path: {path}\n{text}")
        if not pieces:
            self.error_occurred.emit(
                f"No diff available for {len(paths)} file(s) in {sha[:7]}",
            )
            return
        self.copy_to_clipboard("\n".join(pieces))

    def get_workdir_diff_text(self) -> str:
        """Return the full unified diff of the working tree vs HEAD.

        Returns an empty string when no repository is open or the
        working tree is clean. Used by the graph widget's "Copy diff"
        context-menu action for the WIP node.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return ""
        try:
            return self._repo_manager.get_workdir_diff_text()
        except GitError:
            return ""

    def discard_changes(self) -> None:
        """Discard all uncommitted changes (index + workdir) via ``DiscardChangesCommand``.

        Emits ``error_occurred`` on failure. Refreshes all views on success.
        UI confirm is the caller's responsibility — this VM method matches
        the contract of an undoable operation. The main window is expected
        to prompt the user before invoking this method (already does via
        :class:`CommitPanel` confirmation flow).
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import DiscardChangesCommand

        self._log("discard", "Discarding all changes")
        command = DiscardChangesCommand(self._repo_manager)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("discard", f"Discard failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("discard", "All changes discarded")

    def discard_file_changes(self, path: str) -> None:
        """Discard uncommitted changes for a single file via :class:`DiscardFileCommand`.

        Refreshes the commit panel on success so the unstaged/staged
        lists reflect the restored file.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import DiscardFileCommand

        self._log("discard", f"Discarding changes for {path!r}")
        command = DiscardFileCommand(self._repo_manager, path)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("discard", f"Discard {path!r} failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._log("discard", f"Changes for {path!r} discarded")

    def stash_single_file(self, path: str) -> None:
        """Stash a single file via :class:`StashSingleFileCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import StashSingleFileCommand

        self._log("stash", f"Stashing single file {path!r}")
        command = StashSingleFileCommand(self._repo_manager, path)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", f"Stash {path!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("stash", f"File {path!r} stashed")

    @_guard_mutation
    def ignore_pattern(self, pattern: str) -> None:
        """Add a pattern to ``.gitignore`` via :class:`IgnoreCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import IgnoreCommand

        self._log("gitignore", f"Adding ignore pattern {pattern!r}")
        command = IgnoreCommand(self._repo_manager, pattern)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("gitignore", f"Add ignore {pattern!r} failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._log("gitignore", f"Pattern {pattern!r} added to .gitignore")

    @_guard_mutation
    def delete_file_from_disk(self, path: str) -> None:
        """Delete a file from disk; refreshes the commit panel after."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        self._log("file", f"Deleting {path!r} from disk")
        try:
            from src.core.operations import delete_file_from_disk as _delete
            _delete(self._repo_manager, path)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("file", f"Delete {path!r} failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._log("file", f"Deleted {path!r}")

    def show_in_folder(self, path: str) -> None:
        """Open the file explorer at ``path`` (or its parent folder)."""
        import os as _os
        import subprocess as _sp

        if self._repo_manager is None or not self._repo_manager.is_open:
            return
        workdir = self._repo_manager.repo.workdir
        if workdir is None:
            return
        full_path = _os.path.join(workdir, path)
        if not _os.path.exists(full_path):
            full_path = _os.path.dirname(full_path)
        if not _os.path.exists(full_path):
            return
        try:
            _sp.Popen(["explorer", "/select,", _os.path.normpath(full_path)])
        except Exception:
            pass

    def copy_file_path(self, path: str) -> None:
        """Copy ``path`` to the system clipboard."""
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(path)

    def show_repo_in_folder(self, path: str) -> None:
        """Open the OS file explorer at the repository root *path*.

        Used by the right-click context menu on a repository tab:
        *Show repo folder* opens Explorer (or the platform equivalent)
        in the directory the tab points at. The path is normalised
        through :func:`os.path.normpath` before being handed to the
        shell so trailing backslashes / mixed separators do not trip
        up ``explorer.exe``.

        Non-existent paths are silently ignored (a tab may briefly
        reference a stale path during config restore). All other
        failures are swallowed for the same reason — surfacing an
        error dialog here would interrupt the user mid-action and the
        OS error from ``explorer`` is rarely actionable anyway.
        """
        import os as _os
        import subprocess as _sp

        if not path:
            return
        normalised = _os.path.normpath(path)
        if not _os.path.isdir(normalised):
            return
        try:
            _sp.Popen(["explorer", normalised])
        except Exception:
            pass

    def copy_repo_path(self, path: str) -> None:
        """Copy *path* (a repository root) to the system clipboard.

        Thin helper paired with :meth:`show_repo_in_folder` — both
        are invoked by the tab-bar context menu. Guards on empty
        input so a stale menu with no selected row cannot clear the
        clipboard by accident.
        """
        if not path:
            return
        self.copy_to_clipboard(path)

    def copy_file_diff(self, path: str, *, staged: bool = False) -> None:
        """Copy the unified diff of a single file to the system clipboard.

        Used by the *Copy Diff* right-click action in the right panel's
        commit-input view. ``staged=True`` produces the index-vs-HEAD
        diff (what ``git commit`` would pick up); ``staged=False`` (the
        default) produces the working-tree-vs-HEAD diff.

        On any failure (no repository open, Git error) the clipboard is
        left untouched and :attr:`error_occurred` is emitted — never a
        raw exception.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        try:
            text = self._commit_panel_view_model.build_diff_text(path, staged=staged)
        except GitError as exc:
            self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
            return
        self.copy_to_clipboard(text)

    def copy_files_diff(self, paths: list[str], *, staged: bool = False) -> None:
        """Copy the concatenated diffs of *paths* to the system clipboard.

        Multi-file counterpart of :meth:`copy_file_diff`. Each per-file
        patch is preceded by a ``# path: <p>`` comment header so the
        result stays readable when pasted. An empty list is a no-op.
        """
        if not paths:
            return
        pieces: list[str] = []
        for path in paths:
            try:
                text = self._commit_panel_view_model.build_diff_text(path, staged=staged)
            except GitError as exc:
                self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
                return
            if text:
                pieces.append(f"# path: {path}\n{text}")
        if not pieces:
            return
        self.copy_to_clipboard("\n".join(pieces))

    def copy_to_clipboard(self, text: str) -> None:
        """Copy arbitrary *text* to the system clipboard."""
        from PySide6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)

    # ----- branch commands ---------------------------------------------

    def checkout_branch(self, name: str) -> bool:
        """Switch ``HEAD`` to ``name`` via :class:`CheckoutCommand`.

        Refreshes every view (graph + commit panel + branch panel)
        on success because a checkout changes the working tree, the
        status, and the current branch marker in the left panel all
        at once. :class:`DirtyWorkTreeError` is surfaced through
        :attr:`error_occurred` so the panel can decide whether to
        offer a forced checkout (Stage 5+).

        Returns ``True`` on success, ``False`` on failure.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("checkout", f"Checkout {name!r} failed: no repository open", level="error")
            return False
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return False
        from src.viewmodels.commands import CheckoutCommand  # local import: avoids cycle

        self._log("checkout", f"Checkout {name!r} — switching HEAD to refs/heads/{name}")
        # Log pre-checkout working-tree state for diagnostics.
        try:
            status = self._repo_manager.repo.status()
            dirty_pre = [p for p, _ in status.items()]
            if dirty_pre:
                self._log(
                    "checkout",
                    f"Working tree has {len(dirty_pre)} uncommitted change(s) "
                    f"before checkout: {', '.join(dirty_pre[:10])}",
                    level="warn",
                )
            else:
                self._log("checkout", "Working tree clean before checkout")
        except Exception:
            pass  # diagnostic only — never block the actual operation
        self._is_busy = True
        self.busy_changed.emit(True)
        command = CheckoutCommand(self._repo_manager, name)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("checkout", f"Checkout {name!r} failed: {exc}", level="error")
            return False
        finally:
            self._is_busy = False
            self.busy_changed.emit(False)
        self._refresh_all_views()
        self._log("checkout", f"Checkout {name!r} succeeded — HEAD is now {name}")
        return True

    @_guard_mutation
    def checkout_remote_branch(self, remote_name: str) -> bool:
        """Create a local tracking branch from ``remote_name`` and switch to it.

        ``remote_name`` is in the form ``origin/feature``. The remote prefix
        (e.g. ``origin``) is stripped so the local branch is just ``feature``.
        If a local branch with that name already exists the checkout proceeds
        directly; otherwise a branch is created at the remote-tracking tip
        first.

        Returns ``True`` on success, ``False`` on failure.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("checkout",
                      f"Checkout remote {remote_name!r} failed: no repo", level="error")
            return False
        if "/" not in remote_name:
            self.error_occurred.emit(f"Not a remote branch: {remote_name!r}")
            return False
        local_name = remote_name.split("/", 1)[1]
        self._log("checkout", f"Checkout remote {remote_name!r} -> local {local_name!r}")

        remote_branches = {
            b.name: b for b in self._repo_manager.branches if b.is_remote
        }
        remote_info = remote_branches.get(remote_name)
        if remote_info is None:
            self.error_occurred.emit(f"Unknown remote branch: {remote_name!r}")
            self._log("checkout",
                      f"Unknown remote branch: {remote_name!r}", level="error")
            return False

        target_sha = remote_info.target_sha

        existing = {b.name for b in self._repo_manager.branches if not b.is_remote}
        if local_name not in existing:
            self._log("checkout",
                      f"Creating local branch {local_name!r} at {target_sha[:7]}")
            if not self._create_branch_internal(local_name, target_sha):
                return False

        return self.checkout_branch(local_name)

    def fetch_and_checkout_remote_branch(self, remote_branch_name: str) -> None:
        """Fetch ``remote_branch_name`` from its remote, then switch to a local tracking branch.

        This is the "double-click on a remote-tracking branch" verb:
        download the latest state of the remote first, then create a
        local branch (if one does not already exist) and switch HEAD to
        it.

        The fetch runs **synchronously** on the UI thread. The other
        network ops in this VM (``push_changes`` / ``fetch_changes`` /
        ``pull_changes``) route through :class:`AsyncWorker`, but for
        this specific verb the async path is unsafe: ``pygit2``'s
        :class:`Repository` is not thread-safe when shared with the
        main thread, and a fetch kicked off from a worker has been
        observed to silently hang / never propagate its result to the
        UI (the ``result`` signal is queued back, but the underlying
        network call may have died). For a one-shot user action where
        the user is already waiting on the result, the safest thing
        is to block the UI for the duration of the fetch and surface
        the outcome immediately.

        The busy flag is set so the re-entrancy guard and the
        status-bar spinner still work. On error the issue is surfaced
        via :attr:`error_occurred` and no checkout is attempted.

        If a local branch with the same name already exists but is
        behind the remote tracking tip, the local branch is
        fast-forwarded so the user lands on the freshly downloaded
        commit. If the local branch has diverged from the remote, it
        is left alone and a warning is logged — the user must merge,
        rebase, or reset manually.

        ``remote_branch_name`` is in the form ``origin/feature``.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log(
                "checkout",
                f"Fetch+checkout {remote_branch_name!r} failed: no repo",
                level="error",
            )
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        if "/" not in remote_branch_name:
            self.error_occurred.emit(f"Not a remote branch: {remote_branch_name!r}")
            return

        remote_name, branch_name = remote_branch_name.split("/", 1)
        self._log(
            "checkout",
            f"Fetch {remote_name}/{branch_name} before checkout of "
            f"{remote_branch_name!r}",
        )

        from src.viewmodels.commands import FetchCommand

        command = FetchCommand(self._repo_manager, remote_name, branch_name)
        self._is_busy = True
        self.busy_changed.emit(True)
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("fetch", f"Fetch failed: {exc}", level="error")
            return
        finally:
            self._is_busy = False
            self.busy_changed.emit(False)

        self._log("fetch", "Fetch succeeded")
        # M9 — no ``_refresh_all_views()`` here.  The final
        # ``self.checkout_branch(local_name)`` below is the
        # authoritative refresh for this verb; refreshing earlier
        # races with the follow-up lookup / fast-forward logic and
        # is unnecessary work.  The previous extra refresh has
        # been removed (R2.3 M9).

        # Look up the (now-updated) remote tracking ref.
        remote_info = next(
            (b for b in self._repo_manager.branches
             if b.name == remote_branch_name and b.is_remote),
            None,
        )
        if remote_info is None:
            self.error_occurred.emit(f"Unknown remote branch: {remote_branch_name!r}")
            self._log(
                "checkout",
                f"Cannot find {remote_branch_name!r} after fetch",
                level="error",
            )
            return

        target_sha = remote_info.target_sha
        local_name = branch_name
        local_info = next(
            (b for b in self._repo_manager.branches
             if b.name == local_name and not b.is_remote),
            None,
        )

        if local_info is None:
            self._log(
                "checkout",
                f"Creating local branch {local_name!r} at {target_sha[:7]}",
            )
            if not self._create_branch_internal(local_name, target_sha):
                return
        elif local_info.target_sha != target_sha:
            if self._is_fast_forward(local_info.target_sha, target_sha):
                self._log(
                    "checkout",
                    f"Fast-forwarding {local_name!r} from "
                    f"{local_info.target_sha[:7]} to {target_sha[:7]}",
                )
                if not self._move_branch_ref(local_name, target_sha):
                    return
            else:
                self._log(
                    "checkout",
                    f"Local {local_name!r} has diverged from "
                    f"{remote_branch_name!r}; leaving local ref as-is",
                    level="warn",
                )
        else:
            self._log(
                "checkout",
                f"Local {local_name!r} is already at {target_sha[:7]}",
            )

        self.checkout_branch(local_name)

    def reset_local_branch_to_remote(self, remote_branch_name: str) -> None:
        """Hard-reset the local tracking branch to ``remote_branch_name`` and check it out.

        This is the destructive counterpart to
        :meth:`fetch_and_checkout_remote_branch`: the user explicitly
        asked to abandon any unpushed local work on the tracking
        branch (e.g. to roll back an unmerged merge commit) and
        switch HEAD to whatever the remote currently points at. The
        method is **not** undoable through the normal
        ``CommandProcessor`` — the lost commits are gone from the
        reflog path too once ``reset --hard`` is run, so the UI
        gates this on a confirmation dialog.

        ``remote_branch_name`` is in the form ``origin/feature``;
        the local tracking branch is the part after the ``/``.
        Behaviour:

        * Fetches the remote first so the remote tracking ref is
          up-to-date (otherwise we'd reset to a stale tip).
        * If the local branch does not exist, this is equivalent to
          a normal fetch+create+checkout — no confirmation is
          needed because there is no local work to lose.
        * If the local branch exists, hard-resets it to the remote's
          tip and checks it out. Uncommitted working-tree changes
          would also be lost; the caller is expected to have
          already verified the user is OK with that (the left-panel
          double-click is the only caller today).

        On error the issue is surfaced via :attr:`error_occurred`
        and the repository state is left untouched.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log(
                "reset",
                f"Reset to {remote_branch_name!r} failed: no repo",
                level="error",
            )
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        if "/" not in remote_branch_name:
            self.error_occurred.emit(f"Not a remote branch: {remote_branch_name!r}")
            return

        remote_name, branch_name = remote_branch_name.split("/", 1)
        self._log(
            "reset",
            f"Fetch {remote_name}/{branch_name} and reset local "
            f"{branch_name!r} to {remote_branch_name!r}",
        )

        # Step 1: fetch the remote so the local tracking ref is
        # current.  We deliberately share the sync-fetch strategy
        # with ``fetch_and_checkout_remote_branch`` — the
        # network round-trip is short and a hung async fetch on
        # Windows is a worse user experience than a brief UI
        # freeze for a one-shot user action.
        from src.viewmodels.commands import FetchCommand

        self._is_busy = True
        self.busy_changed.emit(True)
        try:
            self._command_processor.execute(
                FetchCommand(self._repo_manager, remote_name, branch_name),
            )
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("fetch", f"Fetch failed: {exc}", level="error")
            return
        finally:
            self._is_busy = False
            self.busy_changed.emit(False)

        # Step 2: look up the (now-updated) remote tracking ref. If
        # the fetch did not produce one, bail out — the remote
        # either does not have the branch or the fetch silently
        # failed and we do not want to reset to a stale tip.
        remote_info = next(
            (b for b in self._repo_manager.branches
             if b.name == remote_branch_name and b.is_remote),
            None,
        )
        if remote_info is None:
            self.error_occurred.emit(
                f"Unknown remote branch: {remote_branch_name!r}",
            )
            self._log(
                "reset",
                f"Cannot find {remote_branch_name!r} after fetch",
                level="error",
            )
            return
        target_sha = remote_info.target_sha

        # Step 3: detect whether the local branch already tracks
        # this remote.  ``origin/feature`` and ``feature`` are
        # matched by the ``upstream_name`` on the local branch —
        # if upstream points at a different branch (e.g. another
        # fork's ``upstream/feature``) we still want the user's
        # ``feature`` ref to land on the tip the user double-
        # clicked, so we fall back to the bare name when the
        # upstream is missing or different.
        local_branch = next(
            (b for b in self._repo_manager.branches
             if b.name == branch_name and not b.is_remote),
            None,
        )
        if local_branch is None:
            # No local work to lose — just create the local
            # tracking branch at the freshly fetched tip and check
            # it out.  This path is the non-destructive equivalent
            # of the existing ``fetch_and_checkout_remote_branch``
            # and is the correct behaviour when the user has not
            # set up a local branch yet.
            self._log(
                "reset",
                f"Creating local branch {branch_name!r} at {target_sha[:7]}",
            )
            from src.viewmodels.commands import CreateBranchCommand

            try:
                self._command_processor.execute(
                    CreateBranchCommand(self._repo_manager, branch_name, target_sha),
                )
            except GitError as exc:
                self.error_occurred.emit(str(exc))
                self._log("reset", f"Create branch failed: {exc}", level="error")
                return
            self._refresh_all_views()
            self.checkout_branch(branch_name)
            return

        # Step 4: hard-reset the local branch to the remote's tip
        # and check it out.  Hard reset is the right mode here: the
        # user asked to abandon unpushed commits (and any index /
        # worktree drift) so the local matches the remote
        # exactly.  A soft or mixed reset would leave the lost
        # commits in the index / worktree, which is the opposite
        # of what the user is trying to do.
        local_before = local_branch.target_sha
        self._log(
            "reset",
            f"Hard-reset local {branch_name!r} from {local_before[:7]} "
            f"to {target_sha[:7]} (remote {remote_name})",
        )
        from src.core.operations import reset as core_reset
        try:
            core_reset(self._repo_manager, target_sha, mode="hard")
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("reset", f"Reset failed: {exc}", level="error")
            return

        # Step 5: the local branch ref now points at the remote's
        # tip. Check it out so the working tree follows.  We use
        # ``GIT_CHECKOUT_FORCE`` because the hard reset has already
        # brought the index and worktree in line with the target —
        # the post-checkout dirty check inside ``checkout_branch``
        # would otherwise re-flag the files we just rewrote.
        from pygit2 import GIT_CHECKOUT_FORCE  # noqa: PLC0415

        from src.core.operations import checkout_branch as core_checkout
        try:
            core_checkout(
                self._repo_manager, branch_name, strategy=GIT_CHECKOUT_FORCE,
            )
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("checkout", f"Checkout failed: {exc}", level="error")
            return

        self._refresh_all_views()
        self._log(
            "reset",
            f"Local {branch_name!r} is now at {target_sha[:7]} "
            f"(reset to {remote_branch_name})",
        )

    def _is_fast_forward(self, old_sha: str, new_sha: str) -> bool:
        """Return ``True`` if ``new_sha`` is a descendant of ``old_sha``.

        Used to decide whether a remote tracking tip can be applied to
        a local branch as a fast-forward. ``False`` on any error
        (missing commit, repo closed, …) so the caller falls back to
        the safe "leave the local ref alone" path.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return False
        try:
            return bool(
                self._repo_manager.repo.descendant_of(new_sha, old_sha),
            )
        except (KeyError, ValueError, GitError):
            return False

    def _move_branch_ref(self, name: str, target_sha: str) -> bool:
        """Move ``refs/heads/<name>`` to ``target_sha``.

        Pure ref rewrite — no working-tree update. The subsequent
        :meth:`checkout_branch` call updates the worktree. On failure
        the error is surfaced via :attr:`error_occurred`.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return False
        try:
            ref = self._repo_manager.repo.lookup_reference(f"refs/heads/{name}")
        except KeyError as exc:
            self.error_occurred.emit(f"Unknown local branch: {name!r}")
            self._log("branch", f"Cannot fast-forward {name!r}: {exc}", level="error")
            return False
        try:
            ref.set_target(target_sha)
        except GitError as exc:
            self.error_occurred.emit(f"Cannot fast-forward {name!r}: {exc}")
            self._log(
                "branch",
                f"Fast-forward of {name!r} to {target_sha[:7]} failed: {exc}",
                level="error",
            )
            return False
        return True

    @_guard_mutation
    def create_branch(self, name: str, target_sha: str | None = None) -> None:
        """Create a local branch via :class:`CreateBranchCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("branch", f"Create branch {name!r} failed: no repo", level="error")
            return
        from src.viewmodels.commands import CreateBranchCommand

        self._log(
            "branch",
            f"Creating branch {name!r}"
            + (f" at {target_sha!r}" if target_sha else " at HEAD"),
        )
        command = CreateBranchCommand(self._repo_manager, name, target_sha)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("branch", f"Create branch {name!r} failed: {exc}", level="error")
            return
        # Mark the new branch as a session creation so the graph
        # widget can keep it visually secondary when several
        # branches share a commit (the source branch keeps the
        # prominent chip until a later operation re-orders them).
        self._recently_created_branches.add(name)
        self.recently_created_changed.emit(set(self._recently_created_branches))
        # The GraphViewModel forwards the same payload to the graph
        # widget. We emit on both so direct subscribers of either
        # signal observe the update without depending on the chain.
        self._graph_view_model.update_recently_created(
            self._recently_created_branches,
        )
        self._refresh_all_views()
        self._log("branch", f"Branch {name!r} created")

    def _create_branch_internal(self, name: str, target_sha: str) -> bool:
        """Create a local branch without refreshing views; returns success flag."""
        from src.viewmodels.commands import CreateBranchCommand

        command = CreateBranchCommand(self._repo_manager, name, target_sha)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("branch", f"Create branch {name!r} failed: {exc}", level="error")
            return False
        # Mirror the bookkeeping done by :meth:`create_branch`: internal
        # callers (e.g. fetch→create) should also mark the new branch
        # as a session creation so the chip-priority logic stays
        # consistent regardless of which entry point built the branch.
        self._recently_created_branches.add(name)
        self.recently_created_changed.emit(set(self._recently_created_branches))
        self._graph_view_model.update_recently_created(
            self._recently_created_branches,
        )
        self._log("branch", f"Branch {name!r} created at {target_sha[:7]}")
        return True

    @_guard_mutation
    def delete_branch(self, name: str, force: bool = False) -> None:
        """Delete a local branch via :class:`DeleteBranchCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import DeleteBranchCommand

        self._log("branch", f"Deleting branch {name!r}" + (" (force)" if force else ""))
        command = DeleteBranchCommand(self._repo_manager, name, force=force)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("branch", f"Delete branch {name!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("branch", f"Branch {name!r} deleted")

    def delete_remote_branch(self, remote_branch_name: str) -> None:
        """Delete a branch on the remote by pushing a deletion refspec.

        ``remote_branch_name`` is in the form ``origin/feature``. The
        push command carries a ``:refs/heads/<branch>`` refspec which
        instructs the remote to delete the ref.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log(
                "branch",
                f"Delete remote branch {remote_branch_name!r} failed: no repo",
                level="error",
            )
            return
        if "/" not in remote_branch_name:
            self.error_occurred.emit(f"Not a remote branch: {remote_branch_name!r}")
            return
        remote_name, branch_name = remote_branch_name.split("/", 1)
        spec = f":refs/heads/{branch_name}"
        self._log(
            "branch",
            f"Deleting remote branch {remote_branch_name!r} "
            f"(push :refs/heads/{branch_name})",
        )
        self.push_changes(remote_name, spec)

    def delete_local_and_remote_branch(self, local_name: str, remote_branch_name: str) -> None:
        """Delete both the local branch and its remote counterpart."""
        self._log("branch", f"Deleting local {local_name!r} and remote {remote_branch_name!r}")
        self.delete_branch(local_name)
        self.delete_remote_branch(remote_branch_name)

    @_guard_mutation
    def create_tag(
        self,
        name: str,
        target_sha: str,
        message: str | None = None,
    ) -> None:
        """Create a tag (lightweight or annotated) via :class:`CreateTagCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("tag", f"Create tag {name!r} failed: no repo", level="error")
            return
        from src.viewmodels.commands import CreateTagCommand

        kind = "annotated tag" if message else "lightweight tag"
        self._log("tag", f"Creating {kind} {name!r} at {target_sha[:7]}")
        command = CreateTagCommand(self._repo_manager, name, target_sha, message)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("tag", f"Create tag {name!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("tag", f"Tag {name!r} created")

    @_guard_mutation
    def rename_branch(self, old_name: str, new_name: str, force: bool = False) -> None:
        """Rename a local branch via :class:`RenameBranchCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import RenameBranchCommand

        self._log("branch", f"Renaming branch {old_name!r} → {new_name!r}")
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
            self._log(
                "branch",
                f"Rename branch {old_name!r} -> {new_name!r} failed: {exc}",
                level="error",
            )
            return
        self._refresh_all_views()
        self._log("branch", f"Branch renamed {old_name!r} → {new_name!r}")

    # ----- merge / rebase / cherry-pick / revert -----------------------

    def merge_branch(
        self, source: str, target: str | None = None, *, no_ff: bool = False,
    ) -> None:
        """Merge ``source`` into HEAD (or ``target``) via :class:`MergeCommand`.

        On a conflict the command is **not** pushed onto the undo
        stack and the VM transitions into the conflict state — the
        UI can then drive the user through conflict resolution.

        ``no_ff=True`` forces a merge commit even when the source
        is a fast-forward of HEAD.  The UI uses this for drag-and-
        drop and context-menu merges so the merge is visible in
        the graph (a fast-forward would silently move the ref and
        leave the user with "no commit" on the target branch).

        Large merges (more than ``merge_async_threshold`` files
        between HEAD and ``source``) are routed through
        :class:`AsyncWorker` so the UI stays responsive.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("merge", f"Merge {source!r} failed: no repository", level="error")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import MergeCommand

        self._log(
            "merge",
            f"Merge {source!r}"
            + (f" into {target!r}" if target else " into current")
            + (" (no-ff)" if no_ff else ""),
        )
        command = MergeCommand(
            self._repo_manager, source, target=target, no_ff=no_ff,
        )
        if self._async_enabled and self._estimate_merge_size(source) > self._merge_async_threshold:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
                log_tag="merge",
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
            self._log("rebase", f"Rebase onto {upstream!r} failed: no repository", level="error")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import RebaseCommand

        self._log("rebase", f"Rebase current branch onto {upstream!r}")
        command = RebaseCommand(self._repo_manager, upstream)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
                log_tag="rebase",
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
            n = len(exc.conflicting_paths)
            self._log("merge", f"Merge {source!r} produced conflicts in {n} file(s)", level="warn")
            self._set_conflict_state(
                "merge",
                conflicting_paths=exc.conflicting_paths,
                source=source,
                target=target,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("merge", f"Merge {source!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("merge", f"Merge {source!r} succeeded")

    def _execute_rebase_sync(
        self,
        command: object,
        upstream: str,
    ) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except RebaseConflictError as exc:
            self._log("rebase", f"Rebase onto {upstream!r} produced conflicts", level="warn")
            self._set_conflict_state(
                "rebase",
                conflicting_paths=[],
                upstream=upstream,
            )
            self.error_occurred.emit(str(exc))
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("rebase", f"Rebase onto {upstream!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("rebase", f"Rebase onto {upstream!r} succeeded")

    def _execute_push_sync(self, command: object) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("push", f"Push failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("push", "Push succeeded")

    def _execute_pull_sync(
        self,
        command: object,
        remote_name: str,
        refspec: str | None,
    ) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except MergeConflictError as exc:
            self._log("pull", "Pull produced merge conflicts", level="warn")
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
            self._log("pull", f"Pull failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("pull", "Pull succeeded")

    def _execute_fetch_sync(self, command: object, silent: bool) -> None:
        try:
            self._command_processor.execute(command)  # type: ignore[arg-type]
        except GitError as exc:
            if not silent:
                self.error_occurred.emit(str(exc))
                self._log("fetch", f"Fetch failed: {exc}", level="error")
            return
        self._refresh_all_views()
        if not silent:
            self._log("fetch", "Fetch succeeded")

    def _execute_clone_sync(self, url: str, path: str) -> None:
        try:
            manager = RepositoryManager()
            manager.clone(url, path)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("clone", f"Clone failed: {exc}", level="error")
            return
        self.set_repository(manager)
        self._log("clone", f"Clone finished: {url} → {path}")

    def _current_branch_shorthand(self) -> str:
        """Return the current branch shorthand, or ``""`` if unborn / no repo."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            return ""
        repo = self._repo_manager.repo
        if repo.head_is_unborn:
            return ""
        return repo.head.shorthand

    @_guard_mutation
    def cherry_pick(self, sha: str) -> None:
        """Cherry-pick ``sha`` onto HEAD via :class:`CherryPickCommand`.

        Cherry-pick only stages the change (it does not create a new
        commit) — the user follows up with a regular :meth:`commit_changes`.
        On a conflict the staging is left as-is and the VM transitions
        into the conflict state.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} failed: no repo", level="error")
            return
        from src.viewmodels.commands import CherryPickCommand

        self._log("cherry-pick", f"Cherry-pick {sha[:7].rstrip()}")
        command = CherryPickCommand(self._repo_manager, sha)
        try:
            self._command_processor.execute(command)
        except MergeConflictError as exc:
            self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} produced conflicts", level="warn")
            self._set_conflict_state(
                "cherry-pick",
                conflicting_paths=exc.conflicting_paths,
                sha=sha,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} staged")

    @_guard_mutation
    def cherry_pick_commit(self, sha: str) -> None:
        """Cherry-pick ``sha`` and commit the result immediately.

        Graph context-menu variant of :meth:`cherry_pick`: the staged
        result is committed with the original message and authorship.
        On conflict the staging is left as-is and the VM transitions
        into the conflict state.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} failed: no repo", level="error")
            return
        from src.viewmodels.commands import CherryPickCommand

        self._log("cherry-pick", f"Cherry-pick+commit {sha[:7].rstrip()}")
        command = CherryPickCommand(self._repo_manager, sha, auto_commit=True)
        try:
            self._command_processor.execute(command)
        except MergeConflictError as exc:
            self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} produced conflicts", level="warn")
            self._set_conflict_state(
                "cherry-pick",
                conflicting_paths=exc.conflicting_paths,
                sha=sha,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("cherry-pick", f"Cherry-pick {sha[:7]!r} committed")

    @_guard_mutation
    def drop_commit(self, sha: str) -> None:
        """Drop ``sha`` from the current branch via :class:`DropCommitCommand`.

        Undoable through the command processor (reset to the captured
        pre-drop HEAD). A conflicting replay surfaces the rebase
        conflict state.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("drop", f"Drop {sha[:7]!r} failed: no repo", level="error")
            return
        from src.viewmodels.commands import DropCommitCommand

        self._log("drop", f"Drop commit {sha[:7].rstrip()}")
        command = DropCommitCommand(self._repo_manager, sha)
        try:
            self._command_processor.execute(command)
        except RebaseConflictError as exc:
            self._log("drop", f"Drop {sha[:7]!r} produced conflicts", level="warn")
            self._set_conflict_state(
                "rebase",
                conflicting_paths=[],
                op="drop",
                sha=sha,
            )
            self.error_occurred.emit(str(exc))
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("drop", f"Drop {sha[:7]!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("drop", f"Drop {sha[:7]!r} succeeded")

    @_guard_mutation
    def edit_commit_message(self, sha: str, message: str) -> None:
        """Rewrite ``sha``'s message via :class:`EditCommitMessageCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("reword", f"Edit message {sha[:7]!r} failed: no repo", level="error")
            return
        from src.viewmodels.commands import EditCommitMessageCommand

        self._log("reword", f"Edit message of {sha[:7].rstrip()}")
        command = EditCommitMessageCommand(self._repo_manager, sha, message)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("reword", f"Edit message {sha[:7]!r} failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("reword", f"Edit message {sha[:7]!r} succeeded")

    def is_commit_pushed(self, sha: str) -> bool:
        """Return ``True`` when ``sha`` is reachable from any remote ref.

        Non-mutating query used by the UI to warn before history
        rewrites (drop / reword / squash) on published commits.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return False
        from src.core.operations import is_commit_pushed as _is_pushed

        try:
            return _is_pushed(self._repo_manager, sha)
        except GitError:
            return False

    def branch_of_commit(self, sha: str) -> str | None:
        """Return the name of the branch ``sha`` belongs to, or ``None``.

        Non-mutating query for the commit detail panel's "Branch:"
        line; ``None`` when no repository is open or the sha is
        unknown (e.g. the synthetic WIP row).
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return None
        from src.core.operations import branch_of_commit as _branch_of

        try:
            return _branch_of(self._repo_manager, sha)
        except GitError:
            return None

    @_guard_mutation
    def squash_commits(self, shas: list[str], message: str) -> None:
        """Squash a contiguous chain of commits via :class:`SquashCommitsCommand`.

        ``shas`` are ordered newest → oldest (graph selection order).
        A conflicting replay surfaces the rebase conflict state.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("squash", "Squash failed: no repo", level="error")
            return
        from src.viewmodels.commands import SquashCommitsCommand

        self._log("squash", f"Squash {len(shas)} commits")
        command = SquashCommitsCommand(self._repo_manager, shas, message)
        try:
            self._command_processor.execute(command)
        except RebaseConflictError as exc:
            self._log("squash", "Squash produced conflicts", level="warn")
            self._set_conflict_state(
                "rebase",
                conflicting_paths=[],
                op="squash",
            )
            self.error_occurred.emit(str(exc))
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("squash", f"Squash failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("squash", f"Squash {len(shas)} commits succeeded")

    @_guard_mutation
    def revert(self, sha: str) -> None:
        """Revert ``sha`` via :class:`RevertCommand`.

        Mirrors :meth:`cherry_pick` — stages the inverse change but
        does not commit.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("revert", f"Revert {sha[:7]!r} failed: no repo", level="error")
            return
        from src.viewmodels.commands import RevertCommand

        self._log("revert", f"Revert {sha[:7].rstrip()}")
        command = RevertCommand(self._repo_manager, sha)
        try:
            self._command_processor.execute(command)
        except MergeConflictError as exc:
            self._log("revert", f"Revert {sha[:7]!r} produced conflicts", level="warn")
            self._set_conflict_state(
                "revert",
                conflicting_paths=exc.conflicting_paths,
                sha=sha,
            )
            return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("revert", f"Revert {sha[:7]!r} failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._log("revert", f"Revert {sha[:7]!r} staged")

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

        self._log("merge", "Aborting in-progress merge")
        try:
            core_abort_merge(self._repo_manager)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("merge", f"Abort merge failed: {exc}", level="error")
            return
        self._clear_conflict_state()
        self._refresh_all_views()
        self._log("merge", "Merge aborted")

    def abort_rebase(self) -> None:
        """Abort the in-progress rebase (``git rebase --abort``)."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.core.operations import abort_rebase as core_abort_rebase

        self._log("rebase", "Aborting in-progress rebase")
        try:
            core_abort_rebase(self._repo_manager)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("rebase", f"Abort rebase failed: {exc}", level="error")
            return
        self._clear_conflict_state()
        self._refresh_all_views()
        self._log("rebase", "Rebase aborted")

    # ----- stash: push / pop / apply / drop -----------------------------

    def stash_push(self, message: str = "WIP") -> bool:
        """Push the current WIP onto the stash list via :class:`StashPushCommand`.

        A no-op (returns ``False``) when there is nothing to stash —
        the command still runs through the processor, but the undo
        path is a no-op so the user does not see a confusing "undo
        did nothing" entry in the history.

        Returns ``True`` on success, ``False`` on failure.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("stash", "Stash push failed: no repository", level="error")
            return False
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return False
        from src.viewmodels.commands import StashPushCommand

        self._log("stash", f"Stash push — message: {message[:60]!r}")
        command = StashPushCommand(self._repo_manager, message)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", f"Stash push failed: {exc}", level="error")
            return False
        self._refresh_all_views()
        self._log("stash", "Stash push succeeded")
        return True

    def stash_pop(self, index: int = 0) -> bool:
        """Apply and drop the stash at ``index`` via :class:`StashPopCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("stash", f"Stash pop @{index} failed: no repository", level="error")
            return False
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return False
        from src.viewmodels.commands import StashPopCommand

        self._log("stash", f"Stash pop @{{{index}}}")
        command = StashPopCommand(self._repo_manager, index)
        try:
            self._command_processor.execute(command)
        except MergeConflictError as exc:
            # Pop left conflicts — surface them to the user.
            self.error_occurred.emit(str(exc))
            self._log("stash", "Stash pop produced conflicts", level="warn")
            self._refresh_all_views()
            return False
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", f"Stash pop failed: {exc}", level="error")
            return False
        self._refresh_all_views()
        self._log("stash", f"Stash pop @{{{index}}} succeeded")
        return True

    def stash_apply(self, index: int = 0) -> bool:
        """Apply the stash at ``index`` without dropping it via :class:`StashApplyCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("stash", f"Stash apply @{index} failed: no repository", level="error")
            return False
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return False
        from src.viewmodels.commands import StashApplyCommand

        self._log("stash", f"Stash apply @{{{index}}}")
        command = StashApplyCommand(self._repo_manager, index)
        try:
            self._command_processor.execute(command)
        except MergeConflictError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", "Stash apply produced conflicts", level="warn")
            self._refresh_all_views()
            return False
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", f"Stash apply failed: {exc}", level="error")
            return False
        self._refresh_all_views()
        self._log("stash", f"Stash apply @{{{index}}} succeeded")
        return True

    def stash_drop(self, index: int = 0) -> bool:
        """Drop the stash at ``index`` via :class:`StashDropCommand`."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("stash", f"Stash drop @{index} failed: no repository", level="error")
            return False
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return False
        from src.viewmodels.commands import StashDropCommand

        self._log("stash", f"Stash drop @{{{index}}}")
        command = StashDropCommand(self._repo_manager, index)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", f"Stash drop failed: {exc}", level="error")
            return False
        self._refresh_all_views()
        self._log("stash", f"Stash drop @{{{index}}} succeeded")
        return True

    def stash_push_staged(self, message: str = "WIP staged") -> bool:
        """Stash only the *staged* changes via :class:`StashPushStagedCommand`.

        A successful no-op (returns ``True``) when there are no staged
        files; the command is still pushed onto the undo stack so the
        user can see the attempt in the history. The undo path is
        safe — when no stash was actually created, undo is a no-op.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("stash", "Stash staged failed: no repository", level="error")
            return False
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return False
        from src.viewmodels.commands import StashPushStagedCommand

        self._log("stash", f"Stash staged — message: {message[:60]!r}")
        command = StashPushStagedCommand(self._repo_manager, message)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", f"Stash staged failed: {exc}", level="error")
            return False
        self._refresh_all_views()
        self._log("stash", "Stash staged succeeded")
        return True

    def is_stash_sha(self, sha: str) -> bool:
        """Return ``True`` if ``sha`` corresponds to a stash entry."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            return False
        stash_list = self._repo_manager.stash_list
        return any(s.sha == sha for s in stash_list)

    @_guard_mutation
    def apply_stash_file(self, stash_sha: str, path: str) -> None:
        """Apply a single file from the stash identified by ``stash_sha``.

        Reads the file's content from the stash commit and writes it to
        the working tree. The file is also staged so the user can review
        and commit it.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.core.operations import apply_file_from_stash

        self._log("stash", f"Apply file {path!r} from stash {stash_sha[:8]!r}")
        try:
            apply_file_from_stash(self._repo_manager, stash_sha, path)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("stash", f"Apply stash file {path!r} failed: {exc}", level="error")
            return
        self._commit_panel_view_model.refresh_status()
        self._log("stash", f"Applied {path!r} from stash {stash_sha[:8]!r}")

    @_guard_mutation
    def apply_stash_files(self, stash_sha: str, paths: list[str]) -> None:
        """Apply several files from the stash in one batch.

        Multi-file counterpart of :meth:`apply_stash_file`. Each path is
        applied in turn; the first failure surfaces through
        :attr:`error_occurred` and the loop stops so the user can react
        before the working tree is half-overwritten. The status is
        refreshed once at the end (instead of once per file) so a
        large batch does not stall the UI.

        The right-click *Apply N stashed files* action in the
        commit-detail panel routes through this verb.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        if not paths:
            return
        from src.core.operations import apply_file_from_stash

        self._log(
            "stash",
            f"Apply {len(paths)} file(s) from stash {stash_sha[:8]!r}: "
            f"{', '.join(paths[:6])}"
            + ("…" if len(paths) > 6 else ""),
        )
        applied: list[str] = []
        for path in paths:
            try:
                apply_file_from_stash(self._repo_manager, stash_sha, path)
            except GitError as exc:
                self.error_occurred.emit(str(exc))
                self._log(
                    "stash",
                    f"Apply stash file {path!r} failed after "
                    f"{len(applied)} successful apply(ies): {exc}",
                    level="error",
                )
                return
            applied.append(path)
        self._commit_panel_view_model.refresh_status()
        self._log(
            "stash",
            f"Applied {len(applied)} file(s) from stash {stash_sha[:8]!r}",
        )

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
            self._log("push", f"Push to {remote_name!r} failed: no repository", level="error")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import PushCommand

        spec = refspec or "HEAD"
        self._log("push", f"Push {remote_name}/{spec}")
        command = PushCommand(self._repo_manager, remote_name, refspec)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
                log_tag="push",
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
            self._log("pull", f"Pull from {remote_name!r} failed: no repository", level="error")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import PullCommand

        spec = refspec or "HEAD"
        self._log("pull", f"Pull {remote_name}/{spec}")
        command = PullCommand(self._repo_manager, remote_name, refspec)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
                log_tag="pull",
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
            if not silent:
                self.error_occurred.emit("Another operation is in progress.")
            return
        from src.viewmodels.commands import FetchCommand

        spec = refspec or "all"
        if not silent:
            self._log("fetch", f"Fetch {remote_name}/{spec}")
        command = FetchCommand(self._repo_manager, remote_name, refspec)
        if self._async_enabled:
            self._run_async(
                command,
                on_success=lambda: self._refresh_all_views(),
                silent_on_failure=silent,
                log_tag="fetch" if not silent else "",
            )
            return
        self._execute_fetch_sync(command, silent)

    def add_remote(self, name: str, url: str) -> None:
        """Add a remote via :class:`AddRemoteCommand` (sync — fast op)."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import AddRemoteCommand

        self._log("remote", f"Add remote {name!r} → {url}")
        command = AddRemoteCommand(self._repo_manager, name, url)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("remote", f"Add remote {name!r} failed: {exc}", level="error")
            return
        self._branch_panel_view_model.refresh()
        self._log("remote", f"Remote {name!r} added")

    def remove_remote(self, name: str) -> None:
        """Remove a remote via :class:`RemoveRemoteCommand` (sync)."""
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            return
        from src.viewmodels.commands import RemoveRemoteCommand

        self._log("remote", f"Remove remote {name!r}")
        command = RemoveRemoteCommand(self._repo_manager, name)
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("remote", f"Remove remote {name!r} failed: {exc}", level="error")
            return
        self._branch_panel_view_model.refresh()
        self._log("remote", f"Remote {name!r} removed")

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

        R2.2 C7 — the captured generation token bumps on every
        :meth:`set_repository`; if the user opens a *different*
        repository while the clone is in flight, the worker's late
        result is dropped silently instead of overwriting the new
        VM state.
        """
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        self._log("clone", f"Cloning {url} → {path}")
        if not self._async_enabled:
            self._execute_clone_sync(url, path)
            return
        # Capture generation so a late-arriving success/failure does
        # not bleed into the VM after the user opened a different
        # repo (R2.2 C7).
        generation = self._async_generation
        self._is_busy = True
        self.busy_changed.emit(True)

        def _work() -> None:
            manager = RepositoryManager()
            manager.clone(url, path)

        def _on_success(_: object) -> None:
            if generation != self._async_generation:
                # Stale — drop silently.
                return
            self._is_busy = False
            self.busy_changed.emit(False)
            try:
                manager = RepositoryManager(path)
            except (RepositoryNotFoundError, GitError) as exc:
                self.error_occurred.emit(str(exc))
                self._log("clone", f"Clone succeeded but open failed: {exc}", level="error")
                return
            # The async worker has already returned; pass ``force=True``
            # so the (defensive) busy-guard does not refuse our own
            # success-handler call site.
            self.set_repository(manager, force=True)
            self._log("clone", f"Clone finished: {url} → {path}")

        def _on_failure(exc: object) -> None:
            if generation != self._async_generation:
                # Stale — drop silently.
                return
            self._is_busy = False
            self.busy_changed.emit(False)
            self.error_occurred.emit(str(exc))
            self._log("clone", f"Clone failed: {exc}", level="error")

        worker = AsyncWorker(_work)
        worker.signals.finished.connect(_on_success)
        worker.signals.failed.connect(_on_failure)
        self._active_workers.add(worker)
        worker.signals.lifespan_finished.connect(
            lambda w=worker: self._on_async_finished(w),
        )
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
        """Auto-fetch callback: silent fetch of ``origin`` (errors logged).

        Skipped when the working tree has uncommitted changes — a fetch
        into a dirty tree is pointless (the user cannot see the result
        until they commit or stash) and on large repos the status check
        may be slow enough to cause a UI hiccup.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            return
        if self._is_busy:
            return
        try:
            if self._repo_manager.repo.status():
                return  # working tree is dirty — skip auto-fetch
        except Exception:
            pass
        self.fetch_changes("origin", silent=True)

    @_guard_mutation
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

        self._log("conflict", f"Resolving conflict in {path!r}")

        try:
            full_path = Path(self._repo_manager.path) / path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(resolution, encoding="utf-8")
            self._repo_manager.repo.index.add(path)
            self._repo_manager.repo.index.write()
        except OSError as exc:
            self.error_occurred.emit(f"Failed to resolve {path!r}: {exc}")
            self._log("conflict", f"Failed to write resolution for {path!r}: {exc}", level="error")
            return

        # Drop the resolved path from the conflict list.
        paths = list(self._conflict_state.get("conflicting_paths", []))
        if path in paths:
            paths.remove(path)
        if paths:
            self._conflict_state["conflicting_paths"] = paths
            self.conflict_state_changed.emit(dict(self._conflict_state))
            self._log("conflict", f"Still {len(paths)} conflicting file(s) remaining")
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
                self._log("merge", f"Complete merge failed: {exc}", level="error")
                return
            self._clear_conflict_state()
            self._refresh_all_views()
            self._log("merge", "Merge completed after conflict resolution")
            return
        if operation == "rebase":
            try:
                more = complete_rebase_continue(self._repo_manager)
            except GitError as exc:
                self.error_occurred.emit(str(exc))
                self._log("rebase", f"Rebase continue failed: {exc}", level="error")
                return
            if more or is_rebase_in_progress(self._repo_manager):
                from src.core.operations import _collect_conflicts
                from src.core.repository import unwrap

                with unwrap(self._repo_manager) as r:
                    paths = _collect_conflicts(r)
                self._conflict_state["conflicting_paths"] = paths
                self.conflict_state_changed.emit(dict(self._conflict_state))
                self._log("rebase", "More conflicts — continuing rebase")
                return
            self._clear_conflict_state()
            self._refresh_all_views()
            self._log("rebase", "Rebase completed after conflict resolution")
            return
        if operation in ("cherry-pick", "revert"):
            self._clear_conflict_state()
            self._commit_panel_view_model.refresh_status()
            self._log(operation, f"{operation} conflict resolved — staged, ready for commit")
            return
        self._clear_conflict_state()
        self._refresh_all_views()

    @_guard_mutation
    def complete_merge_after_conflict(self, source: str, parent_oid: str | None = None) -> None:
        """Finalise a resolved merge via :class:`CompleteMergeCommand`.

        Constructs and executes :class:`CompleteMergeCommand`, which
        calls :func:`src.core.operations.complete_merge` to write the
        merge commit. The command captures ``parent_oid`` so undo
        can hard-reset HEAD and the worktree back to the pre-merge
        SHA — making the conflict-resolution flow reversible through
        the standard toolbar Undo.

        ``parent_oid`` defaults to ``self._repo_manager.repo.head.target``
        captured at call time (the pre-merge HEAD). The caller can
        override it (e.g. when the user reopens the dialog and the
        VM has already moved HEAD elsewhere) — passing an explicit
        value keeps the undo path anchored to the SHA the merge
        actually replaced.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("merge", "Complete merge failed: no repository open", level="error")
            return
        if parent_oid is None:
            parent_oid = str(self._repo_manager.repo.head.target)
        from src.viewmodels.commands import CompleteMergeCommand

        self._log("merge", f"Complete merge (source={source!r}, parent={parent_oid[:7]})")
        command = CompleteMergeCommand(
            self._repo_manager,
            source=source,
            parent_oid=parent_oid,
        )
        try:
            self._command_processor.execute(command)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._log("merge", f"Complete merge failed: {exc}", level="error")
            return
        self._clear_conflict_state()
        self._refresh_all_views()
        self._log("merge", "Complete merge succeeded")

    def conflict_state(self) -> dict | None:
        """Return a copy of the current conflict state, or ``None``."""
        return None if self._conflict_state is None else dict(self._conflict_state)

    def is_busy(self) -> bool:
        """Return ``True`` while a long-running async operation is in progress."""
        return self._is_busy

    # ----- internals ---------------------------------------------------

    def _log(self, category: str, message: str, level: str = "info") -> None:
        """Emit a timestamped log line via :attr:`log_message`."""
        import datetime

        ts = datetime.datetime.now().strftime("%H:%M:%S")
        level_tag = {"info": "INFO ", "warn": "WARN ", "error": "ERROR"}.get(level, "INFO ")
        prefix = f"[{category}]" if category else ""
        self.log_message.emit(f"{ts} {level_tag}{prefix} {message}")

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
        log_tag: str = "",
    ) -> None:
        """Run ``command.execute()`` on a worker thread.

        The work is wrapped in an :class:`AsyncWorker`; the ``finished``
        signal triggers ``on_success`` on the UI thread, the ``failed``
        signal routes the exception (passed as the actual exception
        **object**, not a string) through the normal VM error /
        conflict paths, and the ``lifespan_finished`` signal clears
        the busy flag and drops the strong :class:`AsyncWorker`
        reference.

        ``silent_on_failure=True`` suppresses the ``error_occurred``
        signal for generic :class:`GitError` failures. Conflict state
        is still surfaced because the user must resolve it. The
        auto-fetch timer uses silent mode so a dropped connection
        does not flash a status-bar error every minute.

        ``log_tag`` is used to emit success/failure log entries
        (e.g. ``"fetch"``, ``"push"``).

        R2.2 notes
        ----------
        * The current dispatch passes the UI-thread command
          directly to the worker.  ``command._repo`` is therefore the
          UI-thread :class:`RepositoryManager`; a true C6 fix would
          require reconstructing the command with a worker-owned
          manager (out of scope for this stage).  The busy-guard on
          :meth:`set_repository` (and the long-standing busy-guard
          on ``refresh_state`` and the verb verbs) are the
          operational mitigation that prevents the UI thread from
          entering the same ``pygit2.Repository`` while the worker
          is in flight.
        * The captured ``generation`` token drops stale results
          when ``set_repository`` runs between worker dispatch and
          completion (R2.2 C7).
        """
        if self._is_busy:
            return
        self._is_busy = True
        self.busy_changed.emit(True)
        # Capture the generation token at dispatch time (R2.2 C7).
        generation = self._async_generation

        def _work() -> None:
            self._command_processor.execute(command)  # type: ignore[arg-type]

        def _on_result(_: object) -> None:
            if generation != self._async_generation:
                # Stale — the user opened a different repo while the
                # worker was in flight.  Drop silently (R2.2 C7/M8).
                return
            if log_tag:
                self._log(log_tag, "Operation succeeded")
            on_success()  # type: ignore[operator]

        def _on_failure(exc: object) -> None:
            if generation != self._async_generation:
                # Stale — drop silently.
                return
            self._on_async_failed(
                command, exc, silent_on_failure, log_tag=log_tag,
            )

        worker = AsyncWorker(_work)
        worker.signals.finished.connect(_on_result)
        worker.signals.failed.connect(_on_failure)
        self._active_workers.add(worker)
        worker.signals.lifespan_finished.connect(
            lambda w=worker: self._on_async_finished(w),
        )
        QThreadPool.globalInstance().start(worker)

    def _on_async_failed(
        self,
        command: object,
        exc: object,
        silent: bool = False,
        *,
        log_tag: str = "",
    ) -> None:
        """Map a worker exception back into the VM's error/conflict paths.

        ``exc`` is the actual exception object raised inside the worker
        — the previous implementation received a pre-formatted string
        and re-detected the type with :func:`is_merge_in_progress`,
        which raced with the async worker that just finished
        modifying state.  Routing on the actual exception type
        (R2.2) is both faster and race-free.
        """
        message = str(exc)
        # Domain exceptions we surface with a dedicated path.
        from src.core.exceptions import MergeConflictError, RebaseConflictError

        if isinstance(exc, MergeConflictError):
            if log_tag:
                self._log(log_tag, f"Operation failed (conflicts): {message}", level="warn")
            if self._repo_manager is not None:
                self._set_conflict_state(
                    "merge",
                    conflicting_paths=exc.conflicting_paths,
                    source=None,
                    target=None,
                )
            elif not silent:
                self.error_occurred.emit(message)
            return
        if isinstance(exc, RebaseConflictError):
            if log_tag:
                self._log(log_tag, f"Operation failed (rebase conflicts): {message}", level="warn")
            if self._repo_manager is not None:
                self._set_conflict_state("rebase", conflicting_paths=[], upstream=None)
            if not silent:
                self.error_occurred.emit(message)
            return

        if log_tag:
            self._log(log_tag, f"Operation failed: {message}", level="error")
        if self._repo_manager is None:
            if not silent:
                self.error_occurred.emit(message)
            return
        # Fall-through: still consult ``is_merge_in_progress`` to
        # handle the case where the worker raised a non-domain
        # exception after the merge was initiated (e.g. a plain
        # ``RuntimeError`` from pygit2).  This is the only path that
        # still needs a heuristic.
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

    def _on_repo_load_finished(self) -> None:
        """Called on the UI thread after the background repo data load succeeds."""
        self._is_busy = False
        self.busy_changed.emit(False)
        self._log("repo", "Repository data loaded")
        # Drain any events queued by the worker's signal emissions so
        # the graph / side panels render without waiting for the next
        # event-loop iteration.
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)

    def _on_repo_load_failed(self, message: str) -> None:
        """Called on the UI thread when the background repo data load raises."""
        self._is_busy = False
        self.busy_changed.emit(False)
        self.error_occurred.emit(f"Failed to load repository data: {message}")
        self._log("repo", f"Repository data load failed: {message}", level="error")

    def _on_async_finished(self, worker: object) -> None:
        self._active_workers.discard(worker)
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
        n = len(conflicting_paths)
        self._log(operation, f"Entered conflict state — {n} conflicting file(s)")
        self.conflict_state_changed.emit(dict(self._conflict_state))

    def _clear_conflict_state(self) -> None:
        """Leave the conflict state and notify listeners."""
        if self._conflict_state is None:
            return
        op = self._conflict_state.get("operation", "unknown")
        self._conflict_state = None
        self._log(op, "Conflict state cleared")
        self.conflict_state_changed.emit(
            {
                "in_progress": False,
                "conflicting_paths": [],
                "operation": None,
            },
        )


__all__ = ["MainViewModel"]
