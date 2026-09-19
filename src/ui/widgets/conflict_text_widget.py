"""Conflict rows with the same hover gutter as staged/unstaged diffs."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
from PySide6.QtWidgets import QTextEdit

from src.core.diff_parser import DiffLineType, ParsedDiffLine
from src.ui.widgets.diff_view_widget import (
    ADDITION_BG,
    DELETION_BG,
    HUNK_BG,
    DiffLineActionMode,
    _DiffEditor,
)
from src.viewmodels.conflict_editor_viewmodel import ConflictRow


class ConflictTextWidget(_DiffEditor):
    row_action_requested = Signal(object)

    def __init__(self, side: str, parent=None) -> None:
        super().__init__(parent)
        self._side = side
        self._rows: list[ConflictRow] = []
        self.setReadOnly(side != "result")
        self.set_line_action_mode(DiffLineActionMode.UNSTAGE if side == "result"
                                  else DiffLineActionMode.STAGE)
        self.setAccessibleName({"ours": "Целевая версия", "theirs": "Вливаемая версия",
                                "result": "Результат слияния"}[side])
        self._line_number_area.setToolTip(
            "Убрать строку из результата" if side == "result" else "Добавить строку в результат"
        )

    def is_actionable_block(self, block_number: int | None) -> bool:
        if block_number is None or not 0 <= block_number < len(self._rows):
            return False
        row = self._rows[block_number]
        return row.token is not None and (self._side == "result" or not row.selected)

    def request_line_action(self, block_number: int) -> None:
        if self.is_actionable_block(block_number):
            self.row_action_requested.emit(self._rows[block_number].token)

    def set_rows(self, rows: list[ConflictRow], text: str) -> None:
        self._rows = rows
        if self.toPlainText() != text:
            vertical = self.verticalScrollBar().value()
            horizontal = self.horizontalScrollBar().value()
            self.setPlainText(text)
            self.verticalScrollBar().setValue(vertical)
            self.horizontalScrollBar().setValue(horizontal)
        infos = [ParsedDiffLine(
            DiffLineType.ADDITION if row.changed else DiffLineType.CONTEXT,
            row.text, row.number,
        ) for row in rows]
        self.set_line_info(infos)
        self.update_diff_markers(infos)
        selections = []
        for i, row in enumerate(rows):
            block = self.document().findBlockByNumber(i)
            if not block.isValid() or not row.changed:
                continue
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(block)
            colour = (ADDITION_BG if row.selected else
                      DELETION_BG if self._side == "ours" else HUNK_BG)
            selection.format.setBackground(colour)
            selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
            selections.append(selection)
            for start, end in row.emphasis:
                if end <= start:
                    continue
                emphasis = QTextEdit.ExtraSelection()
                emphasis.cursor = QTextCursor(block)
                utf16_start = len(row.text[:start].encode("utf-16-le")) // 2
                utf16_end = len(row.text[:end].encode("utf-16-le")) // 2
                emphasis.cursor.setPosition(block.position() + utf16_start)
                emphasis.cursor.setPosition(block.position() + utf16_end,
                                            QTextCursor.MoveMode.KeepAnchor)
                emphasis.format.setBackground(QColor(colour).lighter(180))
                selections.append(emphasis)
        self.setExtraSelections(selections)
