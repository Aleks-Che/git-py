"""Main application window: menu, panels, status bar.

Stage 0 wires the bare layout (left panel, graph, commit panel, terminal)
with a File/Edit/View/Help menu whose actions are all stubs that pop
an "implemented later" dialog. Real handlers (opening repositories,
undo/redo via ``CommandProcessor``, panel visibility persistence) land
in later stages.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
)

from src.ui.widgets.commit_panel import CommitPanel
from src.ui.widgets.graph_widget import GraphWidget
from src.ui.widgets.left_panel import LeftPanel
from src.ui.widgets.terminal_widget import TerminalWidget


class MainWindow(QMainWindow):
    """Top-level window: horizontal splitter (left/graph/commit) over a terminal."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("git-py")
        self.resize(1280, 800)

        self._build_menu()
        self._build_central()
        self._build_status_bar()

    def _build_menu(self) -> None:
        bar = self.menuBar()

        file_menu = bar.addMenu("&File")
        self._action_open = QAction("&Open Repository…", self)
        self._action_open.setShortcut(QKeySequence.StandardKey.Open)
        self._action_open.triggered.connect(lambda: self._stub("Open Repository"))
        file_menu.addAction(self._action_open)

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
        edit_menu.addAction(self._action_undo)

        self._action_redo = QAction("&Redo", self)
        self._action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self._action_redo.setEnabled(False)
        edit_menu.addAction(self._action_redo)

        view_menu = bar.addMenu("&View")
        for label in ("Left Panel", "Commit Panel", "Terminal"):
            view_menu.addAction(QAction(label, self, checkable=True, checked=True))

        help_menu = bar.addMenu("&Help")
        action_about = QAction("&About git-py", self)
        action_about.triggered.connect(self._show_about)
        help_menu.addAction(action_about)

    def _build_central(self) -> None:
        self._left_panel = LeftPanel()
        self._graph_widget = GraphWidget()
        self._commit_panel = CommitPanel()

        top = QSplitter(self)
        top.addWidget(self._left_panel)
        top.addWidget(self._graph_widget)
        top.addWidget(self._commit_panel)
        top.setStretchFactor(0, 1)
        top.setStretchFactor(1, 4)
        top.setStretchFactor(2, 1)

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
        status = QStatusBar(self)
        status.showMessage("No repository")
        self.setStatusBar(status)

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
