"""Commit panel: file list, message field, commit button.

Stage 0 stub. The real implementation (file rows with checkboxes, diff
preview, wired to ``CommitPanelViewModel``) lands in Stage 3.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QListWidget,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class CommitPanel(QWidget):
    """Placeholder panel with a file list, message field, and disabled commit button."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._files = QListWidget(self)
        self._message = QTextEdit(self)
        self._message.setPlaceholderText("Commit message (required)")
        self._commit_button = QPushButton("Commit", self)
        self._commit_button.setEnabled(False)

        form = QFormLayout()
        form.addRow("Message:", self._message)

        layout = QVBoxLayout(self)
        layout.addWidget(self._files)
        layout.addLayout(form)
        layout.addWidget(self._commit_button, alignment=Qt.AlignmentFlag.AlignRight)
