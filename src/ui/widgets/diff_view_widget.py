"""Colour-coded diff view with line-number gutter.

Replaces the plain ``QPlainTextEdit`` that was used through Stage 3-8 as
a temporary diff viewer. Uses :func:`src.core.diff_parser.parse_diff_lines`
to classify each line and paints backgrounds accordingly:

* Additions  — green-tinted background
* Deletions  — red-tinted background
* Hunk headers (``@@ … @@``) — cyan-tinted background
* Context / empty lines — transparent (the editor background shows through)

A gutter on the left draws the **file** line number for each change —
additions and context use the new-file line, deletions use the
old-file line, both derived from the parsed hunk header. The user
sees "this addition lives on line 42 of the file" rather than a
sequential 1, 2, 3 … diff index.

File-level header lines (``diff --git``, ``index``, ``--- a/…``,
``+++ b/…``, ``old mode``, etc.) are not displayed: the viewer is
opened from a specific file, so the file header is redundant noise.

The widget is read-only and has no file-io or Git logic — it only
receives a diff string via :meth:`set_diff`.
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

from src.core.diff_parser import DiffLineType, ParsedDiffLine, parse_diff_lines

# ── diff colour palette ───────────────────────────────────────────────
ADDITION_BG = QColor(26, 64, 32)
DELETION_BG = QColor(64, 32, 32)
HUNK_BG = QColor(26, 48, 64)
GUTTER_BG = QColor(21, 21, 21)
GUTTER_FG = QColor(90, 90, 90)
ADDITION_FG = QColor(172, 229, 172)
DELETION_FG = QColor(229, 172, 172)
HUNK_FG = QColor(140, 190, 210)
# ──────────────────────────────────────────────────────────────────────

ExtraSelection = QTextEdit.ExtraSelection

# How many characters of padding to leave on the right edge of the
# gutter before the diff text starts. Mirrors the value the old
# implementation used (digits + 3).
_GUTTER_RIGHT_PADDING_CHARS = 3


class _LineNumberArea(QWidget):
    """Left gutter that paints the file line number for each block."""

    def __init__(self, editor: _DiffEditor) -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(event.rect(), GUTTER_BG)
        block = self._editor.firstVisibleBlock()
        top = round(
            self._editor.blockBoundingGeometry(block)
            .translated(self._editor.contentOffset())
            .top()
        )
        bottom = top + round(self._editor.blockBoundingRect(block).height())
        fm = self._editor.fontMetrics()
        w = self.width() - fm.horizontalAdvance("9")
        line_info = self._editor._line_info
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                block_num = block.blockNumber()
                line_number = (
                    line_info[block_num].line_number
                    if block_num < len(line_info)
                    else None
                )
                if line_number is not None:
                    painter.setPen(GUTTER_FG)
                    painter.drawText(
                        0,
                        top,
                        w,
                        fm.height(),
                        Qt.AlignmentFlag.AlignRight,
                        str(line_number),
                    )
            block = block.next()
            top = bottom
            bottom = top + round(self._editor.blockBoundingRect(block).height())
        painter.end()


class _DiffEditor(QPlainTextEdit):
    """Internal plain-text editor that collaborates with the gutter."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(QFont("Consolas", 10))
        self.setTabStopDistance(40)
        # Parallel to the editor's blocks: for each visible block we
        # remember its semantic type and (when applicable) the real
        # file line number, so the gutter can paint the file row
        # instead of a sequential index.
        self._line_info: list[ParsedDiffLine] = []
        self._line_number_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_line_number_area)

    def gutter_width(self) -> int:
        """Width required to fit the longest line number we display.

        Sized off the maximum file line number (not the visible block
        count) so a single-line change in a 1000-line file still gets
        a 4-digit gutter.
        """
        max_block = max(self.blockCount(), 1)
        max_line = max(
            (info.line_number for info in self._line_info if info.line_number),
            default=0,
        )
        digits = len(str(max(max_block, max_line)))
        digits = max(1, digits)
        fm = self.fontMetrics()
        return fm.horizontalAdvance("9") * (digits + _GUTTER_RIGHT_PADDING_CHARS)

    def line_number_area_width(self) -> int:
        return self.gutter_width()

    def set_line_info(self, line_info: list[ParsedDiffLine]) -> None:
        """Replace the parallel block-to-line mapping.

        Must be called whenever the editor's text changes so block N
        in the document still corresponds to the right
        :class:`ParsedDiffLine`.
        """
        self._line_info = list(line_info)
        # Recompute gutter width now that the max line number may
        # have changed; ``blockCountChanged`` only fires when the
        # number of blocks changes, not when their metadata does.
        self._update_gutter_width()

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

    File-level header lines (``diff --git`` / ``---`` / ``+++`` /
    ``index`` / mode lines) are not shown — the user picked this
    view from a specific file, so the header is redundant. The
    gutter paints the actual file line number for every change so
    the reader can see "this addition is on line 42 of the file"
    instead of a sequential diff index.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editor = _DiffEditor(self)

    # ── public API ────────────────────────────────────────────────

    def set_diff(self, text: str) -> None:
        """Set the diff content and apply line-level colour coding."""
        parsed = parse_diff_lines(text)
        # Drop file-level headers — they're noise in a per-file view.
        # Hunk markers, additions, deletions, context, and the
        # ``\\ No newline at end of file`` marker all stay.
        kept = [p for p in parsed if p.line_type != DiffLineType.HEADER]
        self._editor.set_line_info(kept)
        self._editor.setPlainText("\n".join(p.text for p in kept))
        self._apply_highlighting(kept)

    def clear(self) -> None:
        """Remove all content and highlights."""
        self._editor.clear()
        self._editor.set_line_info([])
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

    def _apply_highlighting(self, line_info: list[ParsedDiffLine]) -> None:
        """Paint backgrounds for each visible block based on *line_info*."""
        doc: QTextDocument = self._editor.document()
        selections: list[ExtraSelection] = []
        for idx, info in enumerate(line_info):
            bg, fg = _colours_for(info.line_type)
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
    DiffLineType.HUNK: (HUNK_BG, HUNK_FG),
    DiffLineType.CONTEXT: (None, None),
    DiffLineType.EMPTY: (None, None),
    # HEADER colour slots are intentionally absent — the widget
    # filters these lines out before they reach the document.
}


__all__ = ["DiffViewWidget"]
