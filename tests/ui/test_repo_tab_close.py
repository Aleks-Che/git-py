"""Close glyphs must act on their own repository, including linked worktrees."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabBar
from src.ui.main_window import MainWindow
from src.ui.widgets.repo_bar_widget import RepoBarWidget
from src.utils.config import save_config
from src.viewmodels.repo_tabs_viewmodel import RepoTabViewModel


@pytest.mark.parametrize("close_index", [0, 1, 2])
@pytest.mark.parametrize("reordered", [False, True])
def test_close_glyph_removes_clicked_tab(qtbot, tmp_path, close_index, reordered):
    vm = RepoTabViewModel()
    for name in ("main-repository", "linked-worktree", "another-repository"):
        vm.add_tab(str(tmp_path / name))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)
    bar.resize(900, 40)
    bar.show()
    qtbot.waitExposed(bar)
    tabs = bar._tab_bar
    original_active = vm.active_path
    if reordered:
        tabs.moveTab(2, 0)
    paths = [tabs.tabData(i) for i in range(tabs.count())]
    active_path = tabs.tabData(tabs.currentIndex())
    assert vm.active_path == active_path == original_active
    button = tabs.tabButton(close_index, QTabBar.ButtonPosition.RightSide)

    qtbot.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())

    expected = paths[:close_index] + paths[close_index + 1:]
    assert vm.tabs == expected
    assert [tabs.tabData(i) for i in range(tabs.count())] == expected
    if paths[close_index] != active_path:
        assert vm.active_path == active_path
    assert tabs.tabData(tabs.currentIndex()) == vm.active_path

    # A second click uses the new buttons and indices after rebuilding the bar.
    button = tabs.tabButton(1, QTabBar.ButtonPosition.RightSide)
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
    assert vm.tabs == expected[:1]


@pytest.mark.parametrize("reordered", [False, True])
def test_close_worktree_tab_returns_to_original_repository(
    qtbot, committed_repo, linked_worktree, tmp_path, reordered,
):
    edited_file = Path(linked_worktree.path) / "hello.txt"
    edited_file.write_text("linked edit\n")
    original_head = str(committed_repo.repo.head.target)
    worktree_head = str(linked_worktree.repo.head.target)
    config = tmp_path / "window.json"
    save_config(config, {"worktree_refresh_interval_ms": 60_000})
    window = MainWindow(config_path=config)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(10)  # Finish restoring the empty session before opening a repo.
    vm = window.main_view_model()
    try:
        window.set_repository(committed_repo)
        entry = vm.graph_view_model().other_worktrees()[0]
        vm.select_commit(entry.node_id)
        qtbot.wait(20)  # Allow the newly selected panel to finish layout.
        with qtbot.waitSignal(vm.open_worktree_requested):
            qtbot.mouseClick(window._right_panel._open_worktree_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(lambda: not vm.is_busy())
        assert Path(vm.repository_manager().path) == Path(linked_worktree.path)
        tabs = window._repo_bar._tab_bar
        assert tabs.count() == 2
        if reordered:
            tabs.moveTab(1, 0)
            assert Path(vm.repository_manager().path) == Path(linked_worktree.path)
        button = tabs.tabButton(0 if reordered else 1, QTabBar.ButtonPosition.RightSide)

        qtbot.mouseClick(button, Qt.MouseButton.LeftButton, pos=button.rect().center())
        qtbot.waitUntil(lambda: not vm.is_busy())

        assert window.repo_tabs_view_model().tabs == [Path(committed_repo.path).as_posix()]
        assert Path(vm.repository_manager().path) == Path(committed_repo.path)
        assert tabs.count() == 1
        assert edited_file.read_text() == "linked edit\n"
        assert str(committed_repo.repo.head.target) == original_head
        assert str(linked_worktree.repo.head.target) == worktree_head
    finally:
        window.close()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)
        qtbot.waitUntil(lambda: not vm._active_workers)
