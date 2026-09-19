"""Checkout navigation respects busy/editor guards and reports lookup failures."""
from pathlib import Path

import pytest
from src.core.exceptions import GitError
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.mark.parametrize("blocked_by", ["busy", "editor", "lookup_error"])
def test_checkout_navigation_refuses_unsafe_transition(
    qtbot, committed_repo, linked_worktree, tmp_path, monkeypatch, blocked_by,
):
    vm = MainViewModel(config_path=tmp_path / "settings.json")
    vm.set_repository(committed_repo)
    vm.stop_worktree_refresh()
    requested, errors = [], []
    vm.open_worktree_requested.connect(requested.append)
    vm.error_occurred.connect(errors.append)
    original_head = (Path(committed_repo.repo.path) / "HEAD").read_bytes()
    if blocked_by == "busy":
        vm._is_busy = True
    elif blocked_by == "editor":
        monkeypatch.setattr(vm.commit_panel_view_model().file_editor, "finish_editing",
                            lambda: False)
    else:
        def fail(*args):
            raise GitError("Cannot read worktree HEAD")
        monkeypatch.setattr("src.core.worktree_status.find_branch_worktree", fail)
    vm.request_checkout_branch("agents-ide/run/test-worktree")
    assert requested == []
    assert (Path(committed_repo.repo.path) / "HEAD").read_bytes() == original_head
    assert vm.command_processor().undo_stack_snapshot() == []
    if blocked_by == "busy":
        assert len(errors) == 1 and "in progress" in errors[0]
    elif blocked_by == "lookup_error":
        assert errors == ["Cannot read worktree HEAD"]
    else:
        assert errors == []
    vm._is_busy = False
