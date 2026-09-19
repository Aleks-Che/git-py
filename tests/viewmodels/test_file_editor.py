"""Editor autosave, navigation, and command history against real Git state."""
from pathlib import Path

import pytest
from src.core.file_edit import read_text_file
from src.viewmodels.commands import CommandProcessor, SaveFileCommand
from src.viewmodels.main_viewmodel import MainViewModel


def test_save_command_undo_redo_preserves_index(committed_repo):
    root = Path(committed_repo.path)
    index_path = Path(committed_repo.repo.path) / "index"
    index_before = index_path.read_bytes()
    snapshot = read_text_file(str(root), "hello.txt", 1024)
    processor = CommandProcessor(max_undo=10)
    processor.execute(SaveFileCommand(snapshot, "saved\n"))
    assert (root / "hello.txt").read_bytes() == snapshot.encode("saved\n")
    assert processor.undo()
    assert (root / "hello.txt").read_bytes() == snapshot.data
    assert processor.redo()
    assert (root / "hello.txt").read_bytes() == snapshot.encode("saved\n")
    assert index_path.read_bytes() == index_before


@pytest.mark.parametrize("action", ["undo", "redo"])
def test_save_history_refuses_to_overwrite_later_changes(committed_repo, action):
    root = Path(committed_repo.path)
    snapshot = read_text_file(str(root), "hello.txt", 1024)
    processor = CommandProcessor(max_undo=10)
    processor.execute(SaveFileCommand(snapshot, "saved\n"))
    if action == "redo":
        assert processor.undo()
    (root / "hello.txt").write_bytes(b"external\n")
    errors = []
    processor.error_occurred.connect(errors.append)
    assert not getattr(processor, action)()
    assert (root / "hello.txt").read_bytes() == b"external\n"
    assert "changed on disk" in errors[-1]
    assert processor.can_undo if action == "undo" else processor.can_redo


def test_failed_autosave_keeps_selection_and_blocks_repository_switch(
    qtbot, committed_repo, tmp_path,
):
    root = Path(committed_repo.path)
    (root / "other.txt").write_bytes(b"other\n")
    vm = MainViewModel(config_path=tmp_path / "settings.json")
    vm.set_repository(committed_repo)
    panel = vm.commit_panel_view_model()
    panel.select_file("hello.txt")
    panel.begin_file_editing()
    panel.file_editor.set_text("draft\n")
    (root / "hello.txt").write_bytes(b"external\n")
    errors = []
    vm.error_occurred.connect(errors.append)

    assert not panel.select_file("other.txt")
    assert panel.selected_file() == "hello.txt"
    vm.set_repository(None)
    assert vm.repository_manager() is committed_repo
    assert panel.file_editor.active
    assert panel.file_editor.text == "draft\n"
    assert panel.file_editor.dirty
    assert not vm.command_processor().can_undo
    assert (root / "hello.txt").read_bytes() == b"external\n"
    assert len(errors) == 2
    panel.cancel_file_editing()


def test_background_refresh_does_not_clear_draft_when_file_becomes_clean(
    qtbot, committed_repo, tmp_path,
):
    root = Path(committed_repo.path)
    original = (root / "hello.txt").read_bytes()
    (root / "hello.txt").write_bytes(b"worktree\n")
    vm = MainViewModel(config_path=tmp_path / "settings.json")
    vm.set_repository(committed_repo)
    panel = vm.commit_panel_view_model()
    panel.select_file("hello.txt")
    panel.begin_file_editing()
    panel.file_editor.set_text("draft\n")
    (root / "hello.txt").write_bytes(original)
    panel.refresh_status()
    panel.recompute_selected_diff()
    assert panel.file_editor.active
    assert panel.file_editor.text == "draft\n"
    assert panel.selected_file() == "hello.txt"
    panel.cancel_file_editing()
