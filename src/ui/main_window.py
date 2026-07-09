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
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.ui.widgets.action_history_widget import ActionHistoryWidget
from src.ui.widgets.diff_view_widget import DiffViewWidget
from src.ui.widgets.graph_panel import GraphTableWidget
from src.ui.widgets.left_panel import LeftPanel
from src.ui.widgets.log_widget import LogWidget
from src.ui.widgets.repo_bar_widget import RepoBarWidget
from src.ui.widgets.right_panel import RightPanel
from src.ui.widgets.search_bar import SearchBar
from src.ui.widgets.terminal_widget import TerminalWidget
from src.utils.config import (
    SPLITTER_KEY_HORIZONTAL,
    load_config,
    load_graph_column_widths,
    load_hotkey,
    load_splitter_sizes,
    load_window_size,
    save_config,
    save_graph_column_widths,
)
from src.utils.theme import DARK_THEME
from src.viewmodels.main_viewmodel import MainViewModel
from src.viewmodels.repo_tabs_viewmodel import RepoTabViewModel


class MainWindow(QMainWindow):
    """Top-level window: graph + right panel over a terminal stub."""

    # Minimum width for the right panel — used as a safety net to
    # prevent the panel from disappearing when it is restored from a
    # config that saved it with zero width (a known regression from
    # Stage 9). 200 px is wide enough to show meaningful content.
    MIN_RIGHT_WIDTH = 200

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
        # Load config early so `_build_menu` reads hotkey preferences.
        self._config: dict[str, object] = (
            load_config(self._config_path) if self._config_path is not None else {}
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
        # Stage 7: search bar created early so the toolbar (next) can use it.
        self._search_bar = SearchBar(self)
        self._search_bar.search_requested.connect(self._on_search_commits)
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
        # and laid out once. We defer via ``QTimer.singleShot(0)`` so
        # the window appears immediately — otherwise the synchronous
        # ``get_all_history()`` inside ``refresh_graph()`` blocks the
        # UI thread during construction, making the window look frozen
        # on startup.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._restore_state)

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

    def _open_repository_async(self, manager: RepositoryManager) -> None:
        """Bind *manager* and start background data loading.

        Uses :meth:`MainViewModel.set_repository` with ``refresh=False``
        to emit ``repository_changed`` and update the tab bar / status
        bar immediately, then kicks off :meth:`MainViewModel.load_repository_data`
        on a worker thread so the graph and side panels populate without
        freezing the UI.
        """
        print("[repo] _open_repository_async start")
        self._repo_manager = manager
        print("[repo] calling set_repository(refresh=False)...")
        self._main_vm.set_repository(manager, refresh=False)
        print("[repo] set_repository done, status:", manager.path)
        self._status.showMessage(f"Repository: {manager.path}")
        self._action_close.setEnabled(manager is not None)
        print("[repo] calling load_repository_data...")
        self._main_vm.load_repository_data()
        print("[repo] _open_repository_async done")

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
        # Context-menu signals from the tab bar: the widget stays
        # passive and emits the clicked tab's repo path; the window
        # forwards to :class:`MainViewModel` so the helpers can share
        # the central clipboard / Explorer helpers with the rest of
        # the app.
        self._repo_bar.show_folder_requested.connect(self._on_show_repo_folder)
        self._repo_bar.copy_path_requested.connect(self._on_copy_repo_path)
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
        """Switch the active repository to the tab at *index*.

        No-ops when the current VM is busy (the previous repository
        data is still loading on a background thread) or when the
        requested path is already the active repository.  The busy
        guard prevents a second tab switch from racing with the
        :class:`AsyncWorker` that was launched by the first one.

        When a tab click arrives while a worker is in flight we
        restore the tab bar selection to the currently-open repository
        so the tab bar does not get out of sync with the actual state.
        To avoid a signal feedback loop the ``active_tab_changed``
        signal is temporarily disconnected during the restoration.
        """
        if self._main_vm.is_busy():
            current = self._main_vm.repository_manager()
            if current is not None and current.path is not None:
                # Temporarily unhook to avoid triggering
                # _on_tab_changed again from add_tab → set_active_tab.
                self._repo_tabs_vm.active_tab_changed.disconnect(
                    self._on_tab_changed,
                )
                self._repo_tabs_vm.add_tab(current.path)
                self._repo_tabs_vm.active_tab_changed.connect(
                    self._on_tab_changed,
                )
            return
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
        self._open_repository_async(manager)

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

        self._action_settings = QAction("&Settings…", self)
        self._action_settings.triggered.connect(self._open_settings_dialog)
        file_menu.addAction(self._action_settings)

        file_menu.addSeparator()
        action_exit = QAction("E&xit", self)
        action_exit.setShortcut(QKeySequence.StandardKey.Quit)
        action_exit.triggered.connect(self.close)
        file_menu.addAction(action_exit)

        edit_menu = bar.addMenu("&Edit")
        self._action_undo = QAction("&Undo", self)
        self._action_undo.setShortcut(
            QKeySequence(load_hotkey(self._config, "undo", "Ctrl+Z")),
        )
        self._action_undo.setEnabled(False)
        self._action_undo.triggered.connect(self._main_vm.undo)
        edit_menu.addAction(self._action_undo)

        self._action_redo = QAction("&Redo", self)
        self._action_redo.setShortcut(
            QKeySequence(load_hotkey(self._config, "redo", "Ctrl+Y")),
        )
        self._action_redo.setEnabled(False)
        self._action_redo.triggered.connect(self._main_vm.redo)
        edit_menu.addAction(self._action_redo)

        remote_menu = bar.addMenu("&Remote")
        self._action_fetch = QAction("&Fetch from origin", self)
        self._action_fetch.setShortcut(
            QKeySequence(load_hotkey(self._config, "fetch", "Ctrl+Shift+F")),
        )
        self._action_fetch.setEnabled(False)
        self._action_fetch.triggered.connect(lambda: self._main_vm.fetch_changes("origin"))
        remote_menu.addAction(self._action_fetch)

        self._action_pull = QAction("&Pull from origin", self)
        self._action_pull.setShortcut(
            QKeySequence(load_hotkey(self._config, "pull", "Ctrl+Shift+P")),
        )
        self._action_pull.setEnabled(False)
        self._action_pull.triggered.connect(lambda: self._main_vm.pull_changes("origin"))
        remote_menu.addAction(self._action_pull)

        self._action_push = QAction("&Push to origin", self)
        self._action_push.setShortcut(
            QKeySequence(load_hotkey(self._config, "push", "Ctrl+Shift+U")),
        )
        self._action_push.setEnabled(False)
        self._action_push.triggered.connect(lambda: self._main_vm.push_changes("origin"))
        remote_menu.addAction(self._action_push)

        remote_menu.addSeparator()
        self._action_manage_remotes = QAction("&Manage Remotes…", self)
        self._action_manage_remotes.setEnabled(False)
        self._action_manage_remotes.triggered.connect(self._open_remote_manage_dialog)
        remote_menu.addAction(self._action_manage_remotes)

        stash_menu = bar.addMenu("&Stash")
        self._action_stash_push = QAction("&Stash Changes", self)
        self._action_stash_push.setShortcut(
            QKeySequence(load_hotkey(self._config, "stash_push", "Ctrl+Shift+S")),
        )
        self._action_stash_push.setEnabled(False)
        self._action_stash_push.triggered.connect(self._on_stash_push)
        stash_menu.addAction(self._action_stash_push)

        self._action_stash_pop = QAction("Stash &Pop", self)
        self._action_stash_pop.setShortcut(
            QKeySequence(load_hotkey(self._config, "stash_pop", "Ctrl+Shift+O")),
        )
        self._action_stash_pop.setEnabled(False)
        self._action_stash_pop.triggered.connect(self._on_stash_pop)
        stash_menu.addAction(self._action_stash_pop)

        view_menu = bar.addMenu("&View")
        self._action_view_left_panel = QAction("Left Panel", self, checkable=True, checked=True)
        view_menu.addAction(self._action_view_left_panel)

        self._action_view_terminal = QAction("Terminal", self, checkable=True, checked=True)
        view_menu.addAction(self._action_view_terminal)

        self._action_view_history = QAction("History", self, checkable=True, checked=True)
        view_menu.addAction(self._action_view_history)

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

    # ----- View menu toggle handlers ------------------------------------

    def _on_view_terminal_toggled(self, visible: bool) -> None:
        idx = self._bottom_tabs.indexOf(self._terminal)
        if idx >= 0:
            self._bottom_tabs.setTabVisible(idx, visible)

    def _on_view_history_toggled(self, visible: bool) -> None:
        idx = self._bottom_tabs.indexOf(self._action_history)
        if idx >= 0:
            self._bottom_tabs.setTabVisible(idx, visible)

    def _build_toolbar(self) -> None:
        """Stage 8: Edit toolbar with Undo / Redo, then Remote / Stash / Search."""
        edit_toolbar = QToolBar("Edit", self)
        edit_toolbar.setObjectName("edit-toolbar")
        edit_toolbar.setMovable(False)
        edit_toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        edit_toolbar.addAction(self._action_undo)
        edit_toolbar.addAction(self._action_redo)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, edit_toolbar)
        self._edit_toolbar = edit_toolbar

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

        # Stage 7: Stash toolbar with Push / Pop shortcuts.
        stash_toolbar = QToolBar("Stash", self)
        stash_toolbar.setObjectName("stash-toolbar")
        stash_toolbar.setMovable(False)
        stash_toolbar.addAction(self._action_stash_push)
        stash_toolbar.addAction(self._action_stash_pop)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, stash_toolbar)
        self._stash_toolbar = stash_toolbar

        # Stage 7: commit search bar as a compact toolbar.
        self._search_bar.setMaximumHeight(32)
        self._search_bar.setMinimumWidth(260)
        search_toolbar = QToolBar("Search", self)
        search_toolbar.setObjectName("search-toolbar")
        search_toolbar.setMovable(False)
        search_toolbar.addWidget(self._search_bar)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, search_toolbar)

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

        # Wire context-menu actions from the graph table.
        self._graph_table.checkout_commit_requested.connect(
            self._main_vm.checkout_commit,
        )
        self._graph_table.copy_diff_requested.connect(self._on_copy_diff)
        self._graph_table.discard_changes_requested.connect(self._on_discard_changes)
        self._graph_table.stash_apply_requested.connect(self._on_stash_apply_graph)
        self._graph_table.stash_pop_requested.connect(self._on_stash_pop_graph)
        self._graph_table.stash_drop_requested.connect(self._on_stash_drop_graph)
        self._graph_table.stash_push_requested.connect(self._on_stash_push_graph)
        # "Create branch here" — emitted after the inline editor
        # commits (Enter pressed); routes to MainViewModel which
        # owns the CreateBranchCommand and undo/redo bookkeeping.
        self._graph_table.create_branch_here_requested.connect(
            self._on_create_branch_here,
        )

        # Branch chip gestures: double-click on a chip checks the
        # branch out; the context menu on a chip can issue a merge or
        # rebase into the current HEAD.  Both signals carry the bare
        # ref name (e.g. ``"main"`` for the local branch, ``"origin/main"``
        # for a remote-tracking ref) and the right pane / merge verb
        # handle the resolution.
        self._graph_table.checkout_branch_requested.connect(
            self._on_graph_branch_checkout,
        )
        self._graph_table.merge_branch_requested.connect(
            self._on_graph_branch_merge,
        )
        self._graph_table.rebase_branch_requested.connect(
            self._on_graph_branch_rebase,
        )
        # Drag-and-drop on a branch chip: dropping one chip on
        # another surfaces a context menu with the same merge / rebase
        # actions, but with the *drop target* as the integration
        # branch (instead of the current HEAD).  Mirrors the left
        # panel's drag-and-drop semantics.
        self._graph_table.branch_dropped_on_branch.connect(
            self._on_graph_branch_dropped,
        )
        # "Copy branch name" / "Copy commit sha" — emitted from the
        # branch-chip context menu (right-click on a chip in the
        # leftmost column). Mirrors the equivalent wiring on the
        # left panel, which calls ``MainViewModel.copy_to_clipboard``
        # directly; here the widget stays passive and the slot
        # below forwards the value.
        self._graph_table.copy_branch_name_requested.connect(
            self._on_copy_branch_name,
        )
        self._graph_table.copy_commit_sha_requested.connect(
            self._on_copy_commit_sha,
        )

        # When the VM clears the selection (toggle-off) the graph
        # widget must also remove its highlight ring.  The reverse
        # direction (VM → graph) for a *new* selection is redundant
        # (the graph already set it on click) but harmless.
        self._main_vm.selection_changed.connect(
            self._graph_table.set_selected_sha,
        )
        # Stash toolbar actions are gated on graph selection: Push
        # and Pop are only enabled when a stash node is selected, so
        # re-evaluate them whenever the user picks / deselects a
        # commit in the graph, the stash list changes, or the
        # working-tree status changes.
        self._main_vm.selection_changed.connect(self._update_stash_actions)
        self._main_vm.branch_panel_view_model().references_changed.connect(
            self._update_stash_actions,
        )
        self._main_vm.commit_panel_view_model().file_changes_changed.connect(
            self._update_stash_actions,
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
        # Prevent the user from collapsing the right panel to zero
        # width by dragging its left edge — a common source of the
        # "right panel disappeared" bug. The panel can still be
        # hidden programmatically (via ``setVisible(False)`` when
        # nothing is selected).
        top.setCollapsible(2, False)
        # Track splitter drags while the left panel is in its normal
        # (visible) state so we can restore the user's layout if the
        # window is closed with the diff view open. See ``closeEvent``
        # for the consumer side. We also gate on the right panel
        # being visible — dragging while the right panel is hidden
        # would produce cached sizes with a zero-width right column,
        # and those would overwrite the user's normal layout on close
        # (regression #2 for the same "right panel disappeared" bug).
        top.splitterMoved.connect(self._on_top_splitter_moved)

        self._terminal = TerminalWidget(DARK_THEME, self)
        self._terminal.setMaximumHeight(180)
        self._main_vm.repository_changed.connect(self._on_repo_path_for_terminal)

        self._log_widget = LogWidget(self)
        self._log_widget.setMaximumHeight(180)

        self._action_history = ActionHistoryWidget(DARK_THEME, self)
        self._action_history.setMaximumHeight(180)
        self._action_history.set_processor(self._main_vm.command_processor())

        self._bottom_tabs = QTabWidget(self)
        self._bottom_tabs.setMaximumHeight(200)
        self._bottom_tabs.addTab(self._terminal, "Terminal")
        self._bottom_tabs.addTab(self._log_widget, "Log")
        self._bottom_tabs.addTab(self._action_history, "History")

        # Wire View-menu toggle actions now that widgets exist.
        self._action_view_left_panel.toggled.connect(self._left_panel.setVisible)
        self._action_view_terminal.toggled.connect(self._on_view_terminal_toggled)
        self._action_view_history.toggled.connect(self._on_view_history_toggled)

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

    def _on_copy_diff(self, sha: str) -> None:
        """Copy the full unified diff to the system clipboard."""
        if sha == "WIP":
            text = self._main_vm.get_workdir_diff_text()
            label = "WIP"
        elif self._main_vm.is_stash_sha(sha):
            text = self._main_vm.get_stash_diff_text(sha)
            label = f"stash {sha[:7]}"
        else:
            text = self._main_vm.get_commit_diff_text(sha)
            label = sha[:7]
        if not text:
            self._on_error(f"No diff available for {label}")
            return
        QApplication.clipboard().setText(text)
        self._status.showMessage(f"Diff of {label} copied to clipboard", 3000)

    def _on_copy_branch_name(self, name: str) -> None:
        """Copy a branch ref name from the graph chip context menu.

        ``name`` arrives as the chip's *full* ref (``"main"`` for a
        local chip, ``"origin/main"`` for a remote-tracking one) so
        the clipboard receives a value the user can paste straight
        into a ``git checkout <name>`` command. The empty-string
        guard mirrors the defensive copy in the left panel — a stale
        graph rebuild can in principle deliver an empty chip, and
        silently clearing the clipboard would be surprising.
        """
        if not name:
            return
        self._main_vm.copy_to_clipboard(name)
        self._status.showMessage(f"Copied branch name '{name}'", 3000)

    def _on_copy_commit_sha(self, sha: str) -> None:
        """Copy a commit SHA from the graph chip context menu."""
        if not sha:
            return
        self._main_vm.copy_to_clipboard(sha)
        self._status.showMessage(f"Copied commit {sha[:7]}", 3000)

    def _on_discard_changes(self, sha: str) -> None:
        """Discard all uncommitted changes (from graph WIP context menu)."""
        self._main_vm.discard_changes()

    def _on_stash_push_graph(self, sha: str) -> None:
        """Push WIP onto the stash list (from graph WIP context menu).

        Mirrors the toolbar's :meth:`_on_stash_push` so the right-click
        path and the ``Ctrl+Shift+S`` path share the same default
        message and the same underlying command. ``sha`` is the WIP
        marker (``"WIP"``) carried by the signal and is ignored —
        ``stash_push`` reads the live worktree state, so nothing else
        is needed.
        """
        del sha
        self._main_vm.stash_push("WIP")

    def _on_create_branch_here(self, sha: str, name: str) -> None:
        """Create a branch at *sha* with the user-supplied *name*.

        Fired from the graph's "Create branch here" context-menu
        action after the inline ``QLineEdit`` collects the name.
        Routing through :meth:`MainViewModel.create_branch` means
        the operation goes through ``CreateBranchCommand`` and
        therefore onto the undo stack, matching every other branch
        creation in the app.

        We do not validate the name here — ``create_branch`` (via
        the ``pygit2`` checkout wrapper) raises a ``GitError`` that
        the VM surfaces through ``error_occurred``; the graph is
        auto-refreshed on success and the new chip collapses into
        the existing branch column (the row already had at least
        one branch, so the just-created branch is the second chip
        the user sees when they hover).
        """
        self._main_vm.create_branch(name=name, target_sha=sha)

    def _on_stash_apply_graph(self, sha: str) -> None:
        """Apply stash by OID (from graph context menu)."""
        idx = self._stash_index_for_sha(sha)
        if idx >= 0:
            self._main_vm.stash_apply(idx)

    def _on_stash_pop_graph(self, sha: str) -> None:
        """Pop stash by OID (from graph context menu)."""
        idx = self._stash_index_for_sha(sha)
        if idx >= 0:
            self._main_vm.stash_pop(idx)

    def _on_stash_drop_graph(self, sha: str) -> None:
        """Drop stash by OID (from graph context menu)."""
        idx = self._stash_index_for_sha(sha)
        if idx >= 0:
            self._main_vm.stash_drop(idx)

    def _on_graph_branch_checkout(self, name: str) -> None:
        """Checkout the branch whose chip the user double-clicked on the graph.

        ``name`` is the ref name as stored on the chip — local branches
        come through as bare ``"main"``, remote-tracking refs as
        ``"origin/main"``.  The VM's :meth:`checkout_branch` handles
        the local case directly; remote refs check whether a local
        tracking branch exists — if not, the safe fetch+create+checkout
        is used; if it does, the user gets a confirmation dialog asking
        whether to hard-reset the local branch to the remote tip
        (matching the left panel's double-click behaviour).
        """
        if "/" not in name:
            self._main_vm.checkout_branch(name)
            return
        local_name = name.split("/", 1)[1]
        if not self._main_vm.local_branch_exists(local_name):
            self._main_vm.fetch_and_checkout_remote_branch(name)
            return
        from PySide6.QtWidgets import QMessageBox
        confirm = QMessageBox.question(
            self,
            "Reset Local Branch",
            f"Reset local '{local_name}' to match the remote?\n\n"
            f"This will discard any unpushed commits on '{local_name}' "
            f"(including the merge that is not yet on the remote). "
            f"Working-tree changes will also be lost.\n\n"
            f"Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._main_vm.reset_local_branch_to_remote(name)

    def _on_graph_branch_merge(self, source: str, target: str) -> None:
        """Merge ``source`` into ``target`` from the graph chip context menu.

        Empty ``source`` or ``target`` (which can happen on an unborn
        HEAD or a stale chip cache) is a no-op — the VM would just
        emit its own error otherwise.

        ``no_ff=True`` is passed so the merge commit is always
        visible in the graph, even on a fast-forward. The user
        asked for a merge by right-clicking a branch chip; the
        history should reflect that explicitly instead of
        silently moving the ref.
        """
        if not source or not target:
            return
        self._main_vm.merge_branch(source, target=target, no_ff=True)

    def _on_graph_branch_rebase(self, source: str, target: str) -> None:
        """Rebase ``source`` onto ``target`` from the graph chip context menu.

        The two-command sequence (checkout ``source`` then rebase onto
        ``target``) is built into the VM's ``rebase_branch`` verb, but
        here we issue them through the same path the left panel's
        drop handler uses to keep undo and logging consistent.
        """
        if not source or not target:
            return
        # Switch to the source first so rebase can move the user's
        # working branch.  When the user is already on ``source`` the
        # checkout is a no-op (HEAD unchanged) and the rebase still
        # works correctly.  Both commands are pushed onto the undo
        # stack independently, so undo restores the previous HEAD even
        # if the rebase gets partway through.
        if self._current_branch_shorthand() != source:
            if not self._main_vm.checkout_branch(source):
                return
        self._main_vm.rebase_branch(target)

    def _on_graph_branch_dropped(self, source: str, target: str) -> None:
        """Handle a branch-chip drag-and-drop on the graph.

        Mirrors the left panel's drop handler: opens a small
        :class:`QMenu` next to the cursor with **Merge {source}
        into {target}** and **Rebase {source} onto {target}**.
        The menu is positioned at the current cursor position; the
        widget does not pass an explicit drop point through the
        signal because the chip rect itself is small enough that
        the cursor is a reliable anchor.
        """
        if not source or not target or source == target:
            return
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu

        actions = self._build_branch_drop_actions(source, target)
        menu = QMenu(self)
        for action in actions:
            menu.addAction(action)
        menu.exec(QCursor.pos())

    def _build_branch_drop_actions(
        self, source: str, target: str,
    ) -> list[QAction]:
        """Build the :class:`QAction` list for a chip-on-chip drop.

        Exposed (single-underscore) so tests can inspect the
        actions synchronously. ``_on_graph_branch_dropped`` uses
        the same builder to populate the actual menu, splitting
        the action construction from the menu lifecycle so the
        tests do not have to spin up a real ``QMenu.exec`` (which
        would block on user input).

        ``source == target`` and empty inputs are filtered out by
        the caller — the builder assumes the input is sane.

        The merge action passes ``no_ff=True`` so the merge commit
        is always visible in the graph, even when the source is a
        fast-forward of the target.  The user explicitly asked for
        a merge by dropping one branch onto another, so the
        history should show a merge — not silently move the ref.
        """
        actions: list[QAction] = []
        merge_label = f"Merge {source} into {target}"
        merge_action = QAction(merge_label, self)
        merge_action.triggered.connect(
            lambda checked=False, s=source, t=target: (
                self._main_vm.merge_branch(s, target=t, no_ff=True)
            ),
        )
        actions.append(merge_action)

        rebase_label = f"Rebase {source} onto {target}"
        rebase_action = QAction(rebase_label, self)
        rebase_action.triggered.connect(
            lambda checked=False, s=source, t=target: (
                self._on_graph_branch_rebase(s, t)
            ),
        )
        actions.append(rebase_action)
        return actions

    def _current_branch_shorthand(self) -> str:
        """Return the current branch shorthand, or ``""`` when no repo / unborn."""
        mgr = self._main_vm.repository_manager()
        if mgr is None or not mgr.is_open or mgr.repo.head_is_unborn:
            return ""
        return mgr.repo.head.shorthand

    def _stash_index_for_sha(self, sha: str) -> int:
        repos = self._main_vm.repository_manager()
        if repos is None:
            return -1
        for entry in repos.stash_list:
            if entry.sha == sha:
                return entry.index
        return -1

    def _on_top_splitter_moved(self, _pos: int, _index: int) -> None:
        """Cache the current splitter sizes when both panels are visible.

        The cache is used by :meth:`closeEvent` to avoid overwriting
        the saved layout with the zeroed-out sizes that the splitter
        reports while one of the panels is hidden (i.e. while the
        diff view is open *or* while no commit is selected so the
        right panel is hidden). We deliberately do nothing while
        either panel is hidden — the cache must reflect a "normal"
        state where all three columns are visible.
        """
        if (
            self._top_splitter is not None
            and self._left_panel.isVisible()
            and self._right_panel.isVisible()
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

        Tab restoration re-opens the previous session's active
        repository. Because ``_restore_state`` runs via a deferred
        ``QTimer.singleShot(0)``, the window is already painted by
        the time the repo opens — the ``get_all_history`` graph walk
        may briefly block the event loop but the user already sees
        the window frame.
        """
        if self._config_path is None:
            return
        config = self._config
        width, height = load_window_size(config)
        self.resize(width, height)
        splitter_sizes = load_splitter_sizes(config)
        horizontal = splitter_sizes.get(SPLITTER_KEY_HORIZONTAL)
        if horizontal is not None and self._top_splitter is not None and len(horizontal) == 3:
            # Guard against old configs where the right panel was
            # saved with zero width (a known regression from Stage 9).
            # ``QSplitter.setSizes`` remembers the value for hidden
            # widgets and restores it when they become visible, so a
            # zero would make the panel invisible until the user
            # manually drags the splitter handle. We raise the right
            # column to at least ``MIN_RIGHT_WIDTH`` *before* calling
            # ``setSizes``, taking the space from the centre column.
            safe = list(horizontal)
            if safe[2] < self.MIN_RIGHT_WIDTH:
                donation = self.MIN_RIGHT_WIDTH - safe[2]
                if safe[1] >= donation:
                    safe[1] -= donation
                    safe[2] += donation
            self._top_splitter.setSizes(safe)
        recent_repos = config.get("recent_repos", [])
        active_repo = config.get("active_repo")
        if recent_repos:
            # Temporarily unhook the signal while loading the tab
            # state so ``_on_tab_changed`` does not fire during
            # ``load_from_state`` itself. Once the tabs are populated
            # we reconnect and explicitly activate the previous
            # session's tab. By this point the window is painted
            # (``_restore_state`` runs via ``QTimer.singleShot(0)``),
            # so the user sees the window while the graph loads.
            self._repo_tabs_vm.active_tab_changed.disconnect(self._on_tab_changed)
            self._repo_tabs_vm.load_from_state(recent_repos, active_repo)
            self._repo_tabs_vm.active_tab_changed.connect(self._on_tab_changed)
            # Fire the handler manually so the tab bar widget updates
            # and the repo opens.
            self._on_tab_changed(self._repo_tabs_vm.active_index)
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
                if self._left_panel.isVisible() and self._right_panel.isVisible():
                    splitter_sizes[SPLITTER_KEY_HORIZONTAL] = (
                        self._top_splitter.sizes()
                    )
                elif self._last_normal_splitter_sizes is not None:
                    splitter_sizes[SPLITTER_KEY_HORIZONTAL] = (
                        self._last_normal_splitter_sizes
                    )
                else:
                    live = list(self._top_splitter.sizes())
                    if len(live) == 3 and live[2] < self.MIN_RIGHT_WIDTH:
                        donation = self.MIN_RIGHT_WIDTH - live[2]
                        if live[1] >= donation:
                            live[1] -= donation
                            live[2] += donation
                    splitter_sizes[SPLITTER_KEY_HORIZONTAL] = live
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
        self._terminal.close()
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

    def _on_repo_path_for_terminal(self, path: str | None) -> None:
        """Start / stop the embedded terminal when the repo changes.

        ``path`` is ``None`` when the repository is closed (or no
        repo is open); a string when a repository is open (the
        absolute filesystem path).
        """
        self._terminal.set_repo_path(path)

    def _on_search_commits(self, query: str) -> None:
        """Run the commit search and highlight results in the graph.

        Delegates to :meth:`GraphViewModel.search_commits`; on a
        non-empty result selects the first match so the user lands
        directly on it and the right panel shows its details. The
        search bar already debounces at 300 ms, so the handler
        does not add another delay.
        """
        gv = self._main_vm.graph_view_model()
        results = gv.search_commits(query)
        if results and self._graph_table is not None:
            # Select the first match — the selection ring in the
            # graph serves as the visual highlight.
            self._main_vm.set_selected_commit(results[0])
            self._graph_table.scroll_to_commit(results[0])

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
            self._action_stash_push,
            self._action_stash_pop,
        ):
            action.setEnabled(action.isEnabled() and not busy)
        # When a busy operation ends, the actions' enabled state must
        # be re-evaluated against the new repository state.
        if not busy:
            self._status.clearMessage()
            self._update_repo_actions()

    def _on_error(self, message: str) -> None:
        """Show error in the status bar and as a toast popup."""
        self._status.showMessage(f"Error: {message}", 8000)
        self._show_toast(message)

    def _show_toast(self, message: str) -> None:
        """Display a short-lived notification in the bottom-right corner.

        Dismisses any currently visible toast, then shows a new
        red-background widget that hides itself after 12 seconds or
        on click. A small ``×`` button in the top-right corner of the
        toast lets the user dismiss the notification manually. If the
        mouse is over the toast the auto-hide timer is paused so the
        user can read the message at their own pace. The toast is
        positioned above the status bar.
        """
        from PySide6.QtCore import QEvent, QObject, QTimer
        from PySide6.QtWidgets import QPushButton, QWidget

        if hasattr(self, "_toast_label") and self._toast_label is not None:
            try:
                self._toast_label.hide()
                self._toast_label.deleteLater()
            except RuntimeError:
                pass
            self._toast_label = None

        toast_timeout_ms = 12_000

        container = QWidget(self)
        container.setObjectName("toast_container")
        container.setStyleSheet(
            "QWidget#toast_container { background-color: #c0392b; "
            "border-radius: 6px; }"
            "QLabel { color: white; background: transparent; "
            "font-size: 13px; }"
            "QPushButton { color: white; background: transparent; "
            "border: none; font-size: 16px; font-weight: bold; "
            "padding: 0px; }"
            "QPushButton:hover { color: #ffd6d2; }"
        )

        label = QLabel(message, container)
        label.setWordWrap(True)
        label.setMaximumWidth(380)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setContentsMargins(14, 10, 28, 10)

        close_btn = QPushButton("×", container)
        close_btn.setFixedSize(20, 20)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Close")
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Resize the container to fit the label plus padding.
        label.adjustSize()
        container.setFixedSize(
            max(label.width() + 28 + 24, 120),
            max(label.height() + 20, 36),
        )

        # Layout: place the close button in the top-right corner of the container.
        close_btn.move(container.width() - close_btn.width() - 6, 6)

        def _dismiss() -> None:
            timer.stop()
            container.hide()
            container.deleteLater()
            if getattr(self, "_toast_label", None) is container:
                self._toast_label = None

        close_btn.clicked.connect(_dismiss)

        # Click on the message text dismisses the toast (preserve old UX).
        label.mousePressEvent = lambda _e: _dismiss()  # type: ignore[assignment]

        container.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

        # Event filter: pause / resume auto-hide on hover over the
        # whole container (so hovering the close button keeps it open).
        timer = QTimer(container)
        timer.setSingleShot(True)
        timer.timeout.connect(_dismiss)
        timer.setInterval(toast_timeout_ms)

        class _HoverFilter(QObject):
            def eventFilter(self, _obj, event):  # noqa: N802, N805 - Qt override
                if event.type() == QEvent.Type.HoverEnter:
                    timer.stop()
                elif event.type() == QEvent.Type.HoverLeave:
                    timer.start(toast_timeout_ms)
                return False

        toast_filter = _HoverFilter(container)
        container.installEventFilter(toast_filter)
        label.installEventFilter(toast_filter)
        close_btn.installEventFilter(toast_filter)

        y_offset = 40  # above the status bar
        container.move(
            self.width() - container.width() - 16,
            self.height() - container.height() - y_offset,
        )
        container.show()
        container.raise_()
        self._toast_label = container

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
        self._update_repo_actions()

    def _update_repo_actions(self) -> None:
        """Enable Push / Pull / Fetch only when a repository is open."""
        repo_open = (
            self._main_vm.repository_manager() is not None
            and self._main_vm.repository_manager().is_open
        )
        busy = self._main_vm.is_busy()
        for action in (
            self._action_fetch,
            self._action_pull,
            self._action_push,
            self._action_manage_remotes,
        ):
            action.setEnabled(repo_open and not busy)
        self._update_stash_actions()

    def _update_stash_actions(self) -> None:
        """Recompute Stash Push / Stash Pop enabled state.

        Each button gates on a different graph selection:

        * **Stash Pop** is active only when a *stash* node is
          currently selected in the graph
          (``MainViewModel.is_stash_sha`` on ``selected_commit_sha``)
          **and** the stash list has at least one entry — there is
          nothing to pop otherwise.
        * **Stash Changes (push)** is active only when the *WIP*
          node is selected (``selected_commit_sha == WIP_SHA``)
          **and** the working tree has uncommitted changes — there
          is nothing to stash otherwise. Selecting a stash node
          does *not* enable push: push creates a *new* stash
          archive, which is unrelated to any existing one.

        Both are disabled while a long-running operation is in
        flight (busy) or no repository is open.
        """
        repo_open = (
            self._main_vm.repository_manager() is not None
            and self._main_vm.repository_manager().is_open
        )
        busy = self._main_vm.is_busy()
        if not repo_open or busy:
            self._action_stash_push.setEnabled(False)
            self._action_stash_pop.setEnabled(False)
            return
        from src.viewmodels.graph_viewmodel import WIP_SHA

        selected = self._main_vm.selected_commit_sha()
        is_stash = selected is not None and self._main_vm.is_stash_sha(selected)
        is_wip = selected == WIP_SHA
        if is_stash:
            stash_count = len(
                self._main_vm.branch_panel_view_model().stash_list(),
            )
            self._action_stash_pop.setEnabled(stash_count > 0)
        else:
            self._action_stash_pop.setEnabled(False)
        if is_wip:
            has_uncommitted = bool(
                self._main_vm.commit_panel_view_model().file_changes(),
            )
            self._action_stash_push.setEnabled(has_uncommitted)
        else:
            self._action_stash_push.setEnabled(False)

    def _on_stash_push(self) -> None:
        """Run :meth:`MainViewModel.stash_push` with the default message.

        A more sophisticated UI would prompt for a message first; for
        Stage 7 the default ``"WIP"`` is fine — the user can still
        rename via a follow-up dialog if needed.
        """
        self._main_vm.stash_push("WIP")

    def _on_stash_pop(self) -> None:
        """Run :meth:`MainViewModel.stash_pop` on the most recent entry."""
        self._main_vm.stash_pop(0)

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
        self._open_repository_async(manager)

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
        self._open_repository_async(manager)

    def _open_settings_dialog(self) -> None:
        """Show the :class:`SettingsDialog` (modal)."""
        dialog = SettingsDialog(
            config_path=str(self._config_path) if self._config_path else None,
            parent=self,
        )
        dialog.exec()

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

    def _on_show_repo_folder(self, path: str) -> None:
        """Open the OS file explorer at the given *path*.

        Forwarded from the repository tab bar's right-click context
        menu (*Show repo folder*). The status bar reflects the action
        so the user sees confirmation that Explorer opened — the
        underlying helper silently no-ops on missing paths so we
        cannot raise a dialog here.
        """
        if not path:
            return
        self._main_vm.show_repo_in_folder(path)
        self._status.showMessage(f"Opened {path} in Explorer", 3000)

    def _on_copy_repo_path(self, path: str) -> None:
        """Copy a repository path to the system clipboard.

        Forwarded from the repository tab bar's right-click context
        menu (*Copy repo path*). Empty payloads are ignored — a stale
        menu with no selected row must not silently clear the
        clipboard (mirrors the branch-name / commit-sha guards in
        :meth:`_on_copy_branch_name`).
        """
        if not path:
            return
        self._main_vm.copy_repo_path(path)
        self._status.showMessage(f"Copied repository path: {path}", 3000)


def _same_path(a: str, b: str) -> bool:
    """Return ``True`` if two paths point to the same directory."""
    from pathlib import Path

    return Path(a).resolve() == Path(b).resolve()


__all__ = ["MainWindow"]
