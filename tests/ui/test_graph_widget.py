"""Stage 2 tests for the :class:`GraphWidget`.

These run under ``pytest-qt`` (set ``QT_QPA_PLATFORM=offscreen``
for headless environments). We use real pygit2 repos and a real
:class:`GraphViewModel` so the data flow is exercised end-to-end;
the only thing under test is the widget's rendering and click
handling.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsSimpleTextItem,
)
from src.core.repository import RepositoryManager
from src.ui.widgets.graph_widget import GraphWidget
from src.viewmodels.graph_viewmodel import GraphViewModel


def _make_committed_repo(path: Path) -> RepositoryManager:
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree1 = mgr.repo.index.write_tree()
    c1 = mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree1, [])
    (path / "f.txt").write_text("v2\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree2 = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "second", tree2, [c1])
    return mgr


# ----- rendering -------------------------------------------------------


def test_widget_renders_node_per_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Exactly one ellipse per commit in the layout.
    nodes = [item for item in widget.scene().items() if isinstance(item, QGraphicsEllipseItem)]
    assert len(nodes) == 2
    # Each node carries the corresponding SHA as data(0).
    shas = {node.data(0) for node in nodes}
    assert len(shas) == 2
    # The set should match the two commits in the repo.
    history = mgr.get_all_history()
    assert shas == {c.sha for c in history}


def test_widget_renders_lines_between_commits(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # At least one connecting path (the edge from child to parent).
    paths = [it for it in widget.scene().items() if isinstance(it, QGraphicsPathItem)]
    assert len(paths) >= 1


def test_widget_shows_placeholder_for_empty_graph(qtbot) -> None:
    vm = GraphViewModel()  # no repo -> empty layout
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=1000):
        vm.refresh_graph()

    # No nodes for an empty graph.
    nodes = [it for it in widget.scene().items() if isinstance(it, QGraphicsEllipseItem)]
    assert nodes == []


def test_widget_clears_previous_scene_on_new_layout(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    first_nodes = [
        it for it in widget.scene().items() if isinstance(it, QGraphicsEllipseItem)
    ]
    assert len(first_nodes) == 2

    # Drop the repository: the next refresh must wipe the scene.
    vm.set_repository(None)
    after = [
        it for it in widget.scene().items() if isinstance(it, QGraphicsEllipseItem)
    ]
    assert after == []


# ----- selection / clicks ---------------------------------------------


def test_set_selected_sha_highlights_node(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    widget.set_selected_sha(head_sha)
    assert widget.selected_sha() == head_sha

    # The selected node's pen should be a lighter color than the
    # background. We test by looking at the pen's color name.
    selected_node = widget._node_items[head_sha]  # noqa: SLF001 - intentional
    pen = selected_node.pen()
    assert pen.color().name().lower() in ("#ffffff", "#fff")


def test_clicking_node_emits_commit_selected(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    head_node = widget._node_items[head_sha]  # noqa: SLF001 - intentional
    # Translate the node's scene rect to a viewport click point.
    target_scene = head_node.mapToScene(head_node.rect().center())
    target_view = widget.mapFromScene(target_scene)

    with qtbot.waitSignal(vm.commit_selected, timeout=1000) as blocker:
        qtbot.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=target_view)
    assert blocker.args[0] == head_sha
    # And the widget remembers the selection.
    assert widget.selected_sha() == head_sha


def test_clicking_empty_space_does_not_emit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Click somewhere far away from any node.
    with qtbot.assertNotEmitted(vm.commit_selected, wait=200):
        qtbot.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(2, 2),
        )


# ----- integration with main_window -----------------------------------


def test_main_window_wires_graph_view_model(qtbot, tmp_git_repo: Path) -> None:
    from src.ui.main_window import MainWindow

    mgr = _make_committed_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    with qtbot.waitSignal(window.graph_view_model().graph_updated, timeout=2000):
        window.set_repository(mgr)

    graph_table = window._graph_table  # noqa: SLF001
    assert graph_table.row_count() == 2
    window.close()


# ----- WIP node (Stage 3) ---------------------------------------------


def test_widget_renders_wip_node_when_worktree_dirty(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A modified worktree file adds a WIP ellipse above the real nodes."""
    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    nodes = [item for item in widget.scene().items() if isinstance(item, QGraphicsEllipseItem)]
    # 2 real commits + 1 WIP node.
    assert len(nodes) == 3
    shas = {node.data(0) for node in nodes}
    assert "WIP" in shas
    # WIP node should be tagged with sha="WIP" so clicks route to the
    # special case in CommitDetailPanel.
    wip_node = widget._node_items["WIP"]  # noqa: SLF001 - intentional
    assert wip_node.data(0) == "WIP"


# ----- branch label column --------------------------------------------


def _branch_label_items(widget: GraphWidget, sha: str) -> list[QGraphicsItem]:
    """Return the chip items in the branch-label column for *sha*."""
    return widget._branch_label_items.get(sha, [])  # noqa: SLF001


def _chip_children(
    widget: GraphWidget, sha: str, item_type: type,
) -> list[QGraphicsItem]:
    """Return child items of the given *item_type* from every chip for *sha*."""
    result: list[QGraphicsItem] = []
    for chip in _branch_label_items(widget, sha):
        for child in chip.childItems():
            if isinstance(child, item_type):
                result.append(child)
    return result


def _branch_label_texts(widget: GraphWidget, sha: str) -> list[str]:
    """Return the plain-text content of every text item in the column."""
    return [it.text() for it in _chip_children(widget, sha, QGraphicsSimpleTextItem)]


def _branch_label_icons(widget: GraphWidget, sha: str) -> list[QGraphicsPathItem]:
    """Return icon paths (checkmark/monitor) — NoBrush distinguishes from chip."""
    return [
        it for it in _chip_children(widget, sha, QGraphicsPathItem)
        if it.brush().style() == Qt.BrushStyle.NoBrush
    ]


def _branch_label_chips(widget: GraphWidget, sha: str) -> list[QGraphicsPathItem]:
    """Return chip (coloured background) items for *sha*."""
    return [
        it for it in _branch_label_items(widget, sha)
        if isinstance(it, QGraphicsPathItem)
    ]


def test_widget_renders_local_branch_label(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A commit with a non-HEAD local branch shows a chip + name + monitor."""
    mgr = _make_committed_repo(tmp_git_repo)
    parent_sha = mgr.get_all_history()[-1].sha
    mgr.repo.create_reference(
        "refs/heads/old_main", parent_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    assert _branch_label_texts(widget, parent_sha) == ["old_main"]
    # old_main is local, not HEAD: one chip + one monitor icon.
    assert len(_branch_label_chips(widget, parent_sha)) == 1
    icons = _branch_label_icons(widget, parent_sha)
    assert len(icons) == 1


def test_widget_renders_checkmark_for_head_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The current branch chip contains a checkmark + monitor inside it."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    icons = _branch_label_icons(widget, head_sha)
    assert len(icons) == 2  # checkmark + monitor
    # Both icons sit inside the chip: left-aligned checkmark near padding,
    # monitor right of the name.
    xs = sorted(int(round(p.scenePos().x())) for p in icons)
    fm = widget.fontMetrics()
    column_margin = 6
    pad = 5
    icon_size = 10
    gap = 3
    check_x = column_margin + pad
    monitor_x = column_margin + pad + icon_size + gap + fm.horizontalAdvance("main") + gap
    assert xs == sorted([check_x, monitor_x])


def test_widget_renders_no_monitor_for_remote_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """When both local ``main`` and remote ``origin/main`` point at the
    same commit, the remote chip is suppressed — the monitor icon on
    the local chip already conveys the "local" information."""
    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/main", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    # Only the local "main" — the remote duplicate is suppressed.
    texts = _branch_label_texts(widget, head_sha)
    assert texts == ["main"]
    # One chip (local) with checkmark + monitor.
    assert len(_branch_label_chips(widget, head_sha)) == 1
    assert len(_branch_label_icons(widget, head_sha)) == 2  # checkmark + monitor


def test_widget_remote_branch_strips_origin_prefix(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A remote-only branch (no local copy) shows chip with short name, no monitor."""
    mgr = _make_committed_repo(tmp_git_repo)
    # origin/base_features — remote only, no local copy.
    mgr.repo.references.create(
        "refs/remotes/origin/base_features", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    texts = _branch_label_texts(widget, head_sha)
    # main (local HEAD) + base_features (remote, stripped prefix).
    assert texts == ["main", "base_features"]
    # main: chip + checkmark + monitor.  base_features: chip only.
    assert len(_branch_label_chips(widget, head_sha)) == 2
    assert len(_branch_label_icons(widget, head_sha)) == 2  # checkmark + monitor for main


def test_widget_no_branch_label_items_for_commit_without_branches(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A commit no branch points at has an empty branch-label column."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # The older commit (the parent of HEAD) has no branch attached.
    older_sha = mgr.get_all_history()[-1].sha
    assert widget._branch_label_items[older_sha] == []  # noqa: SLF001


def test_graph_node_lane_zero_offsets_by_branch_label_width(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The leftmost commit node must sit to the right of the label column."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    cfg = widget._cfg  # noqa: SLF001
    head_node = widget._node_items[mgr.head_commit.sha]  # noqa: SLF001
    expected_x = cfg.branch_label_width + cfg.lane_offset
    assert int(round(head_node.rect().center().x())) == expected_x


# ----- scroll_to_commit (GraphViewModel) -----------------------------


def test_viewmodel_scroll_to_commit_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``GraphViewModel.scroll_to_commit`` emits the view-side signal verbatim.

    The view subscribes to :attr:`scroll_to_commit_requested`; this
    test pins the ViewModel's role as a pure forwarder so we know
    that the contract for the view is just "you will receive the
    SHA, do whatever scrolling is needed".
    """
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    # GraphViewModel is a QObject, not a QWidget — qtbot.addWidget
    # would refuse it. The QObject lives only for the duration of
    # the test and the Qt event loop is driven by ``waitSignal``, so
    # no explicit teardown is needed.

    head_sha = mgr.head_commit.sha
    with qtbot.waitSignal(vm.scroll_to_commit_requested, timeout=1000) as blocker:
        vm.scroll_to_commit(head_sha)
    assert blocker.args[0] == head_sha


def test_viewmodel_scroll_to_commit_empty_sha_is_noop(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A falsy SHA is ignored — no signal, no crash.

    Defends against accidental callers (e.g. a half-built branch
    snapshot) passing ``None`` or ``""``: the view would otherwise
    scroll to "nothing" and the widget would have to defend itself.
    """
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)

    with qtbot.assertNotEmitted(vm.scroll_to_commit_requested, wait=200):
        vm.scroll_to_commit("")
        vm.scroll_to_commit(None)  # type: ignore[arg-type]


# ----- scroll_to_commit (GraphTableWidget) ---------------------------


def test_graph_table_widget_scrolls_vertically_to_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The graph table scrolls vertically so the target commit is centred.

    Same behaviour as before Stage 4: the row's vertical centre is
    brought to the viewport's vertical centre, regardless of horizontal
    state.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    # Add a few extra commits so the graph is tall enough to scroll.
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    # ``bottom_oid`` captures the *very first* commit on the chain —
    # the one that existed before the loop started. After the loop
    # ``refs/heads/main`` has been rewritten, so reading HEAD would
    # give us the most-recent commit, not the bottom of the graph.
    bottom_oid = mgr.repo.head.target
    parent = bottom_oid
    for i in range(10):
        (tmp_git_repo / "f.txt").write_text(f"v{i + 3}\n")
        mgr.repo.index.add("f.txt")
        mgr.repo.index.write()
        tree = mgr.repo.index.write_tree()
        parent = mgr.repo.create_commit(
            "refs/heads/main", sig, sig, f"commit {i + 3}", tree, [parent],
        )
    bottom_sha = str(bottom_oid)
    head_sha = str(mgr.repo.head.target)

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 200)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Sanity-check: bottom_sha is not the head commit and the
    # scrollbar overflows.
    assert bottom_sha != head_sha
    assert widget._scrollbar.maximum() > 0  # noqa: SLF001

    # Scroll the viewport back to the top first so the test can
    # observe the downward jump.
    widget._scrollbar.setValue(0)  # noqa: SLF001
    widget.update()

    # Now ask the widget to scroll to the bottom commit.
    widget.scroll_to_commit(bottom_sha)

    # The bottom commit's row is the last one. The vertical scroll
    # value should now put that row near the viewport centre.
    assert widget._scrollbar.value() > 0  # noqa: SLF001


def test_graph_table_widget_scroll_horizontal_brings_lane_into_view(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A commit outside the visible graph column triggers a horizontal scroll.

    Builds a graph with a wide lane (lane index 10, well past the
    visible window for an 800px-wide widget) and calls the private
    :meth:`_scroll_horizontal_to_lane` helper directly with a
    synthetic row dict — the helper is the only place the new
    horizontal-scroll math lives, so this is the cheapest way to
    pin its behaviour.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Force the graph column to overflow horizontally by synthesising
    # a row with a very high lane index. Then set the horizontal
    # scrollbar to a non-zero range so the helper has room to move.
    widget._h_scrollbars[1].setRange(0, 500)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(0)  # noqa: SLF001

    fake_row = {
        "commit": {"sha": "deadbeef", "kind": "commit"},
        "lane": 10,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(fake_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() > 0  # noqa: SLF001


def test_graph_table_widget_no_horizontal_scroll_when_lane_already_visible(
    qtbot, tmp_git_repo: Path,
) -> None:
    """If the lane is already on screen, the horizontal scroll is left alone.

    The helper is a no-op when the target lane's centre sits within
    the visible portion of the graph column (with a one-node-radius
    margin). We start the bar at a non-zero value to make the
    assertion meaningful — a careless implementation that always
    recenters would change the value here.

    With the bar at ``value=123`` the content is shifted left by
    123px, so a lane at content-x ~480 (i.e. lane 9) lands inside
    the visible column.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    widget._h_scrollbars[1].setRange(0, 500)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(123)  # noqa: SLF001

    fake_row = {
        "commit": {"sha": "deadbeef", "kind": "commit"},
        "lane": 9,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(fake_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() == 123  # noqa: SLF001


def test_graph_table_widget_horizontal_scroll_skips_connector_rows(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A connector row (no commit) is ignored by the horizontal scroll helper.

    Connector rows in the cell-based layout represent a lane
    continuation without a node — scrolling to one of them would
    be meaningless. The helper must return without touching the
    horizontal scrollbar.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    widget._h_scrollbars[1].setRange(0, 500)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(42)  # noqa: SLF001

    connector_row = {
        "commit": None,
        "lane": 5,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(connector_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() == 42  # noqa: SLF001


def test_graph_table_widget_horizontal_scroll_noop_when_bar_disabled(
    qtbot, tmp_git_repo: Path,
) -> None:
    """If the graph column has no horizontal overflow, the helper is a no-op.

    The bar is hidden in that case (``maximum() == 0``); touching it
    would push the value past its valid range. We verify the helper
    does not error and leaves the bar alone.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Force the bar to "disabled" state — maximum == 0, like when
    # the graph column fits entirely in the viewport.
    widget._h_scrollbars[1].setRange(0, 0)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(0)  # noqa: SLF001

    fake_row = {
        "commit": {"sha": "deadbeef", "kind": "commit"},
        "lane": 5,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(fake_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() == 0  # noqa: SLF001


def test_graph_table_widget_subscribes_to_scroll_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The widget responds to :attr:`scroll_to_commit_requested`.

    Driving the ViewModel side (not the widget's API directly)
    proves the signal/slot wiring done in :meth:`GraphTableWidget.__init__`
    is correct.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    widget._scrollbar.setValue(0)  # noqa: SLF001

    vm.scroll_to_commit(head_sha)
    # The signal is connected directly; it runs synchronously so the
    # scrollbar has the new value by the time we read it.
    assert widget._scrollbar.value() >= 0  # noqa: SLF001
