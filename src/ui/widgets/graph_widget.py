"""Commit graph widget: renders the DAG of commits as a custom Qt scene.

Stage 2 implementation. The widget subscribes to a
:class:`src.viewmodels.graph_viewmodel.GraphViewModel` and rebuilds
its :class:`QGraphicsScene` whenever the layout changes.

Rendering layout (per commit at row ``r``, lane ``l``):

* The node is an ellipse of radius :data:`NODE_RADIUS`, centred at
  ``(LANE_OFFSET + l * LANE_WIDTH, ROW_HEIGHT * r + ROW_HEIGHT/2)``.
* The commit subject and short SHA are drawn to the right of the
  node, starting at ``x = lane_centre + LABEL_OFFSET``.
* Ref labels (``HEAD``, branch names, tags) are drawn as small
  rounded chips above the subject line, in the commit's colour.
* Edges to parents are drawn as straight vertical lines when the
  child and parent share a lane, otherwise as an L-shaped path
  that drops down, jogs across at the row midpoint, and continues
  down to the parent.

Selection is purely visual: clicking a node calls
:meth:`GraphViewModel.select_commit`, which propagates as a Qt
signal. The widget keeps a local ``_selected_sha`` so it can
re-paint the selection ring when the layout is rebuilt.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from src.viewmodels.graph_viewmodel import GraphViewModel


@dataclass(frozen=True)
class RenderConfig:
    """Visual constants for the graph widget.

    Hard-coded for Stage 2; Stage 9 will move them into
    :mod:`src.utils.config`.
    """

    lane_width: int = 30
    lane_offset: int = 30
    row_height: int = 40
    node_radius: int = 8
    label_offset: int = 18
    ref_chip_height: int = 16
    ref_chip_padding: int = 6
    ref_chip_gap: int = 4
    background_color: str = "#1E1E1E"
    text_color: str = "#D4D4D4"
    dim_text_color: str = "#8B8B8B"
    selection_color: str = "#FFFFFF"
    edge_color: str = "#5A5A5A"
    edge_width: int = 2
    selection_ring_width: int = 2
    subject_max_chars: int = 60
    wip_color: str = "#8B8B8B"
    wip_node_radius: int = 7


class GraphWidget(QGraphicsView):
    """Renders a :class:`GraphViewModel`'s commit graph.

    The widget is intentionally passive: it reads
    ``graph_updated`` / ``commit_selected`` and forwards clicks via
    ``view_model.select_commit``. It does **not** query the
    repository on its own.
    """

    def __init__(self, view_model: GraphViewModel, parent=None) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._cfg = RenderConfig()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor(self._cfg.background_color))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._node_items: dict[str, QGraphicsEllipseItem] = {}
        self._selected_sha: str | None = None
        self._placeholder: QGraphicsSimpleTextItem | None = None

        self._view_model.graph_updated.connect(self._on_graph_updated)
        # Pull the current state (in case the ViewModel emitted
        # before the widget was wired up — common during tests).
        self._on_graph_updated([])

    # ----- public API ---------------------------------------------------

    def selected_sha(self) -> str | None:
        """Return the SHA of the currently highlighted commit, if any."""
        return self._selected_sha

    def set_selected_sha(self, sha: str | None) -> None:
        """Force a selection without going through the ViewModel click path.

        Useful for tests and for syncing the highlight when the
        :attr:`GraphViewModel.commit_selected` signal fires from
        elsewhere (e.g. keyboard navigation in Stage 4).
        """
        self._selected_sha = sha
        self._refresh_selection_rings()

    # ----- signal handlers ---------------------------------------------

    def _on_graph_updated(self, rows: list[dict]) -> None:
        """Rebuild the scene from the ViewModel's new layout payload."""
        self._scene.clear()
        self._node_items.clear()
        self._placeholder = None

        if not rows:
            self._draw_placeholder("No commits to display")
            return

        # Pass 1: connecting lines (so they sit underneath the nodes).
        row_by_sha: dict[str, dict] = {r["sha"]: r for r in rows}
        for row in rows:
            for parent_sha in row["parents"]:
                parent = row_by_sha.get(parent_sha)
                if parent is None:
                    continue
                self._draw_edge(
                    child=row,
                    parent=parent,
                )

        # Pass 2: nodes, labels, and ref chips.
        for row in rows:
            self._draw_commit(row)

        # Pass 3: extend scene rect to fit everything.
        rect = self._scene.itemsBoundingRect().adjusted(
            -self._cfg.lane_offset, -self._cfg.row_height,
            self._cfg.lane_offset, self._cfg.row_height,
        )
        self._scene.setSceneRect(rect)
        self._refresh_selection_rings()

    # ----- drawing helpers ---------------------------------------------

    def _draw_placeholder(self, message: str) -> None:
        text = QGraphicsSimpleTextItem(message)
        text.setBrush(QColor(self._cfg.dim_text_color))
        self._scene.addItem(text)
        self._placeholder = text
        rect = self.viewport().rect()
        text.setPos(
            (rect.width() - text.boundingRect().width()) / 2,
            (rect.height() - text.boundingRect().height()) / 2,
        )
        self._scene.setSceneRect(self._scene.itemsBoundingRect())

    def _row_y(self, row: int) -> float:
        return self._cfg.row_height * row + self._cfg.row_height / 2

    def _lane_x(self, lane: int) -> float:
        return self._cfg.lane_offset + lane * self._cfg.lane_width

    def _draw_commit(self, row: dict) -> None:
        x = self._lane_x(row["lane"])
        y = self._row_y(row["row"])
        is_wip = row["sha"] == "WIP"
        if is_wip:
            color = QColor(self._cfg.wip_color)
            radius = self._cfg.wip_node_radius
            pen = QPen(color, 1, Qt.PenStyle.DashLine)
            brush = QBrush(QColor(self._cfg.background_color))
        else:
            color = QColor(row["color"])
            radius = self._cfg.node_radius
            pen = QPen(QColor(self._cfg.background_color), 1)
            brush = QBrush(color)

        # Node ellipse.
        node = QGraphicsEllipseItem(
            x - radius,
            y - radius,
            radius * 2,
            radius * 2,
        )
        node.setBrush(brush)
        node.setPen(pen)
        node.setData(0, row["sha"])  # tag the item with its SHA for hit-testing
        node.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scene.addItem(node)
        self._node_items[row["sha"]] = node

        # Ref chips (HEAD / branch / tag) — drawn just above the subject.
        label_x = x + self._cfg.label_offset
        chip_y = y - self._cfg.ref_chip_height / 2 - 2
        for ref_label in row["refs"]:
            chip = self._draw_ref_chip(ref_label, label_x, chip_y, color)
            label_x += chip.rect().width() + self._cfg.ref_chip_gap

        # Subject line and short SHA.
        text_x = max(
            x + self._cfg.label_offset,
            label_x + (0 if row["refs"] else 0),
        )
        subject = row["subject"]
        if len(subject) > self._cfg.subject_max_chars:
            subject = subject[: self._cfg.subject_max_chars - 1] + "…"
        subject_item = QGraphicsSimpleTextItem(subject)
        subject_color = QColor(self._cfg.dim_text_color) if is_wip else QColor(self._cfg.text_color)
        subject_item.setBrush(subject_color)
        subject_item.setFont(self.font())
        subject_item.setPos(text_x, y - subject_item.boundingRect().height() / 2)
        self._scene.addItem(subject_item)

        sha_item = QGraphicsSimpleTextItem(row["short_sha"])
        sha_item.setBrush(QColor(self._cfg.dim_text_color))
        sha_item.setFont(self.font())
        sha_item.setPos(text_x, y + 4)
        self._scene.addItem(sha_item)

    def _draw_ref_chip(
        self, label: str, x: float, y: float, color: QColor,
    ) -> QGraphicsRectItem:
        text = QGraphicsSimpleTextItem(label)
        text.setBrush(QColor(self._cfg.background_color))
        text.setFont(self.font())
        # First lay out the text to measure it, then place the chip.
        chip_padding = self._cfg.ref_chip_padding
        chip_height = self._cfg.ref_chip_height
        width = text.boundingRect().width() + chip_padding * 2
        chip = QGraphicsRectItem(QRectF(x, y, width, chip_height))
        chip.setBrush(QBrush(color))
        chip.setPen(QPen(color, 0))
        self._scene.addItem(chip)
        text.setParentItem(chip)
        text.setPos(
            (width - text.boundingRect().width()) / 2,
            (chip_height - text.boundingRect().height()) / 2,
        )
        return chip

    def _draw_edge(self, child: dict, parent: dict) -> None:
        cx, cy = self._lane_x(child["lane"]), self._row_y(child["row"])
        px, py = self._lane_x(parent["lane"]), self._row_y(parent["row"])
        r = self._cfg.node_radius
        if abs(cx - px) < 0.5:
            # Straight vertical: from below the child to above the parent.
            path = QPainterPath()
            path.moveTo(cx, cy + r)
            path.lineTo(px, py - r)
        else:
            # L-shape: down, across at the row midpoint, down to parent.
            mid_y = (cy + py) / 2
            path = QPainterPath()
            path.moveTo(cx, cy + r)
            path.lineTo(cx, mid_y)
            path.lineTo(px, mid_y)
            path.lineTo(px, py - r)
        pen = QPen(QColor(self._cfg.edge_color), self._cfg.edge_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setZValue(-1)  # lines below nodes
        self._scene.addItem(item)

    def _refresh_selection_rings(self) -> None:
        for sha, node in self._node_items.items():
            if sha == self._selected_sha:
                node.setPen(
                    QPen(QColor(self._cfg.selection_color), self._cfg.selection_ring_width),
                )
            else:
                node.setPen(QPen(QColor(self._cfg.background_color), 1))

    # ----- input --------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            # Only accept clicks on the actual node ellipse, not the
            # chip or label, so we can use a generous bounding box
            # based on the node's rect.
            for sha, node in self._node_items.items():
                if node.rect().adjusted(-4, -4, 4, 4).contains(
                    node.mapFromScene(scene_pos),
                ):
                    self._selected_sha = sha
                    self._refresh_selection_rings()
                    self._view_model.select_commit(sha)
                    event.accept()
                    return
        super().mousePressEvent(event)

    # ----- layout on resize --------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self._placeholder is not None:
            rect = self.viewport().rect()
            bounds = self._placeholder.boundingRect()
            self._placeholder.setPos(
                (rect.width() - bounds.width()) / 2,
                (rect.height() - bounds.height()) / 2,
            )


__all__ = ["GraphWidget", "RenderConfig"]
