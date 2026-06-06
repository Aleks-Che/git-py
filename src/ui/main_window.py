"""Main application window: menu, panels, status bar.

Stage 3 wires the central :class:`MainViewModel` into the window and
adds the WIP / commit panel to the right side. Stage 4 swaps the
left-panel stub for the real references tree
(:class:`LeftPanel`). Stage 5 adds a re-entrancy guard + spinner for
long-running operations (rebase, large merge). Stage 6 adds a
``Remote`` toolbar with Push / Pull / Fetch, a ``File > Clone…``
dialog, and a ``Remote > Manage Remotes…`` dialog. Stage 9 adds
persistence for window size and splitter positions.

Layout:

* **Top:** :class:`QToolBar` with Fetch / Pull / Push (Stage 6+).
* **Left:** :class:`LeftPanel` — branches / tags / stash tree.
* **Centre:** :class:`GraphWidget` (Stage 2).
* **Right:** :class:`RightPanel` — hidden until the user picks a
  commit or the WIP node in the graph. When shown it shows one of
  two views (commit-input or commit-detail) selected by
  :attr:`MainViewModel.selection_changed`.
* **Bottom:** :class:`TerminalWidget` (Stage 0 stub; real shell in Stage 7)

The :class:`MainViewModel` owns the :class:`RepositoryManager` and the
:class:`CommandProcessor`; widgets either receive their VM as a
constructor argument (preferred) or look it up via
:meth:`MainWindow.main_view_model`. The toolbar Undo / Redo actions
bind to :meth:`MainViewModel.undo` / :meth:`MainViewModel.redo` and
their enabled state tracks the processor's ``stack_changed`` signal.

Per ``docs/DEVELOPMENT_RULES.md`` section 3, long-running operations
(rebase, large merges, push, pull, fetch, clone) are routed through
:class:`AsyncWorker`. While such an operation is in flight
:attr:`MainViewModel.busy_changed` fires; the window disables mutating
toolbar actions and shows a spinner in the status bar.

Stage 9 persistence
-------------------
The constructor accepts an optional ``config_path``. When provided,
the window restores its size and splitter positions from the JSON
file on construction and writes them back on
:meth:`closeEvent`. When ``config_path`` is ``None`` (the default,
used by all existing tests) persistence is disabled — no file is
read or written, so tests do not pollute the user's real config.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QTabWidget,
    QToolBar,
)

from src.core.exceptions import GitError, RepositoryNotFoundError
from src.core.repository import RepositoryManager
from src.ui.dialogs.clone_dialog import CloneDialog
from src.ui.dialogs.open_or_clone_dialog import OpenOrCloneDialog
from src.ui.dialogs.remote_manage_dialog import RemoteManageDialog
from src.ui.widgets.diff_view_widget import DiffViewWidget
from src.ui.widgets.graph_panel import GraphTableWidget
from src.ui.widgets.left_panel import LeftPanel
from src.ui.widgets.log_widget import LogWidget
from src.ui.widgets.repo_bar_widget import RepoBarWidget
from src.ui.widgets.right_panel import RightPanel
from src.ui.widgets.terminal_widget import TerminalWidget
from src.utils.config import (
    SPLITTER_KEY_HORIZONTAL,
    load_config,
    load_graph_column_widths,
    load_splitter_sizes,
    load_window_size,
    save_config,
    save_graph_column_widths,
)
from src.viewmodels.main_viewmodel import MainViewModel
from src.viewmodels.repo_tabs_viewmodel import RepoTabViewModel


class MainWindow(QMainWindow):
    """Top-level window: graph + right panel over a terminal stub."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("git-py")
        # Default size is overridden by :meth:`_restore_state` when
        # ``config_path`` is provided. We still call ``resize`` here
        # so callers that disable persistence (passing ``None``) see
        # the same initial size as before Stage 9.
        self.resize(1280, 800)

        # ``async_enabled=True`` enables the long-running path for
        # rebase and large merges (see ``MainViewModel.busy_changed``).
        self._main_vm = MainViewModel(self, async_enabled=True)
        self._repo_manager: RepositoryManager | None = None
        # ``config_path`` is the JSON file used for window /
        # splitter persistence. ``None`` disables persistence
        # entirely; the field is read by :meth:`_restore_state` and
        # :meth:`closeEvent`.
        self._config_path: Path | None = (
            Path(config_path) if config_path is not None else None
        )

        # The top horizontal splitter (left | graph | right) is kept
        # on ``self`` so the persistence layer can read / write its
        # sizes in :meth:`_restore_state` and :meth:`closeEvent`. It
        # is populated by :meth:`_build_central`.
        #
        # Note: the right_vertical splitter that lived in this
        # position through Stage 5 is gone — the right panel is now
        # a single :class:`RightPanel` whose sub-views are stacked
        # internally; the user no longer resizes between them. The
        # persisted config key ``SPLITTER_KEY_RIGHT_VERTICAL`` is
        # therefore ignored (it is no longer written), so older
        # configs simply fall back to whatever Qt gives the new
        # layout by default.
        self._top_splitter: QSplitter | None = None
        # Cache of the last splitter sizes recorded while the left
        # panel was visible. While the diff view is open the left
        # panel is hidden, which collapses its column to zero — if
        # we wrote those zeroed-out sizes to the config the user's
        # normal layout would be lost. ``closeEvent`` consults this
        # cache when the left panel is hidden at close time.
        self._last_normal_splitter_sizes: list[int] | None = None

        self._build_menu()
        self._build_repo_bar()
        # Force the remote toolbar to sit on the row below the tab bar.
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self._build_toolbar()
        self._build_central()
        self._build_status_bar()
        self._main_vm.busy_changed.connect(self._on_busy_changed)
        # Refresh the repository from disk when the application
        # becomes active (window restored from minimised, switched
        # back from another app, etc.) so commits / branch changes
        # made in another Git client show up immediately. We listen
        # at the ``QApplication`` level rather than per-window so a
        # future secondary window (Settings, Clone…) does not
        # double-trigger the refresh. The hook is a no-op when no
        # repository is open or an async op is running — see
        # :meth:`MainViewModel.refresh_state`.
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_app_state_changed)
        # ``_restore_state`` runs *after* the central widget is built
        # because :meth:`setSizes` needs the children to be parented
        # and laid out once.
        self._restore_state()

    # ----- public API (also used by tests) -----------------------------

    def set_repository(self, manager: RepositoryManager | None) -> None:
        """Bind an open repository to the UI.

        Thin shim over :meth:`MainViewModel.set_repository` kept for
        Stage 2 tests (``test_graph_widget.py::test_main_window_wires_graph_view_model``).
        """
        self._repo_manager = manager
        self._main_vm.set_repository(manager)
        if manager is not None:
            self._status.showMessage(f"Repository: {manager.path}")
        else:
            self._status.showMessage("No repository")
        self._action_close.setEnabled(manager is not None)

    def graph_view_model(self) -> object:
        """Expose the graph ViewModel for Stage 2 test wiring."""
        return self._main_vm.graph_view_model()

    def main_view_model(self) -> MainViewModel:
        """Return the central :class:`MainViewModel`."""
        return self._main_vm

    def repo_tabs_view_model(self) -> RepoTabViewModel:
        """Return the :class:`RepoTabViewModel` driving the tab bar."""
        return self._repo_tabs_vm

    # ----- repo tab bar ------------------------------------------------

    def _build_repo_bar(self) -> None:
        """Build the repository tab bar between menu and remote toolbar."""
        self._repo_tabs_vm = RepoTabViewModel(self)
        self._repo_bar = RepoBarWidget(self._repo_tabs_vm, self)
        toolbar = QToolBar("Repositories", self)
        toolbar.setObjectName("repo-tab-toolbar")
        toolbar.setMovable(False)
        toolbar.addWidget(self._repo_bar)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self._repo_bar.add_requested.connect(self._on_add_repository)
        self._repo_tabs_vm.active_tab_changed.connect(self._on_tab_changed)

    def _on_add_repository(self) -> None:
        """Show ``OpenOrCloneDialog`` when the ``+`` tab is clicked."""
        dialog = OpenOrCloneDialog(self)
        choice = dialog.choice()
        if choice == "open":
            self._open_repository_dialog()
        elif choice == "clone":
            self._open_clone_dialog()

    def _on_tab_changed(self, index: int) -> None:
        """Switch the active repository to the tab at *index*."""
        path = self._repo_tabs_vm.active_path
        if path is None:
            self.set_repository(None)
            return
        current = self._main_vm.repository_manager()
        if current is not None and current.path is not None:
            if _same_path(current.path, path):
                return
        manager = RepositoryManager()
        try:
            manager.open(path)
        except (RepositoryNotFoundError, GitError) as exc:
            self._on_error(str(exc))
            return
        self.set_repository(manager)

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action_open = QAction("&Open Repository…", self)
        self._action_open.setShortcut(QKeySequence.StandardKey.Open)
        self._action_open.triggered.connect(self._open_repository_dialog)
        file_menu.addAction(self._action_open)

        self._action_close = QAction("&Close Repository", self)
        self._action_close.setEnabled(False)
        self._action_close.triggered.connect(self._close_current_tab)
        file_menu.addAction(self._action_close)

        self._action_clone = QAction("&Clone…", self)
        self._action_clone.triggered.connect(self._open_clone_dialog)
        file_menu.addAction(self._action_clone)

        self._action_init = QAction("&Init New Repository…", self)
        self._action_init.triggered.connect(self._open_init_dialog)
        file_menu.addAction(self._action_init)

        file_menu.addSeparator()
        action_exit = QAction("E&xit", self)
        action_exit.setShortcut(QKeySequence.StandardKey.Quit)
        action_exit.triggered.connect(self.close)
        file_menu.addAction(action_exit)

        edit_menu = bar.addMenu("&Edit")
        self._action_undo = QAction("&Undo", self)
        self._action_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self._action_undo.setEnabled(False)
        self._action_undo.triggered.connect(self._main_vm.undo)
        edit_menu.addAction(self._action_undo)

        self._action_redo = QAction("&Redo", self)
        self._action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self._action_redo.setEnabled(False)
        self._action_redo.triggered.connect(self._main_vm.redo)
        edit_menu.addAction(self._action_redo)

        remote_menu = bar.addMenu("&Remote")
        self._action_fetch = QAction("&Fetch from origin", self)
        self._action_fetch.setShortcut("Ctrl+Shift+F")
        self._action_fetch.setEnabled(False)
        self._action_fetch.triggered.connect(lambda: self._main_vm.fetch_changes("origin"))
        remote_menu.addAction(self._action_fetch)

        self._action_pull = QAction("&Pull from origin", self)
        self._action_pull.setShortcut("Ctrl+Shift+P")
        self._action_pull.setEnabled(False)
        self._action_pull.triggered.connect(lambda: self._main_vm.pull_changes("origin"))
        remote_menu.addAction(self._action_pull)

        self._action_push = QAction("&Push to origin", self)
        self._action_push.setShortcut("Ctrl+Shift+U")
        self._action_push.setEnabled(False)
        self._action_push.triggered.connect(lambda: self._main_vm.push_changes("origin"))
        remote_menu.addAction(self._action_push)

        remote_menu.addSeparator()
        self._action_manage_remotes = QAction("&Manage Remotes…", self)
        self._action_manage_remotes.setEnabled(False)
        self._action_manage_remotes.triggered.connect(self._open_remote_manage_dialog)
        remote_menu.addAction(self._action_manage_remotes)

        view_menu = bar.addMenu("&View")
        for label in ("Left Panel", "Commit Detail Panel", "Terminal"):
            view_menu.addAction(QAction(label, self, checkable=True, checked=True))

        help_menu = bar.addMenu("&Help")
        action_about = QAction("&About git-py", self)
        action_about.triggered.connect(self._show_about)
        help_menu.addAction(action_about)

        # Keep Undo / Redo enabled state in sync with the command
        # processor. ``set_repository`` clears both stacks, so this
        # also fires on repo open / close.
        self._main_vm.command_processor().stack_changed.connect(
            self._update_undo_redo_actions,
        )

    def _build_toolbar(self) -> None:
        """Add the Push / Pull / Fetch toolbar (Stage 6)."""
        toolbar = QToolBar("Remote", self)
        toolbar.setObjectName("remote-toolbar")
        toolbar.setMovable(False)
        # Use the same actions as the Remote menu so the enabled
        # state stays in sync (one source of truth).
        toolbar.addAction(self._action_fetch)
        toolbar.addAction(self._action_pull)
        toolbar.addAction(self._action_push)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        self._remote_toolbar = toolbar

    def _update_undo_redo_actions(self) -> None:
        proc = self._main_vm.command_processor()
        self._action_undo.setEnabled(proc.can_undo)
        self._action_redo.setEnabled(proc.can_redo)

    def _build_central(self) -> None:
        self._left_panel = LeftPanel(
            self._main_vm.branch_panel_view_model(),
            self._main_vm,
        )
        self._graph_table = GraphTableWidget(self._main_vm.graph_view_model())
        self._right_panel = RightPanel(self._main_vm)

        # Wire the graph's commit_selected signal to the central
        # VM's select_commit verb. The graph widget already updates
        # its own selection (the highlighted node) and the graph
        # view-model; we just need to make sure the VM tracks the
        # same SHA so the right panel can show it. The toggle-off
        # behaviour (click-same-commit-toggles-off) lives in
        # MainViewModel.select_commit.
        self._graph_table.commit_selected.connect(self._main_vm.select_commit)

        # When the VM clears the selection (toggle-off) the graph
        # widget must also remove its highlight ring.  The reverse
        # direction (VM → graph) for a *new* selection is redundant
        # (the graph already set it on click) but harmless.
        self._main_vm.selection_changed.connect(
            self._graph_table.set_selected_sha,
        )

        # Diff view shown in place of the graph when the user clicks
        # an unstaged file in the commit panel.
        self._diff_view = DiffViewWidget(self)
        # Hide diff view by default; shown when a file is selected.
        self._diff_view.setVisible(False)

        self._graph_stack = QStackedWidget(self)
        self._graph_stack.addWidget(self._graph_table)  # index 0
        self._graph_stack.addWidget(self._diff_view)     # index 1

        # Wire the commit panel VM's file selection signals to
        # switch between graph and diff view.
        cp_vm = self._main_vm.commit_panel_view_model()
        cp_vm.selected_file_changed.connect(self._on_selected_file_changed)
        cp_vm.diff_ready.connect(self._on_diff_ready)

        # The commit-detail panel emits the same signal pair so a
        # file click in either right-panel view swaps the graph for
        # a diff in this same stack.
        self._right_panel._commit_detail.selected_file_changed.connect(
            self._on_selected_file_changed,
        )
        self._right_panel._commit_detail.diff_ready.connect(self._on_diff_ready)

        top = QSplitter(self)
        top.addWidget(self._left_panel)
        top.addWidget(self._graph_stack)
        top.addWidget(self._right_panel)
        # Left panel stretch = 0 so it never grows/shrinks when the
        # right panel is hidden/shown — only the graph absorbs the
        # space change, keeping the left divider stable.
        top.setStretchFactor(0, 0)
        top.setStretchFactor(1, 5)
        top.setStretchFactor(2, 3)
        self._top_splitter = top
        # Track splitter drags while the left panel is in its normal
        # (visible) state so we can restore the user's layout if the
        # window is closed with the diff view open. See ``closeEvent``
        # for the consumer side.
        top.splitterMoved.connect(self._on_top_splitter_moved)

        self._terminal = TerminalWidget(self)
        self._terminal.setMaximumHeight(180)

        self._log_widget = LogWidget(self)
        self._log_widget.setMaximumHeight(180)

        self._bottom_tabs = QTabWidget(self)
        self._bottom_tabs.setMaximumHeight(200)
        self._bottom_tabs.addTab(self._terminal, "Terminal")
        self._bottom_tabs.addTab(self._log_widget, "Log")

        main_splitter = QSplitter(self)
        main_splitter.setOrientation(Qt.Orientation.Vertical)
        main_splitter.addWidget(top)
        main_splitter.addWidget(self._bottom_tabs)
        main_splitter.setStretchFactor(0, 5)
        main_splitter.setStretchFactor(1, 1)

        self.setCentralWidget(main_splitter)

    def _build_status_bar(self) -> None:
        self._status = QStatusBar(self)
        self._status.showMessage("No repository")
        # Indeterminate spinner shown while a long-running Git
        # operation (rebase, large merge) is running on a worker
        # thread. Hidden by default; ``_on_busy_changed`` toggles it.
        self._busy_spinner = QProgressBar(self)
        self._busy_spinner.setRange(0, 0)  # indeterminate marquee
        self._busy_spinner.setMaximumWidth(140)
        self._busy_spinner.setTextVisible(False)
        self._busy_spinner.hide()
        self._status.addPermanentWidget(self._busy_spinner)
        self.setStatusBar(self._status)

        self._main_vm.error_occurred.connect(self._on_error)
        self._main_vm.repository_changed.connect(self._on_repository_changed)
        self._main_vm.log_message.connect(self._log_widget.append_log)
        self._main_vm.error_occurred.connect(self._log_widget.append_log)

    # ----- diff view (replaces graph on file selection) ---------------

    def _on_selected_file_changed(self, path: str | None) -> None:
        """Switch between graph and diff view when a file is selected.

        The left panel (branches / tags / stash tree) is hidden while
        the diff is open so the diff gets the full width of the
        centre column, matching GitKraken. It reappears when the
        file is deselected.

        Before hiding we cache the current splitter sizes so that
        closing the window with the diff open does not overwrite
        the user's normal layout with sizes where the left panel
        has zero width.

        Hiding the left panel frees its column; without intervention
        the splitter would redistribute the freed space between the
        graph and the right panel in proportion to their stretch
        factors (5 : 3), so the right panel would visibly grow. To
        keep the right panel pinned at its previous width we
        redistribute all of the freed space to the graph via
        :meth:`QSplitter.setSizes`. The same widths are restored
        when the file is deselected.
        """
        if path is not None:
            if self._top_splitter is not None and self._left_panel.isVisible():
                self._last_normal_splitter_sizes = self._top_splitter.sizes()
            self._graph_stack.setCurrentIndex(1)
            self._diff_view.setVisible(True)
            self._left_panel.setVisible(False)
            if (
                self._top_splitter is not None
                and self._last_normal_splitter_sizes is not None
            ):
                saved = self._last_normal_splitter_sizes
                # Pin the right panel at its cached width; let the
                # graph absorb the left panel's old width.
                self._top_splitter.setSizes(
                    [0, saved[1] + saved[0], saved[2]],
                )
        else:
            self._graph_stack.setCurrentIndex(0)
            self._diff_view.setVisible(False)
            self._left_panel.setVisible(True)
            if (
                self._top_splitter is not None
                and self._last_normal_splitter_sizes is not None
            ):
                self._top_splitter.setSizes(self._last_normal_splitter_sizes)

    def _on_diff_ready(self, text: str) -> None:
        """Display the computed diff text in the diff view."""
        self._diff_view.set_diff(text)

    def _on_top_splitter_moved(self, _pos: int, _index: int) -> None:
        """Cache the current splitter sizes when the left panel is visible.

        The cache is used by :meth:`closeEvent` to avoid overwriting
        the saved layout with the zeroed-out sizes that the splitter
        reports while the left panel is hidden (i.e. while the diff
        view is open). We deliberately do nothing while the left
        panel is hidden — the cache must reflect a "normal" state.
        """
        if (
            self._top_splitter is not None
            and self._left_panel.isVisible()
        ):
            self._last_normal_splitter_sizes = self._top_splitter.sizes()

    # ----- state persistence (Stage 9) ---------------------------------

    def _restore_state(self) -> None:
        """Apply saved window size, splitter sizes, and repo tabs from config.

        No-op when :attr:`_config_path` is ``None`` — the constructor
        is the only place that calls this, and the absence of a path
        means persistence was explicitly disabled (e.g. in unit
        tests). The default window size from :meth:`__init__` is
        left untouched in that case.
        """
        if self._config_path is None:
            return
        config = load_config(self._config_path)
        width, height = load_window_size(config)
        self.resize(width, height)
        splitter_sizes = load_splitter_sizes(config)
        horizontal = splitter_sizes.get(SPLITTER_KEY_HORIZONTAL)
        if horizontal is not None and self._top_splitter is not None and len(horizontal) == 3:
            self._top_splitter.setSizes(horizontal)
        # Restore repository tabs.
        recent_repos = config.get("recent_repos", [])
        active_repo = config.get("active_repo")
        if recent_repos:
            self._repo_tabs_vm.load_from_state(recent_repos, active_repo)
        # Restore per-repo graph column widths for the active repo.
        # Ignore saved values whose total is unreasonably small
        # (stale config from a previous version).
        graph_widths = load_graph_column_widths(config, active_repo)
        if graph_widths is not None and len(graph_widths) == 3 and sum(graph_widths) >= 300:
            self._graph_table.set_divider_positions(
                [graph_widths[0], graph_widths[0] + graph_widths[1]],
            )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt naming
        """Persist geometry, splitter sizes, and repo tabs before close.

        We write to disk *before* delegating to ``super().closeEvent``
        so a crash inside the base implementation cannot leave the
        on-disk state one resize behind. When :attr:`_config_path` is
        ``None`` this method is effectively a pass-through — the
        configuration file is left untouched.

        When the diff view is open at close time the left panel is
        hidden, which collapses its column to zero in the live
        splitter sizes. We refuse to overwrite the saved layout with
        those zeroed-out values and instead fall back to the last
        sizes we observed while the panel was visible.
        """
        if self._config_path is not None:
            config = load_config(self._config_path)
            config["window_size"] = [self.width(), self.height()]
            splitter_sizes: dict[str, list[int]] = {}
            if self._top_splitter is not None:
                if self._left_panel.isVisible():
                    splitter_sizes[SPLITTER_KEY_HORIZONTAL] = (
                        self._top_splitter.sizes()
                    )
                elif self._last_normal_splitter_sizes is not None:
                    splitter_sizes[SPLITTER_KEY_HORIZONTAL] = (
                        self._last_normal_splitter_sizes
                    )
            config["splitter_sizes"] = splitter_sizes
            # Persist repo tabs.
            tab_state = self._repo_tabs_vm.save_to_state()
            config["recent_repos"] = tab_state["paths"]
            config["active_repo"] = tab_state["active_path"]
            # Persist per-repo graph column widths.
            active = tab_state["active_path"]
            if active and self._graph_table is not None:
                divs = self._graph_table.divider_positions()
                save_graph_column_widths(
                    config, active,
                    [divs[0], divs[1] - divs[0], 100],  # [branch_w, graph_w, _]
                )
            save_config(self._config_path, config)
        super().closeEvent(event)

    def _on_app_state_changed(self, state: Qt.ApplicationState) -> None:
        """Refresh the repository whenever the application becomes active.

        ``QApplication.applicationStateChanged`` fires once per
        transition: ``Qt.ApplicationActive`` when the user switches
        back to this app (Alt-Tab, click on the taskbar, un-minimise),
        ``Qt.ApplicationInactive`` / ``Qt.ApplicationSuspended``
        otherwise. We only act on the active transition; the inactive
        one is the user's "I'm done" signal and there is no work to
        do when they leave.

        The VM swallows the no-op cases (no repo / busy), so this
        slot stays a one-liner.
        """
        if state == Qt.ApplicationState.ApplicationActive:
            self._main_vm.refresh_state()

    def _on_busy_changed(self, busy: bool) -> None:
        """Show / hide the spinner and toggle the re-entrancy guard."""
        self._busy_spinner.setVisible(busy)
        if busy:
            self._status.showMessage("Working…")
        # Disable the toolbar buttons that could race with the worker.
        remote_actions = (
            self._action_fetch,
            self._action_pull,
            self._action_push,
            self._action_manage_remotes,
        )
        for action in (
            self._action_undo,
            self._action_redo,
            self._action_close,
            *remote_actions,
        ):
            action.setEnabled(action.isEnabled() and not busy)
        # When a busy operation ends, the actions' enabled state must
        # be re-evaluated against the new repository state.
        if not busy:
            self._update_remote_actions()

    def _on_error(self, message: str) -> None:
        """Show error in the status bar and as a toast popup."""
        self._status.showMessage(f"Error: {message}", 8000)
        self._show_toast(message)

    def _show_toast(self, message: str) -> None:
        """Display a short-lived notification in the bottom-right corner.

        Dismisses any currently visible toast, then shows a new
        red-background label that hides itself after 12 seconds or
        on click. If the mouse is over the toast the auto-hide timer
        is paused so the user can read the message at their own pace.
        The toast is positioned above the status bar.
        """
        from PySide6.QtCore import QEvent, QObject, QTimer

        if hasattr(self, "_toast_label") and self._toast_label is not None:
            try:
                self._toast_label.hide()
                self._toast_label.deleteLater()
            except RuntimeError:
                pass
            self._toast_label = None

        toast_timeout_ms = 12_000

        label = QLabel(message, self)
        label.setStyleSheet(
            "background-color: #c0392b; color: white; padding: 10px 14px; "
            "border-radius: 6px; font-size: 13px;"
        )
        label.setWordWrap(True)
        label.setMaximumWidth(420)
        label.adjustSize()
        label.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        # Click to dismiss
        label.mousePressEvent = lambda _e: label.hide()  # type: ignore[assignment]

        # Event filter: pause / resume auto-hide on hover
        timer = QTimer(label)
        timer.setSingleShot(True)
        timer.timeout.connect(label.hide)
        timer.setInterval(toast_timeout_ms)

        class _HoverFilter(QObject):
            def eventFilter(self, _obj, event):  # noqa: N802, N805 - Qt override
                if event.type() == QEvent.Type.HoverEnter:
                    timer.stop()
                elif event.type() == QEvent.Type.HoverLeave:
                    timer.start(toast_timeout_ms)
                return False

        toast_filter = _HoverFilter(label)
        label.installEventFilter(toast_filter)

        y_offset = 40  # above the status bar
        label.move(
            self.width() - label.width() - 16,
            self.height() - label.height() - y_offset,
        )
        label.show()
        label.raise_()
        self._toast_label = label

        timer.start()

    def _on_repository_changed(self, path: str | None) -> None:
        if path is None:
            self._status.showMessage("No repository")
            self._action_close.setEnabled(False)
        else:
            self._status.showMessage(f"Repository: {path}")
            self._action_close.setEnabled(True)
            # Ensure there is a tab for this repository (no-op if already present).
            self._repo_tabs_vm.add_tab(path)
        self._update_remote_actions()

    def _update_remote_actions(self) -> None:
        """Enable Push / Pull / Fetch only when a repository is open."""
        repo_open = (
            self._main_vm.repository_manager() is not None
            and self._main_vm.repository_manager().is_open
        )
        for action in (
            self._action_fetch,
            self._action_pull,
            self._action_push,
            self._action_manage_remotes,
        ):
            action.setEnabled(repo_open and not self._main_vm.is_busy())

    # ----- actions -----------------------------------------------------

    def _open_repository_dialog(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Open Git Repository")
        if not path:
            return
        manager = RepositoryManager()
        try:
            manager.open(path)
        except (RepositoryNotFoundError, GitError) as exc:
            QMessageBox.warning(self, "Open Repository", str(exc))
            return
        self.set_repository(manager)

    def _open_clone_dialog(self) -> None:
        """Show the :class:`CloneDialog`; on accept, fire the VM clone."""
        # Default destination: the open repository's path, or ``None``.
        repo = self._main_vm.repository_manager()
        default_path = str(repo.path) if repo and repo.path else None
        dialog = CloneDialog(default_path=default_path, parent=self)
        dialog.accepted.connect(
            lambda url, path: self._main_vm.clone_repository(url, path),
        )
        dialog.exec()

    def _open_init_dialog(self) -> None:
        """Pick a directory and initialise a fresh repository there."""
        path = QFileDialog.getExistingDirectory(self, "Init New Repository In…")
        if not path:
            return
        manager = RepositoryManager()
        try:
            manager.init(path)
        except GitError as exc:
            QMessageBox.warning(self, "Init Repository", str(exc))
            return
        self.set_repository(manager)

    def _open_remote_manage_dialog(self) -> None:
        """Show the :class:`RemoteManageDialog`; reflect changes via the VM."""
        repo = self._main_vm.repository_manager()
        if repo is None or not repo.is_open:
            return
        dialog = RemoteManageDialog(self)
        dialog.set_remotes(self._main_vm.list_remotes())
        dialog.add_requested.connect(
            lambda name, url: self._main_vm.add_remote(name, url),
        )
        dialog.remove_requested.connect(
            lambda name: self._main_vm.remove_remote(name),
        )
        # Refresh the table after a successful add / remove.
        self._main_vm.command_processor().stack_changed.connect(
            lambda: dialog.set_remotes(self._main_vm.list_remotes()),
        )
        dialog.exec()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About git-py",
            "git-py — a GitKraken-like Git client built with PySide6 and pygit2.",
        )

    def _close_current_tab(self) -> None:
        """Remove the current repo tab (the repo on disk is untouched)."""
        idx = self._repo_tabs_vm.active_index
        if idx >= 0:
            self._repo_tabs_vm.remove_tab(idx)


def _same_path(a: str, b: str) -> bool:
    """Return ``True`` if two paths point to the same directory."""
    from pathlib import Path

    return Path(a).resolve() == Path(b).resolve()


__all__ = ["MainWindow"]
