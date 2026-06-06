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
    log_message = Signal(str)  # human-readable timestamped log line
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

    def set_repository(self, manager: RepositoryManager | None) -> None:
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
        """
        self._repo_manager = manager
        self._command_processor.clear()
        self._clear_conflict_state()
        self._graph_view_model.set_repository(manager)
        self._commit_panel_view_model.set_repository(manager)
        self._branch_panel_view_model.set_repository(manager)
        self._update_auto_fetch_timer()
        if self._selected_commit_sha is not None:
            self._selected_commit_sha = None
            self.selection_changed.emit(None)
        self.repository_changed.emit(manager.path if manager is not None else None)

    # ----- verb commands ----------------------------------------------

    def commit_changes(self, message: str) -> None:
        """Create a new commit on ``HEAD`` via :class:`CommitCommand`.

        On success the graph and commit panel are refreshed, the
        commit message is cleared, and the new commit is auto-selected
        so the right panel switches from the commit-input view to the
        commit-detail view of the freshly-created commit.

        On failure the error is surfaced via :attr:`error_occurred` and
        the undo stack is unchanged (the failed command is never
        pushed). The selection is left untouched.
        """
        if self._repo_manager is None or not self._repo_manager.is_open:
            self.error_occurred.emit("No repository open.")
            self._log("commit", "Commit failed: no repository open", level="error")
            return
        from src.viewmodels.commands import CommitCommand  # local import: avoids cycle

        self._log("commit", f"Committing staged changes — message: {message[:80]}")
        command = CommitCommand(self._repo_manager, message)
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

    def stage_all_unstaged(self) -> None:
        """Stage every currently-unstaged file in one call.

        Used by the right panel's *Stage All Changes* button. Errors
        from individual ``stage_file`` calls are routed through
        :attr:`error_occurred` (the VM's normal error path) so a
        single bad file does not abort the rest of the batch.
        """
        unstaged = self._commit_panel_view_model.unstaged_paths()
        for path in unstaged:
            self._commit_panel_view_model.stage_file(path)

    def unstage_all_staged(self) -> None:
        """Unstage every currently-staged file in one call.

        Used by the right panel's red *Unstage All Changes* button.
        Each file is reset via :meth:`CommitPanelViewModel.unstage_file`;
        individual errors are surfaced through :attr:`error_occurred`
        but do not abort the batch.
        """
        staged = self._commit_panel_view_model.staged_files()
        for path in staged:
            self._commit_panel_view_model.unstage_file(path)

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

        No-op when no repository is open (the panels already reflect
        the empty state) and when a long-running async operation is
        in flight (refreshing during a rebase / merge would race
        with the worker thread). The latter is the same re-entrancy
        guard the toolbar buttons rely on via :attr:`busy_changed`.

        :class:`GitError` from any of the child ViewModels is already
        routed through :attr:`error_occurred` by the children
        themselves, so it never reaches this method. A non-``GitError``
        (e.g. an :class:`OSError` when ``.git/`` was removed while
        the window was inactive) is caught here and surfaced the same
        way; the VM state is left unchanged so a subsequent valid
        refresh can still succeed.
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

    def undo(self) -> None:
        """Undo the most recent command; refreshes views on success."""
        if not self._command_processor.can_undo:
            return
        try:
            self._command_processor.undo()
        except GitError as exc:
            self.error_occurred.emit(f"Undo failed: {exc}")
            self._log("undo", f"Undo failed: {exc}", level="error")
            return
        self._refresh_all_views()
        self._log("undo", "Undo succeeded")

    def redo(self) -> None:
        """Redo the most recently undone command; refreshes views on success."""
        if not self._command_processor.can_redo:
            return
        try:
            self._command_processor.redo()
        except GitError as exc:
            self.error_occurred.emit(f"Redo failed: {exc}")
            self._log("redo", f"Redo failed: {exc}", level="error")
            return
        self._refresh_all_views()
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
        self._refresh_all_views()

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
        self._log("branch", f"Branch {name!r} created at {target_sha[:7]}")
        return True

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
            self._log("merge", f"Merge {source!r} failed: no repository", level="error")
            return
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        from src.viewmodels.commands import MergeCommand

        self._log(
            "merge",
            f"Merge {source!r}"
            + (f" into {target!r}" if target else " into current"),
        )
        command = MergeCommand(self._repo_manager, source, target=target)
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
        """
        if self._is_busy:
            self.error_occurred.emit("Another operation is already in progress.")
            return
        self._log("clone", f"Cloning {url} → {path}")
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
                self._log("clone", f"Clone succeeded but open failed: {exc}", level="error")
                return
            self.set_repository(manager)
            self._log("clone", f"Clone finished: {url} → {path}")

        def _on_failure(message: str) -> None:
            self._is_busy = False
            self.busy_changed.emit(False)
            self.error_occurred.emit(message)
            self._log("clone", f"Clone failed: {message}", level="error")

        worker = AsyncWorker(_work)
        worker.signals.result.connect(_on_success)
        worker.signals.failed.connect(_on_failure)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda: self._on_async_finished(worker),
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

        The work is wrapped in an :class:`AsyncWorker`; the result
        signal triggers ``on_success`` on the UI thread, the failed
        signal routes the exception through the normal VM error /
        conflict paths, and the finished signal clears the busy flag.

        ``silent_on_failure=True`` suppresses the ``error_occurred``
        signal for generic :class:`GitError` failures. Conflict state
        is still surfaced because the user must resolve it. The
        auto-fetch timer uses silent mode so a dropped connection
        does not flash a status-bar error every minute.

        ``log_tag`` is used to emit success/failure log entries
        (e.g. ``"fetch"``, ``"push"``).
        """
        if self._is_busy:
            return
        self._is_busy = True
        self.busy_changed.emit(True)

        def _work() -> None:
            self._command_processor.execute(command)  # type: ignore[arg-type]

        def _on_result(_: object) -> None:
            if log_tag:
                self._log(log_tag, "Operation succeeded")
            on_success()  # type: ignore[operator]

        worker = AsyncWorker(_work)
        worker.signals.result.connect(_on_result)
        worker.signals.failed.connect(
            lambda message: self._on_async_failed(
                command, message, silent_on_failure, log_tag=log_tag,
            ),
        )
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda: self._on_async_finished(worker),
        )
        QThreadPool.globalInstance().start(worker)

    def _on_async_failed(
        self,
        command: object,
        message: str,
        silent: bool = False,
        *,
        log_tag: str = "",
    ) -> None:
        """Map a worker exception back into the VM's error/conflict paths."""
        if log_tag:
            self._log(log_tag, f"Operation failed: {message}", level="error")
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
