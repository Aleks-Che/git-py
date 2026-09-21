"""Pixel-level continuity of WIP and the branch beside it."""

import pytest
from PySide6.QtGui import QImage, QPainter
from src.core.graph_v2 import build_graph, graph_to_dicts
from src.ui.widgets.graph_panel import GraphTableWidget
from src.viewmodels.graph_viewmodel import GraphViewModel

from tests.core.test_wip_graph_regression import crossing_history


@pytest.mark.parametrize("marker_kind", [None, "stash", "wip"])
def test_wip_and_neighbouring_branch_have_no_breaks(qtbot, marker_kind):
    commits, branches = crossing_history(marker_kind=marker_kind)
    layout = build_graph(commits, branches, uncommitted_count=12)
    vm = GraphViewModel()
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.resize(900, 500)
    vm.graph_updated.emit(graph_to_dicts(layout))
    cfg = widget._cfg
    lane_width = cfg.node_radius * 2 + 8
    by_sha = {n.commit.sha: i for i, n in enumerate(layout.nodes) if n.commit}

    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        widget._draw_cells(painter, cfg.header_height)
    finally:
        painter.end()

    head_lane = layout.nodes[by_sha["head"]].lane
    assert layout.nodes[0].lane == head_lane
    for lane, first, last in (
        (head_lane, 0, by_sha["head-parent"]),
        (layout.nodes[by_sha["side"]].lane, by_sha["head"], by_sha["side"]),
    ):
        x = int(widget._lane_x(lane, lane_width))
        start = int(widget._row_y(first) + cfg.row_height / 2 + cfg.node_radius + 1)
        end = int(widget._row_y(last) + cfg.row_height / 2 - cfg.node_radius)
        for y in range(start, end):
            assert image.pixelColor(x, y).alpha() > 0, (lane, x, y)

    # WIP must not create an orphan pipe in any other lane at the top.
    for lane in range(layout.max_lane + 1):
        if lane != head_lane:
            x = int(widget._lane_x(lane, lane_width))
            y = int(widget._row_y(0) + cfg.row_height / 2)
            assert image.pixelColor(x, y).alpha() == 0
