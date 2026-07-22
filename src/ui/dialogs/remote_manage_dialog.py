"""``Manage Remotes`` dialog: list, add, and remove Git remotes.

A small modal dialog that shows the configured remotes in a table
and exposes two buttons:

* **Add…** — opens a sub-dialog (an inline :class:`QInputDialog` is
  used for the URL, and the remote name is prompted via another
  :class:`QInputDialog`) and emits :attr:`add_requested` with the
  chosen ``(name, url)`` pair.
* **Remove** — emits :attr:`remove_requested` with the selected
  remote's name.

The dialog itself never talks to ``pygit2`` — it only surfaces
user intent via signals. The caller (:class:`MainWindow`) routes
those into :meth:`MainViewModel.add_remote` /
:meth:`MainViewModel.remove_remote`.

Test helpers
------------
:meth:`set_remotes` populates the table from a list of
:class:`RemoteInfo` (no live repository needed), and :meth:`selected_remote`
returns the currently selected row's name (or ``None``).
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.models import RemoteInfo


class RemoteManageDialog(QDialog):
    """A small manager for the repository's remotes."""

    add_requested = Signal(str, str)  # (name, url)
    remove_requested = Signal(str)  # name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Remotes")
        self.resize(640, 320)

        layout = QVBoxLayout(self)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Name", "URL", "Fetch refspec"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self._table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection,
        )
        self._table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self._table)

        button_row = QHBoxLayout()
        self._add_btn = QPushButton("Add…")
        self._add_btn.clicked.connect(self._on_add)
        button_row.addWidget(self._add_btn)

        self._remove_btn = QPushButton("Remove")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._on_remove)
        button_row.addWidget(self._remove_btn)

        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    # ----- public API --------------------------------------------------

    def set_remotes(self, remotes: list[RemoteInfo]) -> None:
        """Replace the table contents with ``remotes``."""
        self._table.setRowCount(len(remotes))
        for row, remote in enumerate(remotes):
            self._table.setItem(row, 0, QTableWidgetItem(remote.name))
            self._table.setItem(row, 1, QTableWidgetItem(remote.url))
            self._table.setItem(row, 2, QTableWidgetItem(remote.fetch_refspec))
        self._table.resizeColumnsToContents()
        self._update_buttons()

    def selected_remote(self) -> str | None:
        """Return the name of the currently selected remote, or ``None``."""
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._table.item(rows[0].row(), 0)
        return item.text() if item else None

    # ----- internals ---------------------------------------------------

    def _update_buttons(self) -> None:
        self._remove_btn.setEnabled(self.selected_remote() is not None)

    def _on_add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Remote", "Remote name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        url, ok = QInputDialog.getText(
            self,
            "Add Remote",
            f"URL for remote {name!r}:",
        )
        if not ok or not url.strip():
            return
        self.add_requested.emit(name, url.strip())

    def _on_remove(self) -> None:
        name = self.selected_remote()
        if not name:
            return
        confirm = QMessageBox.question(
            self,
            "Remove Remote",
            f"Remove remote {name!r}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.remove_requested.emit(name)


__all__ = ["RemoteManageDialog"]
