"""Right-panel commit-input view: WIP / staging area for the next commit.

Shown when the user clicks the WIP node in the graph (or otherwise
selects the WIP state). Structure (top-to-bottom):

* **Unstaged Files (N)** — collapsible list, with a green *Stage All
  Changes* button on the right of the header. Each row shows the
  file's status badge and path; hovering the row reveals a green
  *Stage File* button on the right.
* **Staged Files (N)** — collapsible list. Each row shows the badge
  and path (no stage button — the row is already staged). The user
  can unstage a file by clicking its row (routed to
  :meth:`MainViewModel.unstage_file`).
* **Commit block** — sticky at the bottom:
    * Commit Summary (single-line ``QLineEdit``)
    * Description (multi-line ``QPlainTextEdit``)
    * Green *Commit Changes to (N) File(s)* button, enabled once at
      least one of the two fields is non-empty **and** there is at
      least one staged file.

The widget is bound to :class:`MainViewModel` for verbs
(``stage_file`` / ``unstage_file`` / ``stage_all_unstaged`` /
``commit_changes``) and to :class:`CommitPanelViewModel` for state
(staged set, commit message, file list). It is a passive view: it
never holds Git state and never calls ``pygit2`` directly.
"""
from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, QModelIndex, QRect, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
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

# Short status letter + colour shared with :class:`CommitDetailPanel`
# so the visual vocabulary is consistent across the two right-panel
# views.
_GREEN = "#3FB950"
_GREEN_HOVER = "#46C75A"
_GREEN_PRESSED = "#2F8B3B"
_RED = "#E8685A"
_RED_HOVER = "#ED7A6E"
_RED_PRESSED = "#C04D40"


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
        # The expander and the label are visually one row; we install
        # the expander as the actual clickable target but keep the
        # label updated with the current count.
        self._unstaged_header.setVisible(False)

        self._stage_all_button = QPushButton("Stage All Changes", self)
        self._stage_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stage_all_button.setStyleSheet(
            f"QPushButton {{ background-color: { _GREEN_PRESSED }; "
            f"color: white; border: none; border-radius: 3px; padding: 4px 10px; "
            f"font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: { _GREEN_HOVER }; }} "
            f"QPushButton:pressed {{ background-color: { _GREEN_PRESSED }; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._stage_all_button.setEnabled(False)
        self._stage_all_button.clicked.connect(self._on_stage_all_clicked)

        unstaged_header_row = QHBoxLayout()
        unstaged_header_row.setContentsMargins(0, 0, 0, 0)
        unstaged_header_row.setSpacing(8)
        # Use the QToolButton as the visible "Unstaged Files" header
        # (clickable, with the arrow indicator). Push the Stage All
        # button to the right.
        self._unstaged_expander.setSizePolicy(
            self._unstaged_expander.sizePolicy().horizontalPolicy(),
            self._unstaged_expander.sizePolicy().verticalPolicy(),
        )
        unstaged_header_row.addWidget(self._unstaged_expander, stretch=1)
        unstaged_header_row.addWidget(self._stage_all_button)

        self._unstaged_list = FileListView(staged=False, parent=self)
        self._unstaged_list.clicked.connect(self._on_unstaged_index_clicked)
        self._unstaged_list.stage_file_requested.connect(self._on_stage_file)

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
            f"QPushButton {{ background-color: { _RED }; "
            f"color: white; border: none; border-radius: 3px; padding: 4px 10px; "
            f"font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: { _RED_HOVER }; }} "
            f"QPushButton:pressed {{ background-color: { _RED_PRESSED }; }} "
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
            f"QPushButton {{ background-color: { _GREEN }; color: white; "
            f"border: none; border-radius: 3px; padding: 8px 14px; "
            f"font-weight: 600; font-size: 12px; }} "
            f"QPushButton:hover {{ background-color: { _GREEN_HOVER }; }} "
            f"QPushButton:pressed {{ background-color: { _GREEN_PRESSED }; }} "
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
        # Commit-button enabled state depends on the *combined* input
        # plus the staged set. We refresh the button whenever either
        # input field changes, in addition to the file-list refreshes
        # above. The summary/description field handlers call
        # ``_refresh_commit_button`` directly so we don't need a
        # separate signal connection here — the file-list refreshes
        # already call it.

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
        self._unstaged_expander.setText(f"  Unstaged Files ({n_unstaged})")
        self._staged_expander.setText(f"  Staged Files ({n_staged})")
        self._unstaged_header.setText(f"Unstaged Files ({n_unstaged})")
        self._stage_all_button.setEnabled(n_unstaged > 0)
        self._unstage_all_button.setEnabled(n_staged > 0)
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

    def _on_stage_all_clicked(self) -> None:
        self._main_vm.stage_all_unstaged()

    def _on_stage_file(self, path: str) -> None:
        self._main_vm.stage_file(path)
        self._vm.select_file(None)

    def _on_unstage_file(self, path: str) -> None:
        self._main_vm.unstage_file(path)
        self._vm.select_file(None)

    def _on_unstaged_index_clicked(self, index: QModelIndex) -> None:
        """Click on a row in the Unstaged list = show diff for that file.

        Clicking the same file again deselects it and returns the
        graph view. The *Stage File* hover button is the dedicated
        way to stage -- this click only previews the diff.
        """
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
        """Click on a row in the Staged list = show diff for that file.

        Clicking the same file again deselects it and returns the
        graph view. The *Unstage File* hover button is the dedicated
        way to unstage -- this click only previews the diff.
        """
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
    Only visible rows are rendered, making it fast even with 28 000 files.
    """

    stage_file_requested = Signal(str)
    """Forwarded from :class:`FileListDelegate`."""

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
