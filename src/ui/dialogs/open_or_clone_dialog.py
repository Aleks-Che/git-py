"""Dialog offering to open or clone a repository.

Shown when the user clicks the ``+`` tab in the repo bar.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class OpenOrCloneDialog(QDialog):
    """Modal dialog: ``Open Repository`` / ``Clone Repository``.

    The caller checks which button was pressed via the return value:
    ``QDialog.Accepted`` with ``self.result()`` set to ``"open"`` or
    ``"clone"``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Repository")
        self.resize(420, 200)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        label = QLabel("Choose how to add a repository:")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        self._open_btn = QPushButton("Open existing Repository")
        self._open_btn.setMinimumHeight(44)
        self._open_btn.clicked.connect(lambda: self.done(1))
        layout.addWidget(self._open_btn)

        self._clone_btn = QPushButton("Clone Repository")
        self._clone_btn.setMinimumHeight(44)
        self._clone_btn.clicked.connect(lambda: self.done(2))
        layout.addWidget(self._clone_btn)

        layout.addStretch()

        self._cancel_btn = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._cancel_btn.rejected.connect(self.reject)
        layout.addWidget(self._cancel_btn)

    def choice(self) -> str | None:
        """Return ``"open"``, ``"clone"``, or ``None``."""
        code = self.exec()
        if code == 1:
            return "open"
        if code == 2:
            return "clone"
        return None


__all__ = ["OpenOrCloneDialog"]
