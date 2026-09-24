"""Tracking commands report failures and preserve their Undo/Redo history."""
from pathlib import Path

import pygit2
import pytest
from src.viewmodels.commands import StopTrackingCommand
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.fixture
def tracking_vm(qtbot, committed_repo, tmp_path):
    root = Path(committed_repo.path)
    (root / ".gitignore").write_text("hello.txt\n", encoding="utf-8")
    vm = MainViewModel(config_path=tmp_path / "config.json")
    vm.set_repository(committed_repo)
    vm._worktree_refresh_timer.stop()
    return vm, committed_repo, root


@pytest.mark.parametrize("direction", ["undo", "redo"])
def test_tracking_history_survives_failed_undo_redo(tracking_vm, direction):
    vm, repo, root = tracking_vm
    errors = []
    vm.error_occurred.connect(errors.append)
    vm.stop_tracking_files(["hello.txt"])
    processor = vm.command_processor()
    command = processor.peek_undo_command()
    assert isinstance(command, StopTrackingCommand)
    if direction == "redo":
        vm.undo()
    (root / "hello.txt").write_bytes(b"external staged content")
    external = pygit2.Repository(repo.path)
    external.index.add("hello.txt")
    external.index.write()
    before = (Path(repo.repo.path) / "index").read_bytes()

    getattr(vm, direction)()

    assert errors and "Index changed" in errors[-1]
    peek = processor.peek_undo_command if direction == "undo" else processor.peek_redo_command
    assert peek() is command
    assert (Path(repo.repo.path) / "index").read_bytes() == before
    assert (root / "hello.txt").read_bytes() == b"external staged content"


def test_tracking_busy_guard_and_no_repository(qtbot, tracking_vm, tmp_path):
    vm, repo, _root = tracking_vm
    errors = []
    vm.error_occurred.connect(errors.append)
    vm._is_busy = True
    before = (Path(repo.repo.path) / "index").read_bytes()
    assert not vm.can_stop_tracking(["hello.txt"])
    assert errors == []
    vm.stop_tracking_files(["hello.txt"])
    assert errors and "operation is in progress" in errors[-1]
    assert (Path(repo.repo.path) / "index").read_bytes() == before
    assert not vm.command_processor().can_undo
    vm._is_busy = False
    empty_vm = MainViewModel(config_path=tmp_path / "empty.json")
    empty_vm.error_occurred.connect(errors.append)
    assert not empty_vm.can_stop_tracking(["hello.txt"])
    empty_vm.stop_tracking_files(["hello.txt"])
    assert errors[-1] == "No repository open."


def test_tracking_finishes_editor_before_removing_index_entry(tracking_vm, monkeypatch):
    vm, repo, _root = tracking_vm
    monkeypatch.setattr(vm.commit_panel_view_model().file_editor, "finish_editing", lambda: False)
    vm.stop_tracking_files(["hello.txt"])
    assert "hello.txt" in repo.repo.index
    assert not vm.command_processor().can_undo


def test_tracking_runs_off_gui_thread_and_blocks_reentry(qtbot, tracking_vm, monkeypatch):
    from threading import Event, get_ident

    vm, _repo, _root = tracking_vm
    vm._async_enabled = True
    release = Event()
    entered = Event()
    threads = []
    original = StopTrackingCommand.execute

    def delayed(command):
        threads.append(get_ident())
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Test did not release the tracking worker")
        original(command)

    monkeypatch.setattr(StopTrackingCommand, "execute", delayed)
    errors = []
    vm.error_occurred.connect(errors.append)
    vm.stop_tracking_files(["hello.txt"])
    try:
        qtbot.waitUntil(entered.is_set)
        assert vm.is_busy()
        assert len(threads) == 1 and threads[0] != get_ident()
        vm.stop_tracking_files(["hello.txt"])
        assert errors and "operation is in progress" in errors[-1]
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm.is_busy())
    assert len(vm.command_processor().undo_stack_snapshot()) == 1
