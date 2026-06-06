"""Colour-coded diff view with line-number gutter.

Replaces the plain ``QPlainTextEdit`` that was used through Stage 3-8 as
a temporary diff viewer. Uses :func:`src.core.diff_parser.parse_diff_lines`
to classify each line and paints backgrounds accordingly:

* Additions  — green-tinted background
* Deletions  — red-tinted background
* Hunk headers (``@@ … @@``) — cyan-tinted background
* File headers (``diff --git``, ``---``, ``+++``, …) — blue-tinted background
* Context / empty lines — transparent (the editor background shows through)

A gutter on the left draws sequential line numbers. The widget is
read-only and has no file-io or Git logic — it only receives a diff
string via :meth:`set_diff`.
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from src.core.diff_parser import DiffLineType, parse_diff_lines

# ── diff colour palette ───────────────────────────────────────────────
ADDITION_BG = QColor(26, 64, 32)
DELETION_BG = QColor(64, 32, 32)
HEADER_BG = QColor(26, 45, 64)
HUNK_BG = QColor(26, 48, 64)
GUTTER_BG = QColor(21, 21, 21)
GUTTER_FG = QColor(90, 90, 90)
ADDITION_FG = QColor(172, 229, 172)
DELETION_FG = QColor(229, 172, 172)
HEADER_FG = QColor(130, 170, 220)
HUNK_FG = QColor(140, 190, 210)
# ──────────────────────────────────────────────────────────────────────

ExtraSelection = QTextEdit.ExtraSelection


class _LineNumberArea(QWidget):
    """Left gutter that paints sequential line numbers."""

    def __init__(self, editor: _DiffEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(event.rect(), GUTTER_BG)
        block = self._editor.firstVisibleBlock()
        block_num = block.blockNumber()
        top = round(
            self._editor.blockBoundingGeometry(block)
            .translated(self._editor.contentOffset())
            .top()
        )
        bottom = top + round(self._editor.blockBoundingRect(block).height())
        fm = self._editor.fontMetrics()
        w = self.width() - fm.horizontalAdvance("9")
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(GUTTER_FG)
                painter.drawText(
                    0, top, w, fm.height(), Qt.AlignmentFlag.AlignRight, str(block_num + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self._editor.blockBoundingRect(block).height())
            block_num += 1
        painter.end()


class _DiffEditor(QPlainTextEdit):
    """Internal plain-text editor that collaborates with the gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFont("Consolas", 10))
        self.setTabStopDistance(40)
        self._line_number_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_line_number_area)

    def gutter_width(self) -> int:
        digits = max(1, len(str(self.blockCount())))
        fm = self.fontMetrics()
        return fm.horizontalAdvance("9") * (digits + 3)

    def line_number_area_width(self) -> int:
        return self.gutter_width()

    # ── internal slots connected to signals ───────────────────────

    def _update_gutter_width(self, _new_block_count: int = 0) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(
                0, rect.y(), self._line_number_area.width(), rect.height(),
            )
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_number_area.setGeometry(
            cr.left(), cr.top(), self.gutter_width(), cr.height(),
        )


class DiffViewWidget(QWidget):
    """Colour-coded, read-only diff viewer with a line-number gutter.

    Usage
    -----
    >>> view = DiffViewWidget(parent)
    >>> view.set_diff(diff_text)
    >>> view.clear()
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editor = _DiffEditor(self)

    # ── public API ────────────────────────────────────────────────

    def set_diff(self, text: str) -> None:
        """Set the diff content and apply line-level colour coding."""
        self._editor.setPlainText(text)
        self._apply_highlighting(text)

    def clear(self) -> None:
        """Remove all content and highlights."""
        self._editor.clear()
        self._editor.setExtraSelections([])

    def toPlainText(self) -> str:  # noqa: N802
        return self._editor.toPlainText()

    def isVisible(self) -> bool:  # noqa: N802
        return super().isVisible()

    def setVisible(self, visible: bool) -> None:  # noqa: N802
        super().setVisible(visible)

    # ── layout ────────────────────────────────────────────────────

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._editor.setGeometry(self.contentsRect())

    # ── highlighting ──────────────────────────────────────────────

    def _apply_highlighting(self, text: str) -> None:
        """Parse *text* and colour-code every line."""
        doc: QTextDocument = self._editor.document()
        selections: list[ExtraSelection] = []
        lines = parse_diff_lines(text)
        for idx, (line_type, _line) in enumerate(lines):
            bg, fg = _colours_for(line_type)
            if bg is None and fg is None:
                continue
            block = doc.findBlockByLineNumber(idx)
            if not block.isValid():
                continue
            cursor = QTextCursor(block)
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            cursor.movePosition(
                QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor,
            )
            fmt = QTextCharFormat()
            if bg is not None:
                fmt.setBackground(bg)
            if fg is not None:
                fmt.setForeground(fg)
            es = ExtraSelection()
            es.cursor = cursor
            es.format = fmt
            selections.append(es)
        self._editor.setExtraSelections(selections)


# ── helpers ───────────────────────────────────────────────────────────


def _colours_for(line_type: DiffLineType) -> tuple[QColor | None, QColor | None]:
    return _TYPE_COLOURS.get(line_type, (None, None))


_TYPE_COLOURS: dict[DiffLineType, tuple[QColor | None, QColor | None]] = {
    DiffLineType.ADDITION: (ADDITION_BG, ADDITION_FG),
    DiffLineType.DELETION: (DELETION_BG, DELETION_FG),
    DiffLineType.HEADER: (HEADER_BG, HEADER_FG),
    DiffLineType.HUNK: (HUNK_BG, HUNK_FG),
    DiffLineType.CONTEXT: (None, None),
    DiffLineType.EMPTY: (None, None),
}


__all__ = ["DiffViewWidget"]
