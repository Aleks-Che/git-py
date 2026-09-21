"""Real Git history -> ViewModel -> distinct branch chip and history colours."""

from pathlib import Path

import pygit2
import pytest
from PySide6.QtGui import QColor, QImage, QPainter
from src.core.graph_v2 import BRANCH_PALETTE, _pick_branch_color
from src.ui.widgets.graph_panel import GraphTableWidget
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
