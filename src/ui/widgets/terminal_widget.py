"""Embedded terminal widget.

Stage 0 stub: a read-only ``QPlainTextEdit`` with monospaced font. The
real implementation (``QProcess``-backed shell launched in the repo
root, colour scheme synced with the active theme) lands in Stage 7.
"""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit


class TerminalWidget(QPlainTextEdit):
    """Placeholder read-only terminal pane."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Terminal (stub)")
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setMaximumBlockCount(1000)
