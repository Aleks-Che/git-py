"""Pixel checks for stash connections across several branch rows."""
import pytest
from PySide6.QtGui import QImage, QPainter
from src.core.graph_v2 import build_graph, graph_to_dicts
from src.core.models import BranchInfo, CommitInfo
from src.ui.widgets.graph_panel import GraphTableWidget
from src.viewmodels.graph_viewmodel import GraphViewModel


@pytest.mark.parametrize("has_wip", [False, True])
def test_stash_line_has_no_gaps_or_orphan_pipes(qtbot, has_wip):
    def commit(sha, parent, kind="commit"):
        return CommitInfo(
            sha=sha, short_sha=sha, message=sha, parents=[parent] if parent else [],
            author_name="", author_email="", author_time=0,
            committer_name="", committer_email="", committer_time=0, kind=kind,
        )

    layout = build_graph(
        [commit("stash", "base", "stash"), commit("dev3", "dev2"),
         commit("dev2", "dev1"), commit("dev1", "base"), commit("base", "")],
        [BranchInfo(name="dev", target_sha="dev3")],
        uncommitted_count=1 if has_wip else None, head_commit_sha="base",
    )
    vm = GraphViewModel()
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.resize(900, 400)
    vm.graph_updated.emit(graph_to_dicts(layout))
    cfg = widget._cfg
    lane_width = cfg.node_radius * 2 + 8

    # Render only the actual graph strokes: transparent pixels expose gaps
    # without labels, row backgrounds or selection colours masking them.
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        widget._draw_cells(painter, cfg.header_height)
    finally:
        painter.end()

    stash_idx = next(i for i, n in enumerate(layout.nodes) if n.commit and n.commit.kind == "stash")
    head_idx = next(i for i, n in enumerate(layout.nodes) if n.is_head)
    stash = layout.nodes[stash_idx]
    x = int(widget._lane_x(stash.lane, lane_width))
    start = int(widget._row_y(stash_idx) + cfg.row_height / 2 + cfg.node_radius + 1)
    end = int(widget._row_y(head_idx) + cfg.row_height / 2 - cfg.node_radius)
    for y in range(start, end):
        assert image.pixelColor(x, y).alpha() > 0, f"stash connection gap at ({x}, {y})"

    top_y = int(widget._row_y(0) + cfg.row_height / 2)
    for lane in range(layout.max_lane + 1):
        if lane != layout.nodes[0].lane:
            x = int(widget._lane_x(lane, lane_width))
            assert image.pixelColor(x, top_y).alpha() == 0, f"orphan pipe on lane {lane}"
