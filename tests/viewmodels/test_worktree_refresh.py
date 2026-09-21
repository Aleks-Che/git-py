"""External saves must update WIP without staging or replacing newer UI state."""
from pathlib import Path
from threading import Event, get_ident

import pytest
from src.core.exceptions import GitError
from src.core.worktree_status import read_worktree_status
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


def test_repeated_saves_refresh_selected_diff_without_resetting_lists(qtbot, monitored):
    vm = monitored
    root = Path(vm.repository_manager().path)
    panel = vm.commit_panel_view_model()
    (root / "hello.txt").write_bytes(b"first edit\n")
    qtbot.waitUntil(lambda: panel.unstaged_paths() == ["hello.txt"])
    panel.select_file("hello.txt")
    qtbot.waitUntil(lambda: "first edit" in (panel.current_diff() or ""))
    changes = []
    panel.file_changes_changed.connect(lambda: changes.append(1))

    (root / "hello.txt").write_bytes(b"second editor save\n")
    qtbot.waitUntil(lambda: "second editor save" in (panel.current_diff() or ""))
    assert panel.selected_file() == "hello.txt"
    assert not changes


def test_focus_refresh_during_busy_runs_after_operation(qtbot, monitored):
    vm = monitored
    panel = vm.commit_panel_view_model()
    vm._is_busy = True
    vm.busy_changed.emit(True)
    (Path(vm.repository_manager().path) / "hello.txt").write_bytes(b"external save\n")
    vm.refresh_state()
    assert panel.unstaged_paths() == []
    assert vm._refresh_pending
    # Stop polling to prove the deferred focus request itself updates the lists.
    vm._worktree_refresh_timer.stop()
    vm._is_busy = False
    vm.busy_changed.emit(False)
    qtbot.waitUntil(lambda: panel.unstaged_paths() == ["hello.txt"])
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert not vm._refresh_pending


@pytest.mark.parametrize("action", ["stage", "switch", "stop", "busy"])
def test_slow_monitor_is_bounded_and_cannot_overwrite_newer_state(
    qtbot, monitored, monkeypatch, action,
):
    vm = monitored
    manager = vm.repository_manager()
    root = Path(manager.path)
    (root / "hello.txt").write_bytes(b"external edit\n")
    panel = vm.commit_panel_view_model()
    started, release = Event(), Event()
    calls = []
    gui_thread = get_ident()

    def slow_read(reader, selected, staged):
        # The module-level patch also sees timers from other live ViewModels.
        # Only delay/count the repository whose re-entrancy guard we test.
        if reader.path != manager.path:
            return read_worktree_status(reader, selected, staged)
        assert reader is not manager
        snapshot = read_worktree_status(reader, selected, staged)
        calls.append(get_ident())
        started.set()
        assert release.wait(5)
        return snapshot

    monkeypatch.setattr("src.viewmodels.main_viewmodel.read_worktree_status", slow_read)
    try:
        vm.refresh_worktree()
        qtbot.waitUntil(started.is_set)
        for _ in range(10):
            vm.refresh_worktree()
        assert len(calls) == 1 and calls[0] != gui_thread
        if action == "stage":
            vm.stage_file("hello.txt")
        elif action == "switch":
            vm.set_repository(None)
        elif action == "stop":
            vm.stop_worktree_refresh()
        else:
            vm._is_busy = True
            vm.busy_changed.emit(True)
        expected = (panel.unstaged_paths(), panel.staged_files())
        vm._worktree_refresh_timer.stop()
    finally:
        release.set()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)
    assert (panel.unstaged_paths(), panel.staged_files()) == expected


def test_monitor_error_preserves_lists_and_retries(qtbot, monitored, monkeypatch):
    vm = monitored
    panel = vm.commit_panel_view_model()
    repo = vm.repository_manager().repo
    original = repo[repo.head.peel().tree["hello.txt"].id].data
    (Path(vm.repository_manager().path) / "hello.txt").write_bytes(b"save\n")
    qtbot.waitUntil(lambda: panel.unstaged_paths() == ["hello.txt"])
    errors, calls = [], []
    vm.error_occurred.connect(errors.append)

    def fail(reader, selected, staged):
        if reader.path != vm.repository_manager().path:
            return read_worktree_status(reader, selected, staged)
        calls.append(1)
        raise GitError("temporarily unavailable")

    monkeypatch.setattr("src.viewmodels.main_viewmodel.read_worktree_status", fail)
    qtbot.waitUntil(lambda: len(calls) >= 2 and bool(errors))
    assert len(errors) == 1
    assert panel.unstaged_paths() == ["hello.txt"]
    monkeypatch.setattr(
        "src.viewmodels.main_viewmodel.read_worktree_status", read_worktree_status,
    )
    (Path(vm.repository_manager().path) / "hello.txt").write_bytes(original)
    qtbot.waitUntil(lambda: panel.unstaged_paths() == [])
