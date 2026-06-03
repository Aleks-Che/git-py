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
from PySide6.QtWidgets import QGraphicsEllipseItem
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
    from PySide6.QtWidgets import QGraphicsPathItem
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

    # The graph widget should now show two nodes.
    nodes = [
        it for it in window._graph_widget.scene().items()  # noqa: SLF001
        if isinstance(it, QGraphicsEllipseItem)
    ]
    assert len(nodes) == 2
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
    wip_node = widget._node_items["WIP"]  # noqa: SLF001
    assert wip_node.data(0) == "WIP"
