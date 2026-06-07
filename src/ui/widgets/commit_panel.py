"""Right-panel commit-input view: WIP / staging area for the next commit.

Shown when the user clicks the WIP node in the graph (or otherwise
selects the WIP state). Structure (top-to-bottom):

* **Discard All Changes** — red button with a trash icon above the
  unstaged list. Discards all working-tree and index changes at once.
* **Unstaged Files (N)** — collapsible list, with a green *Stage All
  Changes* button on the right of the header. Each row shows the
  file's status badge and path; hovering the row reveals a green
  *Stage File* button on the right.
* **Staged Files (N)** — collapsible list. Each row shows the badge
  and path; hovering reveals a red *Unstage File* button on the right.
* **Commit block** — sticky at the bottom:
    * Commit Summary (single-line ``QLineEdit``)
    * Description (multi-line ``QPlainTextEdit``)
    * Green *Commit Changes to (N) File(s)* button, enabled once at
      least one of the two fields is non-empty **and** there is at
      least one staged file.

Both the unstaged and staged file lists have right-click context menus
with actions: Stage/Unstage, Discard Changes, Ignore (with sub-menu),
Stash File, Show in Folder, Copy File Path, Delete File.

The widget is bound to :class:`MainViewModel` for verbs
(``stage_file`` / ``unstage_file`` / ``stage_all_unstaged`` /
``commit_changes``) and to :class:`CommitPanelViewModel` for state
(staged set, commit message, file list). It is a passive view: it
never holds Git state and never calls ``pygit2`` directly.
"""
from __future__ import annotations

import os as _os

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.models import FileChange
from src.ui.widgets.file_list_model import (
    FileChangeRole,
    FileListDelegate,
    FileListModel,
)
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel

_GREEN = "#3FB950"
_GREEN_HOVER = "#46C75A"
_GREEN_PRESSED = "#2F8B3B"
_RED = "#E8685A"
_RED_HOVER = "#ED7A6E"
_RED_PRESSED = "#C04D40"


# ---------------------------------------------------------------------------
# Trash icon — painted programmatically (no image resources)
# ---------------------------------------------------------------------------


def _trash_icon(size: int = 14) -> QIcon:
    """Paint a simple trash-can icon onto a QPixmap and return a QIcon."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    w, h = size, size
    margin = 2
    lid_y = margin
    lid_h = max(2, h // 8)
    lid_w = w - margin * 2

    body_y = lid_y + lid_h + 1
    body_h = h - body_y - margin
    body_w = max(4, w - margin * 2 - 2)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255))

    body_x = (w - body_w) // 2
    painter.drawRoundedRect(QRect(body_x, body_y, body_w, body_h), 1, 1)

    lid_x = (w - lid_w) // 2
    painter.drawRoundedRect(QRect(lid_x, lid_y, lid_w, lid_h), 1, 1)

    handle_x = w // 2 - lid_w // 4
    handle_y = 0
    handle_w = lid_w // 2
    handle_h = max(2, lid_y + 1)
    painter.drawRect(QRect(handle_x, handle_y, handle_w, handle_h))

    # vertical lines inside the body
    painter.setPen(QColor(80, 80, 80))
    line_margin = 3
    for lx in (body_x + line_margin, body_x + body_w // 2, body_x + body_w - line_margin - 1):
        painter.drawLine(lx, body_y + 2, lx, body_y + body_h - 2)

    painter.end()
    return QIcon(pix)


# ---------------------------------------------------------------------------
# Commit panel
# ---------------------------------------------------------------------------


class CommitPanel(QWidget):
    """WIP / commit-input view bound to :class:`MainViewModel`."""

    def __init__(self, view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._main_vm = view_model
        self._vm: CommitPanelViewModel = view_model.commit_panel_view_model()

        self._build_ui()
        self._wire_signals()
        self._vm.set_repository(view_model.repository_manager())
        self._refresh_all()

    # ----- construction -----------------------------------------------

    def _build_ui(self) -> None:
        # --- Discard All Changes button ---------------------------------
        self._discard_all_button = QPushButton("  Discard All Changes", self)
        self._discard_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._discard_all_button.setIcon(_trash_icon())
        self._discard_all_button.setStyleSheet(
            f"QPushButton {{ background-color: {_RED}; "
            f"color: white; border: none; border-radius: 3px; padding: 4px 10px; "
            f"font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: {_RED_HOVER}; }} "
            f"QPushButton:pressed {{ background-color: {_RED_PRESSED}; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._discard_all_button.setEnabled(False)
        self._discard_all_button.clicked.connect(self._on_discard_all_clicked)

        discard_row = QHBoxLayout()
        discard_row.setContentsMargins(0, 0, 0, 0)
        discard_row.addStretch(1)
        discard_row.addWidget(self._discard_all_button)

        # --- Unstaged Files block ---
        self._unstaged_expander = QToolButton(self)
        self._unstaged_expander.setCheckable(True)
        self._unstaged_expander.setChecked(True)
        self._unstaged_expander.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            "font-weight: bold; padding: 4px 0; } "
            "QToolButton:hover { color: #D4D4D4; }",
        )
        self._unstaged_expander.setArrowType(Qt.ArrowType.DownArrow)
        self._unstaged_expander.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
        )
        self._unstaged_expander.toggled.connect(self._on_unstaged_toggled)

        self._unstaged_header = QLabel("Unstaged Files (0)", self)
        self._unstaged_header.setStyleSheet(
            "font-weight: bold; padding: 4px 0; color: #D4D4D4;",
        )
        self._unstaged_header.setVisible(False)

        self._stage_all_button = QPushButton("Stage All Changes", self)
        self._stage_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stage_all_button.setStyleSheet(
            f"QPushButton {{ background-color: {_GREEN_PRESSED}; "
            f"color: white; border: none; border-radius: 3px; padding: 4px 10px; "
            f"font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: {_GREEN_HOVER}; }} "
            f"QPushButton:pressed {{ background-color: {_GREEN_PRESSED}; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._stage_all_button.setEnabled(False)
        self._stage_all_button.clicked.connect(self._on_stage_all_clicked)

        unstaged_header_row = QHBoxLayout()
        unstaged_header_row.setContentsMargins(0, 0, 0, 0)
        unstaged_header_row.setSpacing(8)
        self._unstaged_expander.setSizePolicy(
            self._unstaged_expander.sizePolicy().horizontalPolicy(),
            self._unstaged_expander.sizePolicy().verticalPolicy(),
        )
        unstaged_header_row.addWidget(self._unstaged_expander, stretch=1)
        unstaged_header_row.addWidget(self._stage_all_button)

        self._unstaged_list = FileListView(staged=False, parent=self)
        self._unstaged_list.clicked.connect(self._on_unstaged_index_clicked)
        self._unstaged_list.stage_file_requested.connect(self._on_stage_file)
        self._unstaged_list.context_action_requested.connect(
            self._on_unstaged_context_action,
        )

        # --- Staged Files block ---
        self._staged_expander = QToolButton(self)
        self._staged_expander.setCheckable(True)
        self._staged_expander.setChecked(True)
        self._staged_expander.setStyleSheet(self._unstaged_expander.styleSheet())
        self._staged_expander.setArrowType(Qt.ArrowType.DownArrow)
        self._staged_expander.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
        )
        self._staged_expander.toggled.connect(self._on_staged_toggled)

        self._unstage_all_button = QPushButton("Unstage All Changes", self)
        self._unstage_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unstage_all_button.setStyleSheet(
            f"QPushButton {{ background-color: {_RED}; "
            f"color: white; border: none; border-radius: 3px; padding: 4px 10px; "
            f"font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: {_RED_HOVER}; }} "
            f"QPushButton:pressed {{ background-color: {_RED_PRESSED}; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._unstage_all_button.setEnabled(False)
        self._unstage_all_button.clicked.connect(self._on_unstage_all_clicked)

        staged_header_row = QHBoxLayout()
        staged_header_row.setContentsMargins(0, 0, 0, 0)
        staged_header_row.setSpacing(8)
        self._staged_expander.setSizePolicy(
            self._staged_expander.sizePolicy().horizontalPolicy(),
            self._staged_expander.sizePolicy().verticalPolicy(),
        )
        staged_header_row.addWidget(self._staged_expander, stretch=1)
        staged_header_row.addWidget(self._unstage_all_button)

        self._staged_list = FileListView(staged=True, parent=self)
        self._staged_list.clicked.connect(self._on_staged_index_clicked)
        self._staged_list.stage_file_requested.connect(self._on_unstage_file)
        self._staged_list.context_action_requested.connect(
            self._on_staged_context_action,
        )

        # --- Commit block (sticky at the bottom) ---
        self._summary = QLineEdit(self)
        self._summary.setPlaceholderText("Commit Summary")
        self._summary.setClearButtonEnabled(True)

        self._description = QPlainTextEdit(self)
        self._description.setPlaceholderText("Description")
        self._description.setTabChangesFocus(True)
        self._description.setMaximumHeight(120)

        self._commit_button = QPushButton("Commit Changes to 0 File", self)
        self._commit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._commit_button.setStyleSheet(
            f"QPushButton {{ background-color: {_GREEN}; color: white; "
            f"border: none; border-radius: 3px; padding: 8px 14px; "
            f"font-weight: 600; font-size: 12px; }} "
            f"QPushButton:hover {{ background-color: {_GREEN_HOVER}; }} "
            f"QPushButton:pressed {{ background-color: {_GREEN_PRESSED}; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._commit_button.setEnabled(False)
        self._commit_button.clicked.connect(self._on_commit_clicked)

        commit_layout = QVBoxLayout()
        commit_layout.setContentsMargins(0, 0, 0, 0)
        commit_layout.setSpacing(4)
        commit_layout.addWidget(self._summary)
        commit_layout.addWidget(self._description, stretch=1)
        commit_layout.addWidget(self._commit_button)

        commit_container = QWidget(self)
        commit_container.setObjectName("commit-block")
        commit_container.setStyleSheet(
            "QWidget#commit-block { background-color: #252526; "
            "border-top: 1px solid #3F3F46; }",
        )
        commit_container.setLayout(commit_layout)
        self._commit_container = commit_container

        # --- Outer layout: scrollable lists on top, commit block pinned ---
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        outer.addLayout(discard_row)
        outer.addLayout(unstaged_header_row)
        outer.addWidget(self._unstaged_list, stretch=1)
        outer.addLayout(staged_header_row)
        outer.addWidget(self._staged_list, stretch=1)
        outer.addSpacing(4)
        outer.addWidget(commit_container)

    def _wire_signals(self) -> None:
        self._summary.textChanged.connect(self._on_summary_changed)
        self._description.textChanged.connect(self._on_description_changed)
        self._vm.file_changes_changed.connect(self._refresh_file_lists)
        self._vm.staged_files_changed.connect(self._refresh_file_lists)
        self._vm.commit_summary_changed.connect(self._on_summary_from_vm)
        self._vm.commit_description_changed.connect(self._on_description_from_vm)
        self._vm.selected_file_changed.connect(self._on_selected_file_changed)

    # ----- VM -> UI ---------------------------------------------------

    def _refresh_all(self) -> None:
        """Populate every section from the current VM state."""
        self._refresh_file_lists()
        self._refresh_commit_button()

    def _refresh_file_lists(self) -> None:
        """Rebuild the Unstaged / Staged lists from the VM."""
        unstaged = self._vm.unstaged_files()
        staged = self._vm.staged_files_detailed()

        self._unstaged_list.populate(unstaged)
        self._staged_list.populate(staged)

        n_unstaged = len(unstaged)
        n_staged = len(staged)
        total_dirty = n_unstaged + n_staged

        self._unstaged_expander.setText(f"  Unstaged Files ({n_unstaged})")
        self._staged_expander.setText(f"  Staged Files ({n_staged})")
        self._unstaged_header.setText(f"Unstaged Files ({n_unstaged})")
        self._stage_all_button.setEnabled(n_unstaged > 0)
        self._unstage_all_button.setEnabled(n_staged > 0)
        self._discard_all_button.setEnabled(total_dirty > 0)
        self._highlight_selected_file()
        self._refresh_commit_button()

    def _on_unstaged_toggled(self, checked: bool) -> None:
        self._unstaged_list.setVisible(checked)
        self._unstaged_expander.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow,
        )

    def _on_staged_toggled(self, checked: bool) -> None:
        self._staged_list.setVisible(checked)
        self._staged_expander.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow,
        )

    def _on_summary_changed(self, text: str) -> None:
        self._vm.set_commit_summary(text)
        self._refresh_commit_button()

    def _on_description_changed(self) -> None:
        self._vm.set_commit_description(self._description.toPlainText())
        self._refresh_commit_button()

    def _on_summary_from_vm(self, text: str) -> None:
        if self._summary.text() == text:
            return
        self._summary.blockSignals(True)
        try:
            self._summary.setText(text)
        finally:
            self._summary.blockSignals(False)

    def _on_description_from_vm(self, text: str) -> None:
        if self._description.toPlainText() == text:
            return
        self._description.blockSignals(True)
        try:
            self._description.setPlainText(text)
        finally:
            self._description.blockSignals(False)

    # ----- UI -> VM ---------------------------------------------------

    def _on_discard_all_clicked(self) -> None:
        self._main_vm.discard_changes()

    def _on_stage_all_clicked(self) -> None:
        self._main_vm.stage_all_unstaged()

    def _on_stage_file(self, path: str) -> None:
        self._main_vm.stage_file(path)
        self._vm.select_file(None)

    def _on_unstage_file(self, path: str) -> None:
        self._main_vm.unstage_file(path)
        self._vm.select_file(None)

    def _on_unstaged_index_clicked(self, index: QModelIndex) -> None:
        change = index.data(FileChangeRole) if index.isValid() else None
        if change is None:
            return
        path = change.path
        current = self._vm.selected_file()
        if current == path:
            self._vm.select_file(None)
            self._unstaged_list.clearSelection()
            self._staged_list.clearSelection()
        else:
            self._vm.select_file(path)

    def _on_staged_index_clicked(self, index: QModelIndex) -> None:
        change = index.data(FileChangeRole) if index.isValid() else None
        if change is None:
            return
        path = change.path
        current = self._vm.selected_file()
        if current == path:
            self._vm.select_file(None)
            self._unstaged_list.clearSelection()
            self._staged_list.clearSelection()
        else:
            self._vm.select_file(path, staged=True)

    def _on_unstage_all_clicked(self) -> None:
        self._main_vm.unstage_all_staged()

    # ----- context menu handlers --------------------------------------

    def _on_unstaged_context_action(self, action: str, path: str) -> None:
        if action == "stage":
            self._main_vm.stage_file(path)
        elif action == "discard":
            self._main_vm.discard_file_changes(path)
        elif action == "ignore":
            self._main_vm.ignore_pattern(path)
        elif action == "ignore_dir":
            parent_dir = _os.path.dirname(path)
            if parent_dir:
                self._main_vm.ignore_pattern(parent_dir + "/")
        elif action == "ignore_parent_dir":
            parts = path.replace("\\", "/").split("/")
            if len(parts) >= 2:
                self._main_vm.ignore_pattern("/".join(parts[:-1]) + "/")
        elif action == "ignore_ext":
            _, ext = _os.path.splitext(path)
            if ext:
                self._main_vm.ignore_pattern("*" + ext)
        elif action == "stash":
            self._main_vm.stash_single_file(path)
        elif action == "show":
            self._main_vm.show_in_folder(path)
        elif action == "copy":
            self._main_vm.copy_file_path(path)
        elif action == "delete":
            self._main_vm.delete_file_from_disk(path)

    def _on_staged_context_action(self, action: str, path: str) -> None:
        if action == "unstage":
            self._main_vm.unstage_file(path)
        elif action == "discard":
            self._main_vm.discard_file_changes(path)
        elif action == "ignore":
            self._main_vm.ignore_pattern(path)
        elif action == "ignore_dir":
            parent_dir = _os.path.dirname(path)
            if parent_dir:
                self._main_vm.ignore_pattern(parent_dir + "/")
        elif action == "ignore_parent_dir":
            parts = path.replace("\\", "/").split("/")
            if len(parts) >= 2:
                self._main_vm.ignore_pattern("/".join(parts[:-1]) + "/")
        elif action == "ignore_ext":
            _, ext = _os.path.splitext(path)
            if ext:
                self._main_vm.ignore_pattern("*" + ext)
        elif action == "stash":
            self._main_vm.stash_single_file(path)
        elif action == "show":
            self._main_vm.show_in_folder(path)
        elif action == "copy":
            self._main_vm.copy_file_path(path)
        elif action == "delete":
            self._main_vm.delete_file_from_disk(path)

    def _on_selected_file_changed(self, path: str | None) -> None:
        self._highlight_selected_file()

    def _highlight_selected_file(self) -> None:
        selected = self._vm.selected_file()
        for list_view in (self._unstaged_list, self._staged_list):
            sel_model = list_view.selectionModel()
            if sel_model is None:
                continue
            sel_model.clearSelection()
            if selected is not None:
                model = list_view.model()
                for row in range(model.rowCount()):
                    change = model.change_at(row)
                    if change is not None and change.path == selected:
                        idx = model.index(row, 0)
                        sel_model.select(
                            idx,
                            QItemSelectionModel.SelectionFlag.Select,
                        )
                        return

    def _on_commit_clicked(self) -> None:
        message = self._vm.combined_commit_message()
        if not message.strip():
            return
        if not self._vm.staged_files():
            return
        self._main_vm.commit_changes(message)

    def _refresh_commit_button(self) -> None:
        staged_count = len(self._vm.staged_files())
        has_input = self._vm.has_commit_input()
        label = (
            f"Commit Changes to {staged_count} File"
            if staged_count == 1
            else f"Commit Changes to {staged_count} Files"
        )
        self._commit_button.setText(label)
        self._commit_button.setEnabled(has_input and staged_count > 0)


# ---------------------------------------------------------------------------
# File list view — thin QListView wrapper
# ---------------------------------------------------------------------------


class FileListView(QListView):
    """Thin wrapper around ``QListView`` that owns the model and delegate.

    Replaces the old ``FileListWidget`` (``QListWidget`` + per-row
    ``QWidget``) with a pure data model and ``QPainter``-based delegate.
    Only visible rows are rendered, making it fast even with 28 000 files.

    Provides a right-click context menu with actions forwarded through
    :attr:`context_action_requested`.
    """

    stage_file_requested = Signal(str)
    """Forwarded from :class:`FileListDelegate`."""

    context_action_requested = Signal(str, str)
    """Emitted with ``(action, path)`` when a context-menu item is chosen."""

    def __init__(self, *, staged: bool, parent=None) -> None:
        super().__init__(parent)
        self._staged = staged
        self._model = FileListModel(self)
        self._delegate = FileListDelegate(staged, self)
        self.setModel(self._model)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor("#1E1E1E"))
        self.setPalette(p)
        self.viewport().setAutoFillBackground(False)
        self.entered.connect(self._on_entered)
        self._delegate.stage_file_requested.connect(self.stage_file_requested)
        self.viewport().setMouseTracking(True)

    # -- public API -------------------------------------------------------

    def populate(self, changes: list[FileChange]) -> None:
        """Replace all rows with the supplied *changes*."""
        self._model.set_changes(changes)

    def count(self) -> int:
        return self._model.count()

    def model(self) -> FileListModel:
        return self._model

    # -- context menu -----------------------------------------------------

    def _on_context_menu(self, position: QPoint) -> None:
        index = self.indexAt(position)
        if not index.isValid():
            return
        change = index.data(FileChangeRole)
        if change is None:
            return
        path = change.path

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #2D2D30; color: #D4D4D4; border: 1px solid #3F3F46; "
            "padding: 4px 0; } "
            "QMenu::item { padding: 6px 24px 6px 12px; } "
            "QMenu::item:selected { background-color: #094771; } "
            "QMenu::separator { height: 1px; background: #3F3F46; margin: 3px 8px; }",
        )

        if self._staged:
            stage_action = menu.addAction("Unstage")
            stage_action.triggered.connect(
                lambda checked=False, p=path: self.context_action_requested.emit("unstage", p),
            )
        else:
            stage_action = menu.addAction("Stage")
            stage_action.triggered.connect(
                lambda checked=False, p=path: self.context_action_requested.emit("stage", p),
            )

        discard_action = menu.addAction("Discard Changes")
        discard_action.triggered.connect(
            lambda checked=False, p=path: self.context_action_requested.emit("discard", p),
        )

        # --- Ignore submenu ---
        ignore_menu = menu.addMenu("Ignore")
        ignore_menu.setStyleSheet(menu.styleSheet())

        ignore_file = ignore_menu.addAction(f"Ignore this file ({path})")
        ignore_file.triggered.connect(
            lambda checked=False, p=path: self.context_action_requested.emit("ignore", p),
        )

        # Directory-based ignore options — show up to 2 parent levels.
        normalized = path.replace("\\", "/")
        parts = normalized.split("/")

        if len(parts) >= 2:
            parent_dir = "/".join(parts[:-1]) + "/"
            ignore_parent = ignore_menu.addAction(f"Ignore /{parent_dir}")
            ignore_parent.triggered.connect(
                lambda checked=False, p=path: self.context_action_requested.emit("ignore_dir", p),
            )

        if len(parts) >= 3:
            grandparent_dir = "/".join(parts[:-2]) + "/"
            ignore_grandparent = ignore_menu.addAction(f"Ignore /{grandparent_dir}")
            ignore_grandparent.triggered.connect(
                lambda checked=False, p=path: self.context_action_requested.emit(
                    "ignore_parent_dir", p,
                ),
            )

        _, ext = _os.path.splitext(path)
        if ext:
            ignore_ext = ignore_menu.addAction(f"Ignore *{ext}")
            ignore_ext.triggered.connect(
                lambda checked=False, p=path: self.context_action_requested.emit("ignore_ext", p),
            )

        menu.addSeparator()

        stash_action = menu.addAction("Stash File")
        stash_action.triggered.connect(
            lambda checked=False, p=path: self.context_action_requested.emit("stash", p),
        )

        menu.addSeparator()

        show_action = menu.addAction("Show in Folder")
        show_action.triggered.connect(
            lambda checked=False, p=path: self.context_action_requested.emit("show", p),
        )

        copy_action = menu.addAction("Copy File Path")
        copy_action.triggered.connect(
            lambda checked=False, p=path: self.context_action_requested.emit("copy", p),
        )

        menu.addSeparator()

        delete_action = menu.addAction("Delete File")
        delete_action.triggered.connect(
            lambda checked=False, p=path: self.context_action_requested.emit("delete", p),
        )

        menu.exec(self.viewport().mapToGlobal(position))

    # -- hover forwarding to delegate -------------------------------------

    def _on_entered(self, index: QModelIndex) -> None:
        self._delegate.set_hovered_index(index)
        self.viewport().update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        index = self.indexAt(event.pos())
        if index.isValid():
            item_rect = self.visualRect(index)
            btn_rect = self._button_rect(item_rect)
            over = btn_rect.contains(event.pos())
            self._delegate.set_button_row(index.row() if over else None)
        else:
            self._delegate.set_button_row(None)
        self.viewport().update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._delegate.set_hovered_index(QModelIndex())
        self._delegate.set_button_row(None)
        self.viewport().update()

    @staticmethod
    def _button_rect(item_rect):
        m = FileListDelegate.MARGIN
        bs = FileListDelegate.BUTTON_SIZE
        rh = FileListDelegate.ROW_HEIGHT
        btn_x = item_rect.right() - m - bs
        btn_y = item_rect.top() + (rh - bs) // 2
        return QRect(btn_x, btn_y, bs, bs)


__all__ = ["CommitPanel", "FileListView"]
