"""Clicking sibling WIP opens its own checkout in a reusable repository tab."""
from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from src.ui.main_window import MainWindow
from src.ui.widgets.graph_panel import _row_subject
from src.utils.config import save_config


def test_graph_click_and_button_open_sibling_tab_and_preserve_original(
    qtbot, committed_repo, linked_worktree, tmp_path,
):
    (Path(linked_worktree.path) / "hello.txt").write_text("linked edit\n")
    config = tmp_path / "window.json"
    save_config(config, {"worktree_refresh_interval_ms": 60_000})
    window = MainWindow(config_path=config)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(10)
    window.set_repository(committed_repo)
    vm = window.main_view_model()
    graph = window._graph_table
    entry = vm.graph_view_model().other_worktrees()[0]
    row_index, row = next(
        (i, row) for i, row in enumerate(graph._rows) if row.get("worktree")
    )
    assert entry.branch in _row_subject(row)
    y = graph._cfg.header_height + row_index * graph._cfg.row_height + graph._cfg.row_height // 2
    qtbot.mouseClick(graph, Qt.MouseButton.LeftButton, pos=QPoint(graph.width() - 30, y))
    right = window._right_panel
    assert vm.selected_commit_sha() == entry.node_id
    assert right._stack.currentWidget() is right._worktree_panel
    assert entry.path in right._worktree_info.text()
    assert entry.branch in right._worktree_info.text()
    assert not right._commit_input.isVisible()
    assert not right._commit_detail.isVisible()
    menu = graph._build_node_menu(entry.node_id, "wip")
    assert [action.text() for action in menu.actions()] == ["Open worktree in new tab"]
    selection = [entry.node_id, str(committed_repo.repo.head.target)]
    assert not graph._squash_range_validity(selection)[0]

    try:
        qtbot.wait(20)  # allow the newly shown stack page to finish layout
        assert right._open_worktree_button.isVisible()
        assert right._open_worktree_button.isEnabled()
        with qtbot.waitSignal(vm.open_worktree_requested):
            qtbot.mouseClick(right._open_worktree_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not vm.is_busy())
        assert Path(vm.repository_manager().path) == Path(linked_worktree.path)
        assert window.repo_tabs_view_model().count == 2
        assert Path(window.repo_tabs_view_model().tabs[0]) == Path(committed_repo.path)
        assert vm.commit_panel_view_model().unstaged_paths() == ["hello.txt"]
        assert any(row["sha"] == "WIP" for row in graph._rows)
        assert vm.graph_view_model().other_worktrees() == []
        # Return to the original tab; the same worktree is activated, not duplicated.
        window.repo_tabs_view_model().set_active_tab(0)
        qtbot.waitUntil(lambda: not vm.is_busy())
        entry = vm.graph_view_model().other_worktrees()[0]
        menu = graph._build_node_menu(entry.node_id, "wip")
        menu.actions()[0].trigger()
        qtbot.waitUntil(lambda: not vm.is_busy())
        assert window.repo_tabs_view_model().count == 2
        assert Path(vm.repository_manager().path) == Path(linked_worktree.path)
    finally:
        window.close()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)
        qtbot.waitUntil(lambda: not vm._active_workers)


def test_refresh_removes_selected_worktree_and_hides_navigation(
    qtbot, committed_repo, linked_worktree, tmp_path,
):
    path = Path(linked_worktree.path) / "hello.txt"
    original = path.read_bytes()
    path.write_text("dirty\n")
    window = MainWindow(config_path=tmp_path / "window.json")
    qtbot.addWidget(window)
    qtbot.wait(10)
    window.set_repository(committed_repo)
    vm = window.main_view_model()
    entry = vm.graph_view_model().other_worktrees()[0]
    vm.select_commit(entry.node_id)
    assert not window._right_panel.isHidden()
    path.write_bytes(original)
    vm.graph_view_model().refresh_graph()
    assert vm.selected_commit_sha() is None
    assert window._right_panel.isHidden()
    window.close()
