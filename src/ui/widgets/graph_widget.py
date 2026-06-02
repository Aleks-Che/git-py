"""Commit graph widget: renders the DAG of commits as a custom Qt scene.

Stage 0 stub. The real implementation (``QGraphicsView`` with custom
nodes/lanes, click-to-select, drag-and-drop for merge/rebase) lands in
Stage 2.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsScene, QGraphicsTextItem, QGraphicsView


class GraphWidget(QGraphicsView):
    """Placeholder view showing a centred 'open a repository' message."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)
        self._placeholder = QGraphicsTextItem("Open a repository to see the graph")
        self._placeholder.setDefaultTextColor(Qt.GlobalColor.lightGray)
        self._scene.addItem(self._placeholder)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        rect = self.viewport().rect()
        bounds = self._placeholder.boundingRect()
        self._placeholder.setPos(
            (rect.width() - bounds.width()) / 2,
            (rect.height() - bounds.height()) / 2,
        )
