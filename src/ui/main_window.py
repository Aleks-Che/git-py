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
* **Right (vertical splitter):**
    * :class:`CommitPanel` — file list, message field, commit button
    * :class:`CommitDetailPanel` — details of the selected graph commit
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
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QToolBar,
)

from src.core.exceptions import GitError, RepositoryNotFoundError
from src.core.repository import RepositoryManager
from src.ui.dialogs.clone_dialog import CloneDialog
from src.ui.dialogs.open_or_clone_dialog import OpenOrCloneDialog
from src.ui.dialogs.remote_manage_dialog import RemoteManageDialog
from src.ui.widgets.commit_detail_panel import CommitDetailPanel
from src.ui.widgets.commit_panel import CommitPanel
from src.ui.widgets.graph_widget import GraphWidget
from src.ui.widgets.left_panel import LeftPanel
from src.ui.widgets.log_widget import LogWidget
from src.ui.widgets.repo_bar_widget import RepoBarWidget
from src.ui.widgets.terminal_widget import TerminalWidget
from src.utils.config import (
    SPLITTER_KEY_HORIZONTAL,
    SPLITTER_KEY_RIGHT_VERTICAL,
    load_config,
    load_splitter_sizes,
    load_window_size,
    save_config,
)
from src.viewmodels.main_viewmodel import MainViewModel
from src.viewmodels.repo_tabs_viewmodel import RepoTabViewModel


class MainWindow(QMainWindow):
    """Top-level window: graph + (commit panel / commit detail) over a terminal stub."""

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

        # Splitter references are kept on ``self`` so the persistence
        # layer can read / write their sizes in :meth:`_restore_state`
        # and :meth:`closeEvent`. They are populated by
        # :meth:`_build_central`.
        self._top_splitter: QSplitter | None = None
        self._right_splitter: QSplitter | None = None

        self._build_menu()
        self._build_repo_bar()
        # Force the remote toolbar to sit on the row below the tab bar.
        self.addToolBarBreak(Qt.ToolBarArea.TopToolBarArea)
        self._build_toolbar()
        self._build_central()
        self._build_status_bar()
        self._main_vm.busy_changed.connect(self._on_busy_changed)
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
        self._graph_widget = GraphWidget(self._main_vm.graph_view_model())
        self._commit_panel = CommitPanel(self._main_vm)
        self._detail_panel = CommitDetailPanel(self._main_vm.graph_view_model())

        # Right side: commit panel (top) + commit detail (bottom).
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(self._commit_panel)
        right_splitter.addWidget(self._detail_panel)
        right_splitter.setStretchFactor(0, 1)
        right_splitter.setStretchFactor(1, 1)
        right_splitter.setSizes([320, 320])
        self._right_splitter = right_splitter

        top = QSplitter(self)
        top.addWidget(self._left_panel)
        top.addWidget(self._graph_widget)
        top.addWidget(right_splitter)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 4)
        top.setStretchFactor(2, 3)
        self._top_splitter = top

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
        right_vertical = splitter_sizes.get(SPLITTER_KEY_RIGHT_VERTICAL)
        if (
            right_vertical is not None
            and self._right_splitter is not None
            and len(right_vertical) == 2
        ):
            self._right_splitter.setSizes(right_vertical)
        # Restore repository tabs.
        recent_repos = config.get("recent_repos", [])
        active_repo = config.get("active_repo")
        if recent_repos:
            self._repo_tabs_vm.load_from_state(recent_repos, active_repo)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt naming
        """Persist geometry, splitter sizes, and repo tabs before close.

        We write to disk *before* delegating to ``super().closeEvent``
        so a crash inside the base implementation cannot leave the
        on-disk state one resize behind. When :attr:`_config_path` is
        ``None`` this method is effectively a pass-through — the
        configuration file is left untouched.
        """
        if self._config_path is not None:
            config = load_config(self._config_path)
            config["window_size"] = [self.width(), self.height()]
            splitter_sizes: dict[str, list[int]] = {}
            if self._top_splitter is not None:
                splitter_sizes[SPLITTER_KEY_HORIZONTAL] = self._top_splitter.sizes()
            if self._right_splitter is not None:
                splitter_sizes[SPLITTER_KEY_RIGHT_VERTICAL] = (
                    self._right_splitter.sizes()
                )
            config["splitter_sizes"] = splitter_sizes
            # Persist repo tabs.
            tab_state = self._repo_tabs_vm.save_to_state()
            config["recent_repos"] = tab_state["paths"]
            config["active_repo"] = tab_state["active_path"]
            save_config(self._config_path, config)
        super().closeEvent(event)

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
        self._status.showMessage(f"Error: {message}", 8000)

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
