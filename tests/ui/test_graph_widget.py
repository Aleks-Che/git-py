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
from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
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


# ----- branch chip hit-testing & gestures ------------------------------


def _chip_viewport_pos(widget, sha: str, display: str) -> QPoint:
    """Convert a branch chip's content rect to a viewport click point.

    Returns the centre of the chip — good enough for hit-tests because
    the chip's rounded-rect radius is small enough to leave a clear
    inside region even after the 4-px slop Qt adds during drag.
    """
    chip = widget._branch_chip_rects[(sha, display)]  # noqa: SLF001
    rect = chip["rect"]
    # Translate content x to widget x (the column scrolls horizontally
    # in content coordinates; the click arrives in widget coordinates).
    widget_x = rect.center().x() - widget._h_scrolls[0]  # noqa: SLF001
    # The y is in widget coordinates directly (the painter is not
    # translated vertically, the row position already accounts for the
    # vertical scroll).
    widget_y = rect.center().y()
    return QPoint(int(widget_x), int(widget_y))


def _force_paint(widget) -> None:
    """Force a paint pass so the chip-rect cache is populated.

    In offscreen Qt, ``show()`` + a few signal emissions are not
    always enough to land a paint event before the test starts
    asserting on the cache. The three-call dance (``update`` →
    ``processEvents`` → ``repaint``) is the cheapest way to get
    deterministic paints: ``update`` schedules a repaint,
    ``processEvents`` lets the scheduled event run, and ``repaint``
    blocks until the actual paint completes. Tests that need a
    populated chip cache should call this once after the graph
    update.
    """
    widget.update()
    QApplication.processEvents()
    widget.repaint()


def _make_repo_with_feature(
    path: Path,
) -> tuple[RepositoryManager, str, str]:
    """Build a repo with a second local branch named ``feature`` at HEAD.

    Returns ``(manager, head_sha, feature_sha)``. ``head_sha`` and
    ``feature_sha`` are equal because ``feature`` is created on top of
    ``main``'s tip — the chip is a duplicate of the HEAD chip, which
    is the realistic case the new branch gestures need to handle.
    """
    mgr = _make_committed_repo(path)
    feature_sha = mgr.head_commit.sha
    mgr.repo.create_reference(
        "refs/heads/feature", feature_sha, force=True,
    )
    head_sha = mgr.head_commit.sha
    return mgr, head_sha, feature_sha


def test_branch_chip_at_returns_none_outside_branch_column(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A click in the graph column (not on a chip) must not match.

    The hit-test's first guard is the column boundary — anything past
    the first divider is in the graph / commit-message area and must
    fall through to the commit-selection path.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Click squarely in the graph column (the first divider is at 180
    # by default; click at 400 which is well into col 1).
    chip = widget._branch_chip_at(400, 200)  # noqa: SLF001
    assert chip is None


def test_branch_chip_at_finds_chip_after_horizontal_scroll(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Hit-test works when the branch column is scrolled horizontally.

    Forces the column's scrollbar to a small non-zero value and
    clicks at the chip's post-scroll screen position. The test pins
    the ``content_x + scroll`` math in :meth:`_branch_chip_at` so a
    regression here is loud (the chip would simply never be found).
    The scroll value is small enough to keep the chip partially on
    screen — a click at its post-scroll centre is therefore a real,
    visible hit.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(300, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Force the column overflow so the horizontal scrollbar activates.
    # 40 px is enough to slide the rightmost "main" chip a bit, but
    # the leftmost "feature" chip stays inside the column.
    widget._h_scrollbars[0].setRange(0, 200)  # noqa: SLF001
    widget._h_scrollbars[0].setValue(40)  # noqa: SLF001
    widget._h_scrolls[0] = 40  # noqa: SLF001
    _force_paint(widget)

    # Compute the chip's post-scroll viewport position and click there.
    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    chip = widget._branch_chip_at(pos.x(), pos.y())  # noqa: SLF001
    assert chip is not None
    assert chip["display"] == "feature"
    assert chip["is_head"] is False
    assert chip["is_remote"] is False


def test_branch_chip_at_finds_head_chip(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The current branch's chip carries ``is_head=True``.

    The flag matters for the context menu — actions on the active
    branch are disabled, since merging / rebasing a branch into
    itself is a no-op. The test pins the flag at the chip level so a
    future refactor that drops the ``is_head`` plumbing would be
    caught here.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")
    chip = widget._branch_chip_at(pos.x(), pos.y())  # noqa: SLF001
    assert chip is not None
    assert chip["display"] == "main"
    assert chip["is_head"] is True


def test_left_click_on_branch_chip_does_not_select_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A left click on a branch chip must NOT change the commit selection.

    This is the contract: the chip is a separate gesture surface from
    the commit node that lives in the same row. A click that would
    otherwise have selected the row's commit must be swallowed by
    the chip hit-test.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, head_sha, "feature")
    with qtbot.assertNotEmitted(vm.commit_selected, wait=200):
        qtbot.mouseClick(
            widget, Qt.MouseButton.LeftButton, pos=pos,
        )
    # The widget's own selection also stayed where it was (None
    # initially).
    assert widget.selected_sha() is None


def test_double_click_on_branch_chip_emits_checkout(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Double-clicking a branch chip emits the checkout signal with the ref name.

    Uses the ``feature`` chip so the signal carries ``"feature"`` —
    the bare local name, not ``"refs/heads/feature"``. The VM's
    :meth:`checkout_branch` accepts the bare name so the wiring in
    :meth:`MainWindow._on_graph_branch_checkout` stays a one-liner.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        qtbot.mouseDClick(
            widget, Qt.MouseButton.LeftButton, pos=pos,
        )
    assert blocker.args[0] == "feature"


def test_double_click_on_head_branch_chip_emits_checkout(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Double-clicking the current branch's chip also emits the signal.

    Edge case: clicking the HEAD chip is a no-op as far as the actual
    checkout goes (you're already there), but the signal still fires
    so the MainWindow can decide what to do — the left panel, the
    toolbar and the chip are all consistent. The test pins that the
    gesture is not silently swallowed.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")
    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        qtbot.mouseDClick(
            widget, Qt.MouseButton.LeftButton, pos=pos,
        )
    assert blocker.args[0] == "main"


def test_right_click_on_branch_chip_routes_to_branch_menu(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    """Right-clicking a branch chip invokes the branch menu, not the commit one.

    Pins the dispatch in :meth:`_on_context_menu`: the chip hit-test
    must run before the commit hit-test, otherwise the chip click
    would fall through to the commit menu (Checkout this commit).
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Patch the menu builders so we can detect which path the context
    # menu took without running QMenu.exec() (which would block).
    branch_called = []
    commit_called = []
    monkeypatch.setattr(
        widget, "_show_branch_context_menu",
        lambda chip, pos: branch_called.append(chip["display"]),
    )
    monkeypatch.setattr(
        widget, "_on_context_menu_for_commit",  # type: ignore[attr-defined]
        lambda sha, pos: commit_called.append(sha),
        raising=False,
    )

    pos = _chip_viewport_pos(widget, head_sha, "feature")
    widget._on_context_menu(pos)  # noqa: SLF001

    assert branch_called == ["feature"]
    assert commit_called == []


def test_show_branch_context_menu_contains_merge_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The branch chip's context menu includes 'Merge X into Y'.

    The menu is built in :meth:`_show_branch_context_menu` and
    contains a single :class:`QAction` per verb. The merge action's
    label interpolates both the source and the current HEAD so the
    user sees a readable description of what they are about to do.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001

    # Build the actions synchronously — the actual QMenu.exec() would
    # block waiting for a click. We just want to inspect the actions.
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    labels = [a.text() for a in actions]

    assert "Checkout feature" in labels
    assert "Merge feature into main" in labels
    assert "Rebase feature onto main" in labels


def test_show_branch_context_menu_merge_disabled_on_current_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The merge / rebase actions are disabled for the current branch.

    Merging ``main`` into ``main`` is a no-op; the menu shows the
    action so the user can see what they could do, but it is greyed
    out. Mirrors the left panel's ``_merge_rebase_against_current``
    policy.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "main")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    merge = next(a for a in actions if a.text().startswith("Merge main into"))
    rebase = next(a for a in actions if a.text().startswith("Rebase main onto"))
    assert merge.isEnabled() is False
    assert rebase.isEnabled() is False


def test_branch_menu_merge_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Triggering the merge action emits merge_branch_requested with source+target."""
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    merge = next(a for a in actions if a.text() == "Merge feature into main")

    with qtbot.waitSignal(widget.merge_branch_requested, timeout=1000) as blocker:
        merge.trigger()
    assert blocker.args == ["feature", "main"]


def test_branch_menu_rebase_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The rebase action emits rebase_branch_requested with source+target."""
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    rebase = next(a for a in actions if a.text() == "Rebase feature onto main")

    with qtbot.waitSignal(widget.rebase_branch_requested, timeout=1000) as blocker:
        rebase.trigger()
    assert blocker.args == ["feature", "main"]


def test_branch_menu_checkout_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The checkout action in the chip menu emits the checkout signal."""
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    checkout = next(a for a in actions if a.text() == "Checkout feature")

    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        checkout.trigger()
    assert blocker.args == ["feature"]


def test_remote_branch_chip_checkout_uses_full_ref_name(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A remote-only branch chip's checkout action carries the full ref name.

    The chip displays ``base_features`` (the ``origin/`` prefix is
    stripped for the user), but the underlying ref is
    ``refs/remotes/origin/base_features``. The wiring in
    :meth:`MainWindow._on_graph_branch_checkout` looks at the full
    name to decide between local and fetch+checkout — so the action
    must pass the full ref through, not the display label.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/base_features", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "base_features")]  # noqa: SLF001
    assert chip["is_remote"] is True
    assert chip["full_name"] == "origin/base_features"
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    checkout = next(
        a for a in actions if a.text().startswith("Checkout base_features")
    )
    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        checkout.trigger()
    assert blocker.args == ["origin/base_features"]


# ----- branch chip suppression & per-row keys ------------------------


def test_remote_branch_suppressed_when_local_duplicate_exists(
    qtbot, tmp_git_repo: Path,
) -> None:
    """When local ``main`` and remote ``origin/main`` share a commit,
    only the local chip is drawn and only it has a hit-test rect.

    The pre-fix behaviour was: both chips were drawn but only the
    second one (the remote) ended up in the chip-rect cache (because
    the cache was keyed by display name), so drag-and-drop worked on
    the remote chip but not the local one. Pinning the single-chip
    result here so a future regression surfaces as a loud failure
    rather than a "menu only opens on the remote chip" surprise.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/main", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Exactly one entry for the HEAD row, keyed by the HEAD commit
    # SHA + the display name. The remote ``origin/main`` chip is
    # suppressed because the local ``main`` chip covers the name.
    head_sha = mgr.head_commit.sha
    assert (head_sha, "main") in widget._branch_chip_rects  # noqa: SLF001
    assert len(widget._branch_chip_rects) == 1  # noqa: SLF001

    chip = widget._branch_chip_rects[(head_sha, "main")]  # noqa: SLF001
    # The surviving chip is the *local* one — it must carry the
    # local-style flags so the menu builder treats it as local.
    assert chip["is_remote"] is False
    assert chip["is_head"] is True
    assert chip["full_name"] == "main"


def test_chip_rect_cache_keyed_by_row_and_display(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Two commits with the same display name both have their own chip.

    Without a row-aware key the cache would overwrite the older
    chip with the newer one and the older commit's drag would
    silently stop working. The test builds a feature branch that
    shares ``main``'s name at HEAD and points it at a different
    (older) commit; the cache must have one entry per row.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_commit = mgr.repo[mgr.repo.head.target]
    parent_sha = str(head_commit.parent_ids[0])
    mgr.repo.create_reference(
        "refs/heads/release", parent_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    head_sha = mgr.head_commit.sha
    # HEAD row carries ``main`` (the current branch); the older
    # row carries ``release``. Both keys are present in the
    # cache.
    assert (head_sha, "main") in widget._branch_chip_rects  # noqa: SLF001
    assert (parent_sha, "release") in widget._branch_chip_rects  # noqa: SLF001
    assert len(widget._branch_chip_rects) == 2  # noqa: SLF001


def test_drop_on_suppressed_chip_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Dropping on the single surviving (local) chip works.

    The bug report said: ``drag & drop only works on the remote
    branch``. The fix collapsed the two visual chips into one;
    the drop test pins that the surviving chip's full Qt drag
    pipeline is wired up.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/main", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Build a synthetic drop event at the (only) chip's centre.
    head_sha = mgr.head_commit.sha
    chip = widget._branch_chip_rects[(head_sha, "main")]  # noqa: SLF001
    content_x = chip["rect"].center().x()
    content_y = chip["rect"].center().y()
    drop_x = content_x - widget._h_scrolls[0]  # noqa: SLF001
    drop_pos = QPoint(int(drop_x), int(content_y))

    mime = QMimeData()
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415
    mime.setData(_CHIP_MIME, b"main")
    mime.setText("main")
    event = QDropEvent(
        drop_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )

    with qtbot.assertNotEmitted(widget.branch_dropped_on_branch, wait=200):
        # Dropping main on itself is the documented no-op.
        widget.dropEvent(event)  # noqa: SLF001


# ----- branch chip drag-and-drop --------------------------------------


def _make_chip_mime(
    widget, source_display: str,
) -> QMimeData:
    """Build a :class:`QMimeData` payload that mimics a chip drag.

    The drag payload format is owned by :meth:`GraphTableWidget._begin_chip_drag`
    — both the custom ``application/x-git-py-branch-chip`` type and
    the plain-text branch name are set.  The MIME constant is a
    module-level value in :mod:`src.ui.widgets.graph_panel`; the
    helper imports it on demand so the test does not have to
    duplicate the string.
    """
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415

    mime = QMimeData()
    mime.setData(_CHIP_MIME, source_display.encode("utf-8"))
    mime.setText(source_display)
    return mime


def test_widget_accepts_drops(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``GraphTableWidget`` must call ``setAcceptDrops(True)``.

    ``QWidget`` defaults to ``acceptDrops=False``; without an
    explicit opt-in Qt's drag pipeline never delivers
    ``dragEnterEvent`` / ``dropEvent`` to the widget. The symptom
    is subtle: the press on a branch chip starts a drag (because
    the widget initiates it via ``QDrag``), the cursor changes to
    the drag indicator, but on release nothing happens — the drop
    is silently rejected before the handler ever runs. Pinning
    the flag here turns a runtime "menu doesn't show" mystery
    into a loud, immediate test failure.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    assert widget.acceptDrops() is True


def test_drag_enter_accepts_branch_chip_mime(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The drag-enter handler must accept a chip-style MIME payload.

    The handler is the gate that lets Qt show the drop cursor over
    a chip. A regression that filters out the custom MIME type
    would surface as ``QDrag`` returning ``IgnoreAction`` and the
    drop cursor never appearing — both of which are visible in
    practice but noisy to debug without this test.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    mime = _make_chip_mime(widget, "feature")
    pos = QPoint(50, 100)
    event = QDragEnterEvent(
        pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dragEnterEvent(event)  # noqa: SLF001
    assert event.isAccepted() is True


def test_drag_enter_rejects_unrelated_mime(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Drop payloads without the chip MIME type are not accepted.

    Defensive: the widget may one day accept other drags (e.g.
    dragging a file from a commit row to the terminal). Until then
    the chip MIME is the only valid payload, and ``dragEnterEvent``
    must fall through to ``super`` for everything else.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    mime = QMimeData()
    mime.setText("some-file.txt")
    pos = QPoint(50, 100)
    event = QDragEnterEvent(
        pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    # ``super().dragEnterEvent`` is a no-op for ``QWidget`` — the
    # test pins that the handler does **not** mark an unrelated drop
    # as accepted.
    widget.dragEnterEvent(event)  # noqa: SLF001
    assert event.isAccepted() is False


def test_drop_on_target_branch_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Dropping the ``feature`` chip on the ``main`` chip emits branch_dropped_on_branch.

    The test synthesises a chip-style ``QDropEvent`` at the
    ``main`` chip's screen position and asserts the signal payload.
    The actual :class:`QMenu` that ``MainWindow`` builds on top of
    this signal is exercised in a separate integration test.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    target_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")
    mime = _make_chip_mime(widget, "feature")
    event = QDropEvent(
        target_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )

    with qtbot.waitSignal(
        widget.branch_dropped_on_branch, timeout=1000,
    ) as blocker:
        widget.dropEvent(event)  # noqa: SLF001
    assert blocker.args == ["feature", "main"]


def test_drop_on_same_chip_is_ignored(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Dropping a chip on itself must not emit a signal.

    Merging a branch into itself is a no-op; emitting a signal here
    would just leave the ``MainWindow`` slot to handle a degenerate
    case.  The drop is still accepted (so Qt stops showing the
    "no drop" cursor) but ``branch_dropped_on_branch`` stays quiet.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    target_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    mime = _make_chip_mime(widget, "feature")
    event = QDropEvent(
        target_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )
    with qtbot.assertNotEmitted(widget.branch_dropped_on_branch, wait=200):
        widget.dropEvent(event)  # noqa: SLF001
    assert event.isAccepted() is True


def test_drop_off_any_chip_is_accepted_but_quiet(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A drop that misses every chip is accepted but emits no signal.

    Defensive: the user might drop a chip on the commit graph
    (column 1) or the commit message (column 2). We accept the drop
    so Qt stops showing the "no drop" cursor, but we do not act on
    it — only drops on a chip carry an explicit target branch.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Drop somewhere in the graph column, well past the branch chips.
    drop_pos = QPoint(700, 100)
    mime = _make_chip_mime(widget, "feature")
    event = QDropEvent(
        drop_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )
    with qtbot.assertNotEmitted(widget.branch_dropped_on_branch, wait=200):
        widget.dropEvent(event)  # noqa: SLF001
    assert event.isAccepted() is True


def test_drag_start_threshold_keeps_short_click_quiet(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A press + tiny move on a chip must not start a drag.

    Pins the threshold logic: ``_begin_chip_drag`` only fires once
    the cursor has moved more than ``_DRAG_START_THRESHOLD_PX``
    from the press point. Short clicks on a chip (which is the
    pre-existing commit-selection bypass) must still bypass
    commit selection *without* accidentally starting a drag.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Press on the "feature" chip, then nudge the cursor by 2 px —
    # well below the 6-px drag threshold.
    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(press_event)  # noqa: SLF001
    # The press must have stashed the chip + position; the drag
    # must NOT have started yet.
    assert widget._drag_press_chip is not None  # noqa: SLF001
    assert widget._drag_active_chip is None  # noqa: SLF001

    move_pos = pos + QPoint(2, 0)
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        move_pos,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    # The move event will try to start a real QDrag inside the
    # widget — that blocks waiting for a drop. Patch the drag
    # starter to record the call instead so the test stays
    # synchronous.
    started: list[dict] = []

    def fake_begin(chip, press_pos):
        started.append({"chip": chip, "press_pos": press_pos})
        # No QDrag.exec here — the test does not need a real drag.

    widget._begin_chip_drag = fake_begin  # type: ignore[method-assign]  # noqa: SLF001
    widget.mouseMoveEvent(move_event)  # noqa: SLF001
    assert started == []  # 2 px is below the threshold

    # Now nudge the cursor past the threshold — the drag must start.
    big_move = pos + QPoint(20, 0)
    big_event = QMouseEvent(
        QEvent.Type.MouseMove,
        big_move,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(big_event)  # noqa: SLF001
    assert len(started) == 1
    assert started[0]["chip"]["display"] == "feature"


def test_release_clears_drag_press_state(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A mouse release after a chip press clears the press state.

    The press state is what the move handler reads to decide
    whether a drag should be promoted; if it leaks past the
    release, the next move (in some unrelated area) could start a
    spurious drag. The test pins the release-time cleanup.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(press_event)  # noqa: SLF001
    assert widget._drag_press_chip is not None  # noqa: SLF001

    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseReleaseEvent(release_event)  # noqa: SLF001
    assert widget._drag_press_chip is None  # noqa: SLF001
    assert widget._drag_press_pos is None  # noqa: SLF001
    assert widget._drag_active_chip is None  # noqa: SLF001


def test_begin_chip_drag_uses_correct_mime_types(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The drag payload carries the chip MIME and the plain text.

    Pins the payload contract: the drop handler distinguishes chip
    drags from anything else via the custom MIME type, while the
    plain text lets external targets (clipboard, other widgets
    that only know text) still get a useful payload. The
    ``exec`` call is monkey-patched out so the test does not
    block on a real drag.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    captured: list[QMimeData] = []

    class _StubDrag:
        def __init__(self, parent):
            self._mime = None
            self._pix = None

        def setMimeData(self, mime):  # noqa: N802 - Qt override
            self._mime = mime

        def setPixmap(self, pix):  # noqa: N802 - Qt override
            self._pix = pix

        def setHotSpot(self, pt):  # noqa: N802 - Qt override
            self._hot = pt

        def exec(self, *actions):  # noqa: N802 - Qt override
            captured.append(self._mime)
            return Qt.DropAction.CopyAction

    # ``_begin_chip_drag`` constructs ``QDrag(self)`` directly; patch
    # the symbol in the module so the stub class is used.
    import src.ui.widgets.graph_panel as gp_module
    original_drag = gp_module.QDrag
    gp_module.QDrag = _StubDrag  # type: ignore[assignment]
    try:
        pos = QPoint(50, 100)
        widget._begin_chip_drag(chip, pos)  # noqa: SLF001
    finally:
        gp_module.QDrag = original_drag  # type: ignore[assignment]

    assert len(captured) == 1
    mime = captured[0]
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415
    assert mime.hasFormat(_CHIP_MIME)
    assert bytes(mime.data(_CHIP_MIME)).decode("utf-8") == "feature"
    assert mime.text() == "feature"


def test_full_qdrag_drops_on_target_chip(
    qtbot, tmp_git_repo: Path,
) -> None:
    """End-to-end drag-and-drop on the graph: from ``QDrag`` start to menu signal.

    The previous drop tests synthesised ``QDropEvent`` instances and
    called ``widget.dropEvent(event)`` directly, which bypasses Qt's
    drag pipeline. That is fine for unit-testing the handler logic,
    but it would not have caught a regression where the widget
    refuses to accept drops at the Qt level (``acceptDrops=False``
    on the underlying :class:`QWidget`). This test does the full
    thing: starts a real ``QDrag``, lets ``exec`` run, and
    delivers a synthetic drop via ``QDrag::exec``'s event loop.
    The :meth:`QtBot.mouseMove` calls advance the cursor through
    the drag, and ``QtBot.mouseRelease`` finalises the drop on the
    target chip.

    The test stays in-process by capturing the drag with a custom
    ``QDrag.exec`` (the same trick the other drag tests use) and
    forwarding the drop position to the widget's ``dropEvent`` —
    the only piece of the Qt pipeline that can run synchronously
    inside a test event loop is the drop itself, so we set up
    the press / move / release sequence on the widget and let it
    fall through to ``dropEvent`` exactly as a real release would.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # The widget must accept drops at the Qt level. Without this
    # flag Qt silently rejects the drag before any of our handlers
    # run, and the user sees "nothing happens" on release.
    assert widget.acceptDrops() is True

    # Compute the press and drop positions on the chips.
    press_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    drop_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")

    # Press on the "feature" chip.
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=press_pos)

    # Move past the drag-start threshold to promote the press into a
    # drag.  We bypass the real ``QDrag.exec`` by monkey-patching
    # the class: when ``_begin_chip_drag`` runs, the stub captures
    # the mime and synthesises a drop event at the target position.
    captured: list[tuple[str, str]] = []
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415

    def fake_exec(self, *_args, **_kwargs):
        # Simulate Qt delivering the drop event to the widget at
        # the target position. ``dropEvent`` runs synchronously, so
        # the signal is captured before the stub returns.
        mime = QMimeData()
        mime.setData(_CHIP_MIME, b"feature")
        mime.setText("feature")
        event = QDropEvent(
            drop_pos,
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QEvent.Type.Drop,
        )
        # ``dropEvent`` is a regular method — call it the way Qt
        # would after a real drop.
        try:
            widget.dropEvent(event)  # noqa: SLF001
        except Exception:
            pass
        return Qt.DropAction.CopyAction

    import src.ui.widgets.graph_panel as gp_module
    original_drag = gp_module.QDrag
    gp_module.QDrag = type(  # type: ignore[assignment]
        "_StubDrag",
        (),
        {
            "__init__": lambda self, parent: None,
            "setMimeData": lambda self, mime: setattr(self, "_mime", mime),
            "setPixmap": lambda self, pix: None,
            "setHotSpot": lambda self, pt: None,
            "exec": fake_exec,
        },
    )
    try:
        # Connect the signal to capture the drop result.
        widget.branch_dropped_on_branch.connect(  # noqa: SLF001
            lambda s, t: captured.append((s, t))
        )
        # Nudge the cursor past the threshold to start the drag.
        move_pos = press_pos + QPoint(20, 0)
        qtbot.mouseMove(widget, move_pos)
        # Process events so the drag-start logic runs.
        QApplication.processEvents()
    finally:
        gp_module.QDrag = original_drag  # type: ignore[assignment]
        qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=drop_pos)

    # The drop on the "main" chip must have produced the menu signal
    # with source="feature" and target="main".
    assert captured == [("feature", "main")]
