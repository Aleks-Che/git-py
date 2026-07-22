"""Read-only log panel displaying timestamped operation history.

Connected to :attr:`MainViewModel.log_message` and
:attr:`MainViewModel.error_occurred` so every Git operation is
auto-recorded. Users can watch the log in real time to diagnose
unexpected behaviour.
"""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit


class LogWidget(QPlainTextEdit):
    """Scrollable, read-only log pane fed by the ViewModel's log signal."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Operation log")
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        self.setMaximumBlockCount(2000)

    def append_log(self, line: str) -> None:
        """Append a single pre-formatted log line to the pane."""
        self.appendPlainText(line)


__all__ = ["LogWidget"]
