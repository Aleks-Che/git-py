"""Tests for :class:`src.ui.widgets.graph_panel.GraphTableWidget`.

Covers the GitKraken-style infinite scroll: scrolling to the bottom
of the loaded history (or clicking the "Load more" link in the
truncation label) asks the GraphViewModel for the next page.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from src.core.repository import RepositoryManager
from src.ui.widgets.graph_panel import GraphTableWidget
from src.viewmodels.graph_viewmodel import GraphViewModel


def _make_linear_repo(path: Path, count: int) -> RepositoryManager:
    """A repo with ``count`` linear commits on ``main``."""
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    parents: list = []
    for i in range(count):
        (path / "f.txt").write_text(f"{i}\n")
        mgr.repo.index.add("f.txt")
        mgr.repo.index.write()
        tree = mgr.repo.index.write_tree()
        oid = mgr.repo.create_commit("refs/heads/main", sig, sig, f"c{i}", tree, parents)
        parents = [oid]
    return mgr


def _make_panel(
    qtbot, tmp_git_repo: Path, count: int = 25,
) -> tuple[GraphTableWidget, GraphViewModel]:
    mgr = _make_linear_repo(tmp_git_repo, count)
    vm = GraphViewModel(history_limit=10)
    panel = GraphTableWidget(vm)
    qtbot.addWidget(panel)
    panel.resize(400, 200)
    panel.show()
    vm.set_repository(mgr)
    return panel, vm


def test_scroll_to_bottom_loads_next_page(qtbot, tmp_git_repo: Path) -> None:
    panel, vm = _make_panel(qtbot, tmp_git_repo)
    assert len(panel._rows) == 10

    panel._scrollbar.setValue(panel._scrollbar.maximum())

    assert vm.history_limit == 20
    assert len(panel._rows) == 20


def test_scroll_away_from_bottom_does_not_load(qtbot, tmp_git_repo: Path) -> None:
    panel, vm = _make_panel(qtbot, tmp_git_repo)

    panel._scrollbar.setValue(0)

    assert vm.history_limit == 10
    assert len(panel._rows) == 10


def test_truncation_label_link_loads_next_page(qtbot, tmp_git_repo: Path) -> None:
    panel, vm = _make_panel(qtbot, tmp_git_repo)

    panel._truncation_label.linkActivated.emit("load-more")

    assert vm.history_limit == 20
    assert len(panel._rows) == 20


def test_scroll_to_bottom_stops_at_full_history(qtbot, tmp_git_repo: Path) -> None:
    panel, vm = _make_panel(qtbot, tmp_git_repo)

    # Drain the history page by page.
    for _ in range(3):
        panel._scrollbar.setValue(panel._scrollbar.maximum())

    assert vm.truncated_count == 0
    assert len(panel._rows) == 25
    # Once the full DAG is visible the window must not grow anymore.
    limit = vm.history_limit
    panel._scrollbar.setValue(panel._scrollbar.maximum())
    assert vm.history_limit == limit


def test_branch_overflow_measures_collapsed_row(qtbot, tmp_git_repo: Path) -> None:
    """Multi-branch rows render as ONE collapsed chip (+ ▼ badge);
    the hidden siblings live only in the hover popup.  The column
    overflow must be measured against that single chip — summing all
    ref names produced a phantom scrollbar wider than anything on
    screen (kilocode ``232d7f2c``, 3 long remote names).
    """
    from src.ui.widgets.graph_panel import _branch_display_name

    panel, _vm = _make_panel(qtbot, tmp_git_repo)
    refs = [
        {
            "name": "origin/convoy/research-only-audit-of-kilocode-prs-1176/e083/head",
            "is_remote": True,
            "is_head": False,
        },
        {
            "name": "origin/another-pretty-long-remote-branch-name-for-testing",
            "is_remote": True,
            "is_head": False,
        },
        {"name": "main", "is_remote": False, "is_head": False},
    ]
    fm = panel.fontMetrics()
    measured = panel._measure_branch_row(refs, fm)
    all_names_w = sum(fm.horizontalAdvance(_branch_display_name(b)) for b in refs)
    # One chip plus the collapse badge is dramatically narrower than
    # the full stack of names.
    assert 0 < measured < all_names_w // 2
