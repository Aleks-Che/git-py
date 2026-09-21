"""Real Git history -> ViewModel -> distinct branch chip and history colours."""

from pathlib import Path

import pygit2
import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from src.core.graph_v2 import BRANCH_PALETTE, _pick_branch_color
from src.ui.widgets.graph_panel import GraphTableWidget
from src.viewmodels.commands import (
    CheckoutCommand,
    CommandProcessor,
    CommitCommand,
    CreateBranchCommand,
    MergeCommand,
)
from src.viewmodels.graph_viewmodel import GraphViewModel


@pytest.mark.parametrize("with_stash", [False, True])
def test_idle_branch_keeps_chip_color_without_recoloring_history(qtbot, committed_repo, with_stash):
    repo = committed_repo.repo
    repo.lookup_branch("main").rename("dev")
    base = repo.head.peel()
    repo.create_branch("redesign", base)
    repo.checkout("refs/heads/redesign")
    path = Path(committed_repo.path) / "hello.txt"
    original = path.read_bytes()
    if with_stash:
        path.write_bytes(b"redesign draft\n")
        repo.stash(base.author, "saved redesign draft")
    repo.checkout("refs/heads/dev")
    assert path.read_bytes() == original
    sig = pygit2.Signature("tester", "test@example.com", base.commit_time + 10, 0)
    tip = repo.create_commit("HEAD", sig, sig, "advance dev", base.tree_id, [base.id])
    repo.create_reference("refs/remotes/origin/dev", base.id)
    expected_color = BRANCH_PALETTE[_pick_branch_color("dev")]

    for dirty in (False, True):
        if dirty:
            path.write_bytes(b"new dev draft\n")
        # A fresh ViewModel also checks reopening, without cached colour state.
        vm = GraphViewModel(committed_repo)
        widget = GraphTableWidget(vm)
        qtbot.addWidget(widget)
        widget.resize(1000, 400)
        with qtbot.waitSignal(vm.graph_updated, timeout=2000) as signal:
            vm.refresh_graph()
        rows = signal.args[0]
        regular = [row for row in rows if row.get("kind") == "commit"]
        assert len(regular) == 3
        assert all(row["color"] == expected_color for row in regular)
        assert any(row["is_uncommitted"] for row in rows) == dirty
        assert any(row.get("kind") == "stash" for row in rows) == with_stash
        base_row = next(row for row in regular if row["sha"] == str(base.id))
        assert any(branch["name"] == "redesign" for branch in base_row["branch_refs"])

        image = QImage(widget.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            widget._draw_branch_column(painter, widget._cfg.header_height, 0, widget.width())
        finally:
            painter.end()
        for sha, name in ((str(base.id), "redesign"), (str(tip), "dev")):
            rect = widget._branch_chip_rects[(sha, name)]["rect"]
            # Left padding is inside the solid fill, away from text and icons.
            assert image.pixelColor(rect.left() + 3, rect.center().y()) == QColor(
                BRANCH_PALETTE[_pick_branch_color(name)]
            )
        widget.close()

    assert repo.head.target == tip
    assert repo.lookup_branch("redesign").target == base.id
    assert path.read_bytes() == b"new dev draft\n"
    assert repo.index["hello.txt"].id == (base.tree / "hello.txt").id


@pytest.mark.parametrize("head_name", ["main", "dev"])
@pytest.mark.parametrize("dirty", [False, True])
def test_merged_dev_leaves_main_trunk_blue_at_fork(qtbot, committed_repo, head_name, dirty):
    """TC-GRAPH-COLOR-02: real no-ff merge, ViewModel data and rendered strokes."""
    manager = committed_repo
    repo = manager.repo
    base = repo.head.peel()
    processor = CommandProcessor()
    processor.execute(CreateBranchCommand(manager, "dev"))
    processor.execute(CheckoutCommand(manager, "dev"))
    dev_shas = []
    for i in range(2):
        (Path(manager.path) / "hello.txt").write_bytes(f"dev change {i}\n".encode())
        repo.index.add("hello.txt")
        repo.index.write()
        processor.execute(CommitCommand(manager, f"dev change {i}", base.author, base.committer))
        dev_shas.append(manager.head_commit.sha)
    processor.execute(CheckoutCommand(manager, "main"))
    processor.execute(MergeCommand(manager, "dev", no_ff=True))
    merge_sha = manager.head_commit.sha
    assert manager.head_commit.parents == [str(base.id), dev_shas[-1]]
    if head_name == "dev":
        processor.execute(CheckoutCommand(manager, "dev"))
    if dirty:
        (Path(manager.path) / "hello.txt").write_bytes(b"work in progress\n")

    vm = GraphViewModel(manager, history_limit=100)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.resize(1000, 500)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as signal:
        vm.refresh_graph()
    rows = signal.args[0]
    by_sha = {row["sha"]: row for row in rows}
    blue = QColor(BRANCH_PALETTE[_pick_branch_color("main")])
    green = QColor(BRANCH_PALETTE[_pick_branch_color("dev")])
    for sha in (merge_sha, str(base.id), str(base.parent_ids[0])):
        assert QColor(by_sha[sha]["color"]) == blue, sha
    for sha in dev_shas:
        assert QColor(by_sha[sha]["color"]) == green, sha

    # Paint just the graph's lines; no text, avatars or selection can mask
    # a wrong-coloured vertical immediately above/below the fork.
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        widget._draw_cells(painter, widget._cfg.header_height)
    finally:
        painter.end()
    cfg = widget._cfg
    lane_width = cfg.node_radius * 2 + 8
    fork = by_sha[str(base.id)]
    x_main = int(widget._lane_x(fork["lane"], lane_width))
    x_dev = int(widget._lane_x(by_sha[dev_shas[0]]["lane"], lane_width))
    y_fork = int(widget._row_y(rows.index(fork)) + cfg.row_height / 2)
    assert x_dev > x_main
    for offset in (-cfg.node_radius - 3, cfg.node_radius + 3):
        assert image.pixelColor(x_main, int(y_fork + offset)) == blue
    assert image.pixelColor((x_main + x_dev) // 2, y_fork) == green
    assert str(repo.lookup_branch("main").target) == merge_sha
    assert str(repo.lookup_branch("dev").target) == dev_shas[-1]
    widget.close()
