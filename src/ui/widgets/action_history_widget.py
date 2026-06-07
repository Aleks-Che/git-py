"""Action-history panel showing the undo/redo stack contents.

Stage 8: connects to :class:`CommandProcessor.stack_changed`, reads
:meth:`CommandProcessor.undo_stack_snapshot` and
:meth:`CommandProcessor.redo_stack_snapshot`, and displays them in a
two-section tree (Applied / Undone) with timestamps.

The widget is read-only; double-clicking an entry is reserved for
future use (navigate to the state before the action).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from src.utils.theme import DARK_THEME, Theme


class ActionHistoryWidget(QWidget):
    """Tree panel listing past and undone Git operations."""

    _SECTION_APPLIED = "Applied (can undo)"
    _SECTION_UNDONE = "Undone (can redo)"

    def __init__(
        self,
        theme: Theme | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme = theme or DARK_THEME
        self._processor = None

        self._tree = QTreeWidget(self)
        self._tree.setColumnCount(2)
        self._tree.setHeaderLabels(["Action", "Time"])
        self._tree.setRootIsDecorated(False)
        self._tree.setAlternatingRowColors(False)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self._tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._tree.setEditTriggers(QTreeWidget.EditTrigger.NoEditTriggers)
        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)

        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self._tree.setFont(font)

        self._applied_root = QTreeWidgetItem(self._tree, [self._SECTION_APPLIED])
        self._applied_root.setFlags(Qt.ItemFlag.NoItemFlags)
        self._applied_root.setForeground(0, QColor(self._theme.text_dim))
        bold = QFont(font)
        bold.setBold(True)
        self._applied_root.setFont(0, bold)

        self._undone_root = QTreeWidgetItem(self._tree, [self._SECTION_UNDONE])
        self._undone_root.setFlags(Qt.ItemFlag.NoItemFlags)
        self._undone_root.setForeground(0, QColor(self._theme.text_dim))
        self._undone_root.setFont(0, bold)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._tree)

    def set_processor(self, processor) -> None:
        """Bind to a :class:`CommandProcessor` and populate the tree."""
        if self._processor is not None:
            self._processor.stack_changed.disconnect(self._rebuild)
        self._processor = processor
        if processor is not None:
            processor.stack_changed.connect(self._rebuild)
        self._rebuild()

    def _rebuild(self) -> None:
        """Clear and re-populate from the processor's current stacks."""
        # Remove all child items (keep the section root items).
        for root in (self._applied_root, self._undone_root):
            while root.childCount():
                root.removeChild(root.child(0))

        if self._processor is None:
            self._tree.expandAll()
            return

        for entry in self._processor.undo_stack_snapshot():
            item = QTreeWidgetItem(self._applied_root)
            item.setText(0, str(entry.get("name", "")))
            item.setText(1, self._fmt_time(entry.get("timestamp")))

        for entry in self._processor.redo_stack_snapshot():
            item = QTreeWidgetItem(self._undone_root)
            item.setText(0, str(entry.get("name", "")))
            item.setText(1, self._fmt_time(entry.get("timestamp")))

        # Expand both sections so the user sees all items immediately.
        self._tree.expandAll()

    @staticmethod
    def _fmt_time(ts: object) -> str:
        if not isinstance(ts, int | float):
            return ""
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M:%S")


__all__ = ["ActionHistoryWidget"]
