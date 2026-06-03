"""Commit panel: file list with checkboxes, message field, commit button.

The widget is a passive view: it reads state from
:class:`src.viewmodels.commit_panel_viewmodel.CommitPanelViewModel`
(via the parent :class:`MainViewModel`) and forwards user actions by
calling ViewModel methods. It does **not** hold any Git state and does
**not** know about ``pygit2``.

Layout (vertical ``QVBoxLayout``):

* **Message field** — single ``QPlainTextEdit`` (plain text is friendlier
  for commit messages than rich text). The Commit button sits to its
  right.
* **File list** — ``QListWidget`` with a custom item per file. Each
  row carries a checkable ``QListWidgetItem`` (toggles the staged
  state), a single-letter status (``M`` / ``U`` / ``D`` / ``R`` / ``A``
  / ``C`` / ``T``) colour-coded by status, and the path. Clicking the
  row body (not the checkbox) calls ``select_file`` on the ViewModel.
* **Diff preview** — read-only ``QTextEdit`` showing the unified diff
  for the currently selected file.

Stage 3 commit button behaviour: clicking it reads the message from
the text field and calls :meth:`MainViewModel.commit_changes`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.models import FileStatus
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel

# Short status letter + colour for the file list. Keys match
# :class:`FileStatus` values.
_STATUS_BADGE: dict[FileStatus, tuple[str, str]] = {
    FileStatus.NEW: ("A", "#43BCCD"),         # cyan
    FileStatus.MODIFIED: ("M", "#F5B947"),   # amber
    FileStatus.DELETED: ("D", "#E8685A"),    # red
    FileStatus.RENAMED: ("R", "#5B8FF9"),    # blue
    FileStatus.COPIED: ("C", "#A371F7"),     # violet
    FileStatus.UNTRACKED: ("U", "#3FB950"),  # green
    FileStatus.TYPE_CHANGED: ("T", "#F0883E"),  # orange
    FileStatus.CONFLICTED: ("!", "#FF6B6B"),
    FileStatus.IGNORED: ("I", "#8B8B8B"),
}


class CommitPanel(QWidget):
    """WIP / commit panel bound to a :class:`MainViewModel`."""

    def __init__(self, view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._main_vm = view_model
        self._vm: CommitPanelViewModel = view_model.commit_panel_view_model()

        self._build_ui()
        self._wire_signals()
        self._vm.set_repository(view_model.repository_manager())

    # ----- construction -----------------------------------------------

    def _build_ui(self) -> None:
        # --- top row: message + commit button ---
        self._message = QPlainTextEdit(self)
        self._message.setPlaceholderText("Commit message (required)")
        self._message.setMaximumHeight(80)
        self._message.setTabChangesFocus(True)

        self._commit_button = QPushButton("Commit", self)
        self._commit_button.setEnabled(False)
        self._commit_button.setFixedWidth(96)

        top_row = QHBoxLayout()
        top_row.addWidget(self._message, stretch=1)
        top_row.addWidget(self._commit_button, alignment=Qt.AlignmentFlag.AlignBottom)

        # --- middle: file list ---
        self._files_header = QLabel("Files (0)", self)
        self._files_header.setStyleSheet("font-weight: bold; padding: 4px;")

        self._files = QListWidget(self)
        self._files.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._files.setUniformItemSizes(True)
        self._files.setAlternatingRowColors(True)

        # --- bottom: diff preview ---
        diff_header = QLabel("Diff preview", self)
        diff_header.setStyleSheet("font-weight: bold; padding: 4px;")

        self._diff = QTextEdit(self)
        self._diff.setReadOnly(True)
        self._diff.setPlaceholderText("Select a file to see its diff.")
        self._diff.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._diff.setStyleSheet("font-family: Consolas, monospace; font-size: 11pt;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top_row)
        layout.addWidget(self._files_header)
        layout.addWidget(self._files, stretch=2)
        layout.addWidget(diff_header)
        layout.addWidget(self._diff, stretch=3)

    def _wire_signals(self) -> None:
        self._commit_button.clicked.connect(self._on_commit_clicked)
        self._message.textChanged.connect(self._on_message_changed)
        self._files.itemChanged.connect(self._on_item_changed)
        self._files.currentItemChanged.connect(self._on_current_item_changed)

        self._vm.file_changes_changed.connect(self._on_file_changes_changed)
        self._vm.staged_files_changed.connect(self._on_staged_files_changed)
        self._vm.selected_file_changed.connect(self._on_selected_file_changed)
        self._vm.diff_ready.connect(self._on_diff_ready)
        self._vm.commit_message_changed.connect(self._on_commit_message_changed)

    # ----- signal handlers (UI -> VM) ---------------------------------

    def _on_commit_clicked(self) -> None:
        text = self._message.toPlainText().strip()
        if not text:
            return
        self._main_vm.commit_changes(self._message.toPlainText())

    def _on_message_changed(self) -> None:
        text = self._message.toPlainText()
        self._vm.set_commit_message(text)
        self._refresh_commit_button()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Toggle staging/unstaging when the user flips a checkbox."""
        if item is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if path is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._main_vm.stage_file(path)
        else:
            self._main_vm.unstage_file(path)

    def _on_current_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            self._vm.select_file(None)
            return
        path = current.data(Qt.ItemDataRole.UserRole)
        self._vm.select_file(path)

    # ----- signal handlers (VM -> UI) ---------------------------------

    def _on_file_changes_changed(self) -> None:
        """Rebuild the file list to match the current ``file_changes``."""
        self._files.blockSignals(True)
        try:
            self._files.clear()
            for change in self._vm.file_changes():
                self._append_file_item(change)
        finally:
            self._files.blockSignals(False)
        self._refresh_files_header()

    def _on_staged_files_changed(self, staged: list[str]) -> None:
        """Sync the checkboxes to the new ``staged_files`` set."""
        staged_set = set(staged)
        self._files.blockSignals(True)
        try:
            for i in range(self._files.count()):
                item = self._files.item(i)
                path = item.data(Qt.ItemDataRole.UserRole)
                item.setCheckState(
                    Qt.CheckState.Checked if path in staged_set else Qt.CheckState.Unchecked,
                )
        finally:
            self._files.blockSignals(False)
        self._refresh_files_header()

    def _on_selected_file_changed(self, _path: str | None) -> None:
        """Selection state is reflected in the file list; the diff is
        updated separately by :meth:`_on_diff_ready`."""

    def _on_diff_ready(self, text: str) -> None:
        self._diff.setPlainText(text)
        cursor = self._diff.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self._diff.setTextCursor(cursor)

    def _on_commit_message_changed(self, text: str) -> None:
        """Sync the editor to programmatic message changes (e.g. clear after commit)."""
        if self._message.toPlainText() == text:
            return
        self._message.blockSignals(True)
        try:
            self._message.setPlainText(text)
        finally:
            self._message.blockSignals(False)
        self._refresh_commit_button()

    # ----- helpers ----------------------------------------------------

    def _append_file_item(self, change) -> None:  # noqa: ANN001 - FileChange dataclass
        badge, color_hex = _STATUS_BADGE.get(change.status, ("?", "#8B8B8B"))
        label = f"[{badge}]  {change.path}"
        item = QListWidgetItem(label, self._files)
        item.setData(Qt.ItemDataRole.UserRole, change.path)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        staged = self._vm.staged_files()
        item.setCheckState(
            Qt.CheckState.Checked if change.path in staged else Qt.CheckState.Unchecked,
        )
        # Tint the status badge by recolouring the whole row's foreground.
        item.setForeground(QBrush(QColor(color_hex)))

    def _refresh_files_header(self) -> None:
        n = self._files.count()
        staged = sum(1 for p in self._vm.staged_files() if p in {
            self._files.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self._files.count())
        })
        self._files_header.setText(f"Files ({n}) — {staged} staged")

    def _refresh_commit_button(self) -> None:
        has_message = bool(self._message.toPlainText().strip())
        has_staged = any(
            self._files.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self._files.count())
        )
        self._commit_button.setEnabled(has_message and has_staged)


__all__ = ["CommitPanel"]
