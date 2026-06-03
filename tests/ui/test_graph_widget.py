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
    """A remote-tracking ref collides with local — only the local is shown."""
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
    # Both local "main" and remote "origin/main" resolve to "main"
    # after prefix stripping, so the remote one is suppressed.
    texts = _branch_label_texts(widget, head_sha)
    assert texts == ["main"]
    # One chip (local main) + checkmark + monitor.
    assert len(_branch_label_chips(widget, head_sha)) == 1
    assert len(_branch_label_icons(widget, head_sha)) == 2


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
