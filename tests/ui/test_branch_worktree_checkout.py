"""Branch checkout gestures navigate to an existing worktree without changing Git."""
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from src.ui.main_window import MainWindow
from src.utils.config import save_config


def _disk_state(manager):
    git_dir = Path(manager.repo.path)
    return tuple(path.read_bytes() for path in (
        git_dir / "HEAD", git_dir / "index", Path(manager.path) / "hello.txt",
    ))


def _checkout(qtbot, window, branch, gesture):
    if gesture.startswith("graph"):
        graph = window._graph_table
        graph.repaint()
        chip = next(c for c in graph._branch_chip_rects.values() if c["full_name"] == branch)
        if gesture == "graph_menu":
            graph._build_branch_menu_actions(chip)[0].trigger()
        else:
            center = chip["rect"].center()
            pos = QPoint(int(center.x() - graph._h_scrolls[0]), int(center.y()))
            qtbot.mouseDClick(graph, Qt.MouseButton.LeftButton, pos=pos)
    elif gesture == "left_menu":
        actions = window._left_panel._local_branch_actions(branch)
        next(a for a in actions if a.text() == "Checkout").trigger()
    else:
        panel = window._left_panel
        item = panel.findItems(branch, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)[0]
        panel.scrollToItem(item)
        qtbot.mouseClick(panel.viewport(), Qt.MouseButton.LeftButton,
                         pos=panel.visualItemRect(item).center())
        qtbot.mouseDClick(panel.viewport(), Qt.MouseButton.LeftButton,
                         pos=panel.visualItemRect(item).center())


@pytest.mark.parametrize("gesture", ["graph_double_click", "graph_menu", "left_double_click",
                                    "left_menu"])
@pytest.mark.parametrize("dirty", [False, True])
def test_checkout_opens_or_reuses_worktree_tab(
    qtbot, committed_repo, linked_worktree, tmp_path, gesture, dirty,
):
    # Separate graph rows make the worktree's branch chip directly clickable.
    repo = linked_worktree.repo
    head = repo.head.peel()
    repo.create_commit("HEAD", head.author, head.committer, "linked tip", head.tree_id, [head.id])
    branch = repo.head.shorthand
    (Path(committed_repo.path) / "hello.txt").write_bytes(b"main uncommitted\n")
    if dirty:
        (Path(linked_worktree.path) / "hello.txt").write_bytes(b"linked staged\n")
        repo.index.add("hello.txt")
        repo.index.write()
        (Path(linked_worktree.path) / "hello.txt").write_bytes(b"linked unstaged\n")
    before = (_disk_state(committed_repo), _disk_state(linked_worktree))
    config = tmp_path / "window.json"
    save_config(config, {"worktree_refresh_interval_ms": 60_000})
    window = MainWindow(config_path=config)
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(10)
    window.set_repository(committed_repo)
    vm = window.main_view_model()
    errors = []
    vm.error_occurred.connect(errors.append)
    tabs = window.repo_tabs_view_model()
    try:
        for _ in range(2):
            qtbot.wait(20)
            _checkout(qtbot, window, branch, gesture)
            qtbot.waitUntil(lambda: not vm.is_busy())
            assert errors == []
            assert Path(vm.repository_manager().path) == Path(linked_worktree.path)
            assert tabs.count == 2
            assert Path(tabs.active_path) == Path(linked_worktree.path)
            assert (_disk_state(committed_repo), _disk_state(linked_worktree)) == before
            assert vm.command_processor().undo_stack_snapshot() == []
            # The main checkout is also a worktree and already has a tab.
            window._graph_table.checkout_branch_requested.emit("main")
            qtbot.waitUntil(lambda: not vm.is_busy())
            assert Path(vm.repository_manager().path) == Path(committed_repo.path)
            assert tabs.count == 2
        assert errors == []
    finally:
        window.close()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None and not vm._active_workers)


def test_graph_checkout_local_branch_with_slashes_uses_checkout_command(
    qtbot, committed_repo, tmp_path,
):
    name = "agents-ide/run/free-branch"
    committed_repo.repo.create_branch(name, committed_repo.repo.head.peel())
    window = MainWindow(config_path=tmp_path / "window.json")
    qtbot.addWidget(window)
    window.set_repository(committed_repo)
    vm = window.main_view_model()
    errors = []
    vm.error_occurred.connect(errors.append)
    try:
        window._graph_table.checkout_branch_requested.emit(name)
        qtbot.waitUntil(lambda: not vm.is_busy())
        assert errors == []
        assert committed_repo.repo.head.shorthand == name
        assert window.repo_tabs_view_model().count == 1
        assert len(vm.command_processor().undo_stack_snapshot()) == 1
        vm.undo()
        assert committed_repo.repo.head.shorthand == "main"
    finally:
        window.close()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None and not vm._active_workers)


def test_graph_remote_checkout_keeps_fetch_path(qtbot, committed_repo, tmp_path, monkeypatch):
    window = MainWindow(config_path=tmp_path / "window.json")
    qtbot.addWidget(window)
    window.set_repository(committed_repo)
    vm = window.main_view_model()
    calls = []
    monkeypatch.setattr(vm, "fetch_and_checkout_remote_branch", calls.append)
    try:
        window._graph_table.checkout_branch_requested.emit("origin/feature/nested")
        assert calls == ["origin/feature/nested"]
        assert committed_repo.repo.head.shorthand == "main"
        assert vm.command_processor().undo_stack_snapshot() == []
    finally:
        window.close()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None and not vm._active_workers)
