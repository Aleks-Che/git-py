"""Repair participates in command dispatch and respects mutation/editor guards."""
import pytest
from src.viewmodels.commands import RepairFileStatusCommand
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.mark.parametrize("blocked", ["busy", "editor"])
def test_repair_guard_does_not_dispatch(qapp, committed_repo, tmp_path, monkeypatch, blocked):
    vm = MainViewModel(config_path=tmp_path / "config.json", async_enabled=False)
    vm.set_repository(committed_repo)
    vm._worktree_refresh_timer.stop()
    calls, errors = [], []
    vm.error_occurred.connect(errors.append)
    monkeypatch.setattr(vm.command_processor(), "execute", calls.append)
    if blocked == "busy":
        vm._is_busy = True
    else:
        monkeypatch.setattr(
            vm.commit_panel_view_model().file_editor, "finish_editing", lambda: False,
        )
    vm.repair_file_status(["hello.txt"])
    assert calls == []
    assert bool(errors) == (blocked == "busy")


def test_repair_goes_through_processor_without_undo_entry(
    qapp, committed_repo, make_stale_index, tmp_path, monkeypatch,
):
    paths = make_stale_index(committed_repo)
    vm = MainViewModel(config_path=tmp_path / "config.json", async_enabled=False)
    vm.set_repository(committed_repo)
    vm._worktree_refresh_timer.stop()
    processor = vm.command_processor()
    original = processor.execute
    calls = []

    def execute(command):
        calls.append(command)
        original(command)

    monkeypatch.setattr(processor, "execute", execute)
    vm.repair_file_status(paths)
    assert len(calls) == 1 and isinstance(calls[0], RepairFileStatusCommand)
    assert not processor.can_undo
    assert committed_repo.get_raw_status() == {}
