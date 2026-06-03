"""Main application window: menu, panels, status bar.

Stage 3 wires the central :class:`MainViewModel` into the window and
adds the WIP / commit panel to the right side. Stage 4 swaps the
left-panel stub for the real references tree
(:class:`LeftPanel`). Layout:

* **Left:** :class:`LeftPanel` — branches / tags / stash tree
* **Centre:** :class:`GraphWidget` (Stage 2)
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
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
)

from src.core.exceptions import GitError, RepositoryNotFoundError
from src.core.repository import RepositoryManager
from src.ui.widgets.commit_detail_panel import CommitDetailPanel
from src.ui.widgets.commit_panel import CommitPanel
from src.ui.widgets.graph_widget import GraphWidget
from src.ui.widgets.left_panel import LeftPanel
from src.ui.widgets.terminal_widget import TerminalWidget
from src.viewmodels.main_viewmodel import MainViewModel


class MainWindow(QMainWindow):
    """Top-level window: graph + (commit panel / commit detail) over a terminal stub."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("git-py")
        self.resize(1280, 800)

        self._main_vm = MainViewModel(self)
        self._repo_manager: RepositoryManager | None = None

        self._build_menu()
        self._build_central()
        self._build_status_bar()

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

    # ----- menu / status bar -------------------------------------------

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action_open = QAction("&Open Repository…", self)
        self._action_open.setShortcut(QKeySequence.StandardKey.Open)
        self._action_open.triggered.connect(self._open_repository_dialog)
        file_menu.addAction(self._action_open)

        self._action_close = QAction("&Close Repository", self)
        self._action_close.setEnabled(False)
        self._action_close.triggered.connect(lambda: self.set_repository(None))
        file_menu.addAction(self._action_close)

        self._action_clone = QAction("&Clone…", self)
        self._action_clone.triggered.connect(lambda: self._stub("Clone"))
        file_menu.addAction(self._action_clone)

        self._action_init = QAction("&Init New Repository…", self)
        self._action_init.triggered.connect(lambda: self._stub("Init"))
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

        top = QSplitter(self)
        top.addWidget(self._left_panel)
        top.addWidget(self._graph_widget)
        top.addWidget(right_splitter)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 4)
        top.setStretchFactor(2, 3)

        self._terminal = TerminalWidget(self)
        self._terminal.setMaximumHeight(180)

        main_splitter = QSplitter(self)
        main_splitter.setOrientation(Qt.Orientation.Vertical)
        main_splitter.addWidget(top)
        main_splitter.addWidget(self._terminal)
        main_splitter.setStretchFactor(0, 5)
        main_splitter.setStretchFactor(1, 1)

        self.setCentralWidget(main_splitter)

    def _build_status_bar(self) -> None:
        self._status = QStatusBar(self)
        self._status.showMessage("No repository")
        self.setStatusBar(self._status)

        self._main_vm.error_occurred.connect(self._on_error)
        self._main_vm.repository_changed.connect(self._on_repository_changed)

    def _on_error(self, message: str) -> None:
        self._status.showMessage(f"Error: {message}", 8000)

    def _on_repository_changed(self, path: str | None) -> None:
        if path is None:
            self._status.showMessage("No repository")
            self._action_close.setEnabled(False)
        else:
            self._status.showMessage(f"Repository: {path}")
            self._action_close.setEnabled(True)

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

    def _stub(self, name: str) -> None:
        QMessageBox.information(
            self,
            f"{name} (stub)",
            f"'{name}' is not implemented yet. Coming in a later stage.",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About git-py",
            "git-py — a GitKraken-like Git client built with PySide6 and pygit2.",
        )


__all__ = ["MainWindow"]
