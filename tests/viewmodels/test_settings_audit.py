"""Regression coverage for runtime configuration and bounded discard backups."""

from pathlib import Path

import pytest
from src.core.exceptions import GitError
from src.utils.config import save_config
from src.viewmodels.commands import CommandProcessor, DiscardFileCommand
from src.viewmodels.main_viewmodel import MainViewModel


def test_runtime_components_use_selected_config(qtbot, tmp_path, committed_repo, monkeypatch):
    config_path = tmp_path / "selected.json"
    save_config(config_path, {
        "auto_fetch_enabled": True,
        "auto_fetch_interval_ms": 360_000,
        "merge_async_threshold": 7,
        "graph_history_limit": 1,
        "command_processor_history_size": 1,
        "discard_file_max_backup_bytes": 4,
    })

    def forbidden_default_path():
        pytest.fail("A component ignored the selected config_path")

    monkeypatch.setattr("src.utils.config.default_config_path", forbidden_default_path)
    monkeypatch.setattr(
        "src.viewmodels.graph_viewmodel.default_config_path", forbidden_default_path,
    )
    monkeypatch.setattr("src.viewmodels.main_viewmodel.default_config_path", forbidden_default_path)
    vm = MainViewModel(config_path=config_path)
    vm.set_repository(committed_repo)
    try:
        assert vm.is_auto_fetch_enabled()
        assert vm._auto_fetch_timer.isActive()
        assert vm._auto_fetch_timer.interval() == 360_000
        assert vm._merge_async_threshold == 7
        assert vm.graph_view_model().history_limit == 1
        assert vm.command_processor().max_undo == 1
        vm.create_branch("first")
        vm.create_branch("second")
        assert len(vm.command_processor().undo_stack_snapshot()) == 1

        path = Path(committed_repo.repo.workdir) / "new.txt"
        path.write_bytes(b"12345")
        errors = []
        vm.error_occurred.connect(errors.append)
        history = vm.command_processor().undo_stack_snapshot()
        vm.discard_file_changes("new.txt")
        assert path.read_bytes() == b"12345"
        assert errors and "backup limit (4 bytes)" in errors[-1]
        assert vm.command_processor().undo_stack_snapshot() == history
    finally:
        vm.set_auto_fetch_enabled(False)


def test_explicit_runtime_arguments_override_config(qtbot, tmp_path):
    config_path = tmp_path / "config.json"
    save_config(config_path, {
        "auto_fetch_enabled": True, "auto_fetch_interval_ms": 20000,
        "merge_async_threshold": 7,
    })
    vm = MainViewModel(
        config_path=config_path, auto_fetch_enabled=False,
        auto_fetch_interval_ms=30000, merge_async_threshold=0,
    )
    assert not vm.is_auto_fetch_enabled()
    assert vm._auto_fetch_timer.interval() == 30000
    assert vm._merge_async_threshold == 0


@pytest.mark.parametrize("size,limit", [(5, 4), (1_048_577, 1_048_576), (1, 0)])
def test_oversized_discard_keeps_file_and_history(qtbot, committed_repo, size, limit):
    path = Path(committed_repo.repo.workdir) / "new.bin"
    content = b"x" * size
    path.write_bytes(content)
    processor = CommandProcessor(max_undo=10)
    command = DiscardFileCommand(committed_repo, "new.bin", max_backup_bytes=limit)
    with pytest.raises(GitError, match="backup limit"):
        processor.execute(command)
    assert path.read_bytes() == content
    assert not processor.can_undo
    assert not processor.can_redo


@pytest.mark.parametrize("size,limit", [(0, 0), (3, 4), (4, 4)])
def test_discard_within_limit_survives_undo_redo(qtbot, committed_repo, size, limit):
    path = Path(committed_repo.repo.workdir) / "new.bin"
    content = b"x" * size
    path.write_bytes(content)
    processor = CommandProcessor(max_undo=10)
    processor.execute(DiscardFileCommand(committed_repo, "new.bin", max_backup_bytes=limit))
    assert not path.exists()
    assert processor.undo()
    assert path.read_bytes() == content
    assert processor.redo()
    assert not path.exists()
    assert processor.undo()
    assert path.read_bytes() == content


def test_discard_undo_preserves_recreated_file_and_can_retry(qtbot, committed_repo):
    path = Path(committed_repo.repo.workdir) / "new.txt"
    path.write_bytes(b"old")
    processor = CommandProcessor(max_undo=10)
    processor.execute(DiscardFileCommand(committed_repo, "new.txt", max_backup_bytes=4))
    path.write_bytes(b"new external content")
    errors = []
    processor.error_occurred.connect(errors.append)
    assert not processor.undo()
    assert errors
    assert path.read_bytes() == b"new external content"
    assert processor.can_undo
    path.unlink()
    assert processor.undo()
    assert path.read_bytes() == b"old"


def test_discard_read_failure_keeps_file(qtbot, committed_repo, monkeypatch):
    path = Path(committed_repo.repo.workdir) / "new.txt"
    path.write_bytes(b"data")
    original_open = Path.open

    def deny_read(candidate, mode="r", *args, **kwargs):
        if candidate == path and mode == "rb":
            raise PermissionError("test: unreadable file")
        return original_open(candidate, mode, *args, **kwargs)

    processor = CommandProcessor(max_undo=10)
    with monkeypatch.context() as context:
        context.setattr(Path, "open", deny_read)
        with pytest.raises(GitError, match="unreadable file"):
            processor.execute(DiscardFileCommand(committed_repo, "new.txt", max_backup_bytes=4))
    assert path.read_bytes() == b"data"
    assert not processor.can_undo
