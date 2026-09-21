"""Editor saves update both file lists without automatically staging new content."""
from pathlib import Path

import pygit2
import pytest
from src.ui.widgets.commit_panel import CommitPanel
from src.utils.config import save_config
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.fixture
def monitored(qtbot, committed_repo, tmp_path):
    config = tmp_path / "settings.json"
    save_config(config, {"worktree_refresh_interval_ms": 100})
    vm = MainViewModel(config_path=config, async_enabled=True)
    vm.set_repository(committed_repo)
    try:
        yield vm
    finally:
        vm.stop_worktree_refresh()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None, timeout=6000)
        qtbot.waitUntil(lambda: not vm._active_workers, timeout=6000)
        qtbot.waitUntil(lambda: not vm.commit_panel_view_model()._diff_loader._workers)


def test_saved_staged_file_appears_on_both_sides_without_focus_or_manual_refresh(
    qtbot, monitored,
):
    vm = monitored
    manager = vm.repository_manager()
    root = Path(manager.path)
    widget = CommitPanel(vm)
    qtbot.addWidget(widget)
    panel = vm.commit_panel_view_model()
    busy = []
    vm.busy_changed.connect(busy.append)
    (root / "hello.txt").write_bytes(b"staged version\n")
    qtbot.waitUntil(lambda: widget._unstaged_list.model().rowCount() == 1)
    widget._stage_all_button.click()
    assert widget._unstaged_list.model().rowCount() == 0
    staged_tree = manager.repo.index.write_tree()
    panel.set_commit_summary("Keep my message")

    (root / "hello.txt").write_bytes(b"saved after staging\n")
    nested = root / "new" / "nested"
    nested.mkdir(parents=True)
    (nested / "file.txt").write_bytes(b"new unstaged file\n")
    qtbot.waitUntil(lambda: set(panel.unstaged_paths()) == {"hello.txt", "new/nested/file.txt"})
    assert widget._unstaged_list.model().rowCount() == 2
    assert widget._staged_list.model().rowCount() == 1
    assert panel.staged_files() == ["hello.txt"]
    fresh = pygit2.Repository(str(root))
    assert fresh.index.write_tree() == staged_tree
    assert panel.commit_summary() == "Keep my message"
    assert not busy
    assert not vm.command_processor().can_undo

    # Commit still records the explicitly staged version, leaving the later save.
    vm.commit_changes("Only staged content")
    assert fresh.head.peel().tree_id == staged_tree
    assert "hello.txt" in panel.unstaged_paths()
    assert panel.staged_files() == []


