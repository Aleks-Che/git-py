"""Branch transfers through the Qt event pipeline and edge scrolling."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMenu
from src.ui.main_window import MainWindow
from src.ui.widgets.branch_drag import (
    LOCAL_BRANCH,
    REMOTE_BRANCH,
    branch_mime_data,
    read_branch_mime,
)
from src.ui.widgets.graph_panel import GraphTableWidget
from src.ui.widgets.left_panel import _KIND_LOCAL_BRANCH, _ROLE_NAME
from src.viewmodels.graph_viewmodel import GraphViewModel


def _send_drag(target, mime, point, *, drop=True):
    enter = QDragEnterEvent(point, Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, enter)
    assert enter.isAccepted()
    event_type = QDropEvent if drop else QDragMoveEvent
    position = QPointF(point) if drop else point
    event = event_type(position, Qt.DropAction.CopyAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, event)
    return event


@pytest.fixture
def branch_window(qtbot, committed_repo):
    committed_repo.repo.create_branch("feature", committed_repo.repo.head.peel().parents[0])
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1200, 800)
    window.show()
    window.set_repository(committed_repo)
    yield window
    window.close()


@pytest.mark.parametrize("source_surface", ["tree", "graph"])
@pytest.mark.parametrize("target_surface", ["tree", "graph"])
def test_branch_transfer_opens_same_menu(
    branch_window, monkeypatch, source_surface, target_surface,
):
    window = branch_window
    tree = window._left_panel
    graph = window._graph_table
    source_item = tree._find_child(tree._group_local, _KIND_LOCAL_BRANCH, "feature")
    mime = (tree.mimeData([source_item]) if source_surface == "tree"
            else branch_mime_data("feature", LOCAL_BRANCH))
    menus, merges = [], []
    monkeypatch.setattr(window._main_vm, "merge_branch", lambda *a, **k: merges.append((a, k)))

    def choose_merge():
        menu = QApplication.activePopupWidget()
        assert isinstance(menu, QMenu)
        try:
            menus.append([action.text() for action in menu.actions()])
            menu.actions()[0].trigger()
        finally:
            menu.close()
    if target_surface == "tree":
        item = tree._find_child(tree._group_local, _KIND_LOCAL_BRANCH, "main")
        point = tree.visualItemRect(item).center()
        target = tree.viewport()
    else:
        graph.repaint()
        chip = next(c for c in graph._branch_chip_rects.values() if c["full_name"] == "main")
        point = chip["rect"].center() - QPoint(graph._h_scrolls[0], 0)
        target = graph
    QTimer.singleShot(0, choose_merge)
    event = _send_drag(target, mime, point)
    assert event.isAccepted()
    assert menus == [["Merge feature into main", "Rebase feature onto main"]]
    assert merges == [(("feature",), {"target": "main", "no_ff": True})]
    assert tree._find_child(tree._group_local, _KIND_LOCAL_BRANCH, "feature") is not None


def test_tree_starts_copy_drag_and_rejects_group(branch_window, monkeypatch):
    tree = branch_window._left_panel
    assert tree.dragEnabled() and tree.acceptDrops()
    drags = []

    def make_drag(parent):
        drag = Mock()
        drags.append(drag)
        return drag

    monkeypatch.setattr("src.ui.widgets.left_panel.QDrag", make_drag)
    item = tree._find_child(tree._group_local, _KIND_LOCAL_BRANCH, "main")
    tree.setCurrentItem(item)
    tree.startDrag(Qt.DropAction.MoveAction | Qt.DropAction.CopyAction)
    assert read_branch_mime(drags[0].setMimeData.call_args.args[0]) == ("main", LOCAL_BRANCH)
    drags[0].exec.assert_called_once_with(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)
    assert item.data(0, _ROLE_NAME) == "main"
    tree.setCurrentItem(tree._group_local)
    tree.startDrag(Qt.DropAction.CopyAction)
    assert len(drags) == 1


def test_graph_remote_drag_keeps_full_ref(branch_window, monkeypatch):
    graph = branch_window._graph_table
    drag = Mock()
    monkeypatch.setattr("src.ui.widgets.graph_panel.QDrag", lambda parent: drag)
    graph._begin_chip_drag({
        "full_name": "upstream/topic", "display": "topic", "is_remote": True,
        "rect": QRect(0, 40, 80, 20),
    }, QPoint(10, 50))
    assert read_branch_mime(drag.setMimeData.call_args.args[0]) == (
        "upstream/topic", REMOTE_BRANCH,
    )
    assert not graph._drag_scroll_timer.isActive()


@pytest.mark.parametrize("payload", ["plain", "bad_kind", "bad_utf8"])
def test_tree_rejects_non_branch_payload(branch_window, payload):
    mime = QMimeData()
    mime.setText("feature")
    if payload != "plain":
        mime.setData("application/x-git-py-branch-kind",
                     b"tag" if payload == "bad_kind" else b"\xff")
    event = QDragEnterEvent(QPoint(30, 50), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(branch_window._left_panel.viewport(), event)
    assert not event.isAccepted()


def test_drag_scroll_reaches_unloaded_target_and_stops(qtbot, tmp_git_repo, make_commit):
    parents = []
    first = None
    for i in range(35):
        oid = make_commit(f"commit {i}", parents=parents)
        first = first or oid
        parents = [oid]
    from src.core.repository import RepositoryManager

    repo = RepositoryManager(str(tmp_git_repo))
    repo.repo.create_branch("target", repo.repo[first])
    vm = GraphViewModel(history_limit=10)
    graph = GraphTableWidget(vm)
    qtbot.addWidget(graph)
    graph.resize(900, 220)
    graph.show()
    vm.set_repository(repo)
    mime = branch_mime_data("main", LOCAL_BRANCH)
    bottom = QPoint(40, graph.height() - 24)
    _send_drag(graph, mime, bottom, drop=False)
    # No further mouse motion: the timer must keep scrolling and loading pages.
    qtbot.waitUntil(lambda: vm.truncated_count == 0 and
                    graph.scrollbar().value() == graph.scrollbar().maximum(), timeout=6000)
    graph.repaint()
    chip = next(c for c in graph._branch_chip_rects.values() if c["full_name"] == "target")
    with qtbot.waitSignal(graph.branch_dropped_on_branch) as signal:
        _send_drag(graph, mime, chip["rect"].center())
    assert signal.args == ["main", "target"]
    assert not graph._drag_scroll_timer.isActive()
    old_value = graph.scrollbar().value()
    _send_drag(graph, mime, QPoint(40, graph._cfg.header_height + 3), drop=False)
    qtbot.waitUntil(lambda: graph.scrollbar().value() < old_value)
    QApplication.sendEvent(graph, QDragLeaveEvent())
    assert not graph._drag_scroll_timer.isActive()
    old_value = graph.scrollbar().value()
    qtbot.wait(100)
    assert graph.scrollbar().value() == old_value
    _send_drag(graph, mime, bottom, drop=False)
    _send_drag(graph, mime, QPoint(100, graph.height() // 2), drop=False)
    assert not graph._drag_scroll_timer.isActive()
    _send_drag(graph, mime, bottom, drop=False)
    graph.hide()
    assert not graph._drag_scroll_timer.isActive()
