"""Left references panel: local/remote branches, tags, stash.

Stage 0 stub. The real implementation (grouped ``QTreeWidget`` fed by
``BranchPanelViewModel``) lands in Stage 4.
"""
from __future__ import annotations

from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


class LeftPanel(QTreeWidget):
    """Placeholder tree showing a disabled 'No repository opened' item."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderLabel("References")
        placeholder = QTreeWidgetItem(["No repository opened"])
        placeholder.setDisabled(True)
        self.addTopLevelItem(placeholder)
