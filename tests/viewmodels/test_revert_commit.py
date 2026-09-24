"""Revert lifecycle, background dispatch, and guarded Undo/Redo."""
from pathlib import Path
from threading import Event

import pytest
from src.core.operations import checkout_branch, commit_changes, create_branch, revert_head_oid
from src.utils.config import save_config
from src.viewmodels.commands import RevertCommitCommand
from src.viewmodels.main_viewmodel import MainViewModel


def _vm(mgr, tmp_path, *, async_enabled=False):
    config = tmp_path / "settings.json"
    save_config(config, {
        "use_default_git_credentials": False,
        "author_name": "Reverter", "author_email": "reverter@example.com",
    })
    vm = MainViewModel(config_path=config, async_enabled=async_enabled)
    vm.set_repository(mgr)
    vm._worktree_refresh_timer.stop()
    return vm


@pytest.mark.parametrize("async_enabled", [False, True])
def test_revert_commit_and_undo_redo(qtbot, committed_repo, tmp_path, async_enabled):
    mgr = committed_repo
    target = mgr.head_commit.sha
    vm = _vm(mgr, tmp_path, async_enabled=async_enabled)
    errors = []
    vm.error_occurred.connect(errors.append)
    vm.revert_commit(target)
    qtbot.waitUntil(lambda: not vm.is_busy())
    result = mgr.head_commit
    assert result.parents == [target]
    assert result.author_name == result.committer_name == "Reverter"
    assert isinstance(vm.command_processor().peek_undo_command(), RevertCommitCommand)
    assert vm.graph_view_model().repository().head_commit.sha == result.sha
    vm.undo()
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert mgr.head_commit.sha == target
    assert (Path(mgr.path) / "hello.txt").read_text() == "hello, world\n"
    vm.redo()
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert mgr.head_commit.sha == result.sha
    assert (Path(mgr.path) / "hello.txt").read_text() == "hello\n"
    assert not mgr.repo.status()
    assert not errors


@pytest.mark.parametrize("external", ["edit", "commit", "branch"])
def test_revert_undo_refuses_external_changes(qtbot, committed_repo, tmp_path, external):
    mgr = committed_repo
    vm = _vm(mgr, tmp_path)
    vm.revert_commit(mgr.head_commit.sha)
    errors = []
    vm.error_occurred.connect(errors.append)
    path = Path(mgr.path) / "hello.txt"
    if external == "branch":
        create_branch(mgr, "other")
        checkout_branch(mgr, "other")
    else:
        path.write_text("external\n")
        if external == "commit":
            commit_changes(mgr, "external")
    before = mgr.head_commit.sha
    content = path.read_bytes()
    vm.undo()
    assert errors
    assert mgr.head_commit.sha == before
    assert path.read_bytes() == content
    assert vm.command_processor().can_undo


@pytest.mark.parametrize("async_enabled", [False, True])
@pytest.mark.parametrize("finish", ["resolve", "continue", "abort", "reopen"])
def test_revert_conflict_lifecycle(qtbot, committed_repo, tmp_path, async_enabled, finish):
    mgr = committed_repo
    target = mgr.head_commit.sha
    path = Path(mgr.path) / "hello.txt"
    path.write_text("later\n")
    tip = commit_changes(mgr, "later").sha
    vm = _vm(mgr, tmp_path, async_enabled=async_enabled)
    errors = []
    vm.error_occurred.connect(errors.append)
    vm.revert_commit(target)
    qtbot.waitUntil(lambda: not vm.is_busy())
    state = vm.conflict_state()
    assert state["operation"] == "revert"
    assert state["sha"] == target
    assert state["conflicting_paths"] == ["hello.txt"]
    assert not vm.command_processor().can_undo
    if finish == "reopen":
        vm = _vm(mgr, tmp_path, async_enabled=async_enabled)
        vm.error_occurred.connect(errors.append)
        assert vm.conflict_state()["sha"] == target
    if finish == "abort":
        vm.abort_revert()
        qtbot.waitUntil(lambda: not vm.is_busy())
        assert mgr.head_commit.sha == tip
        assert path.read_text() == "later\n"
        assert not vm.command_processor().can_undo
    else:
        if finish == "continue":
            path.write_text("resolved\n")
            mgr.repo.index.add("hello.txt")
            mgr.repo.index.write()
            vm.continue_operation()
        else:
            vm.resolve_conflict("hello.txt", "resolved\n")
        qtbot.waitUntil(lambda: not vm.is_busy())
        result = mgr.head_commit
        assert result.parents == [tip]
        assert target in result.message
        assert path.read_text() == "resolved\n"
        vm.undo()
        qtbot.waitUntil(lambda: not vm.is_busy())
        assert mgr.head_commit.sha == tip
        vm.redo()
        qtbot.waitUntil(lambda: not vm.is_busy())
        assert mgr.head_commit.sha == result.sha
        assert path.read_text() == "resolved\n"
    assert vm.conflict_state() is None
    assert not revert_head_oid(mgr)
    assert not mgr.repo.status()
    assert not errors


def test_revert_background_guard_and_worker_handle(qtbot, committed_repo, tmp_path, monkeypatch):
    mgr = committed_repo
    vm = _vm(mgr, tmp_path, async_enabled=True)
    started, release = Event(), Event()
    original = RevertCommitCommand.execute
    worker_managers = []

    def delayed(command):
        worker_managers.append(command._repo)
        started.set()
        assert release.wait(5)
        original(command)

    monkeypatch.setattr(RevertCommitCommand, "execute", delayed)
    errors = []
    busy = []
    vm.error_occurred.connect(errors.append)
    vm.busy_changed.connect(busy.append)
    target = mgr.head_commit.sha
    try:
        vm.revert_commit(target)
        qtbot.waitUntil(started.is_set)
        assert vm.is_busy()
        vm.revert_commit(target)
        assert len(errors) == 1 and "in progress" in errors[0]
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm.is_busy())
    assert len(worker_managers) == 1
    assert worker_managers[0] is not mgr
    assert mgr.head_commit.parents == [target]
    assert busy[0] and not busy[-1]


def test_revert_reports_missing_repo_and_dirty_tree(qtbot, committed_repo, tmp_path):
    vm = _vm(committed_repo, tmp_path)
    errors = []
    vm.error_occurred.connect(errors.append)
    target = committed_repo.head_commit.sha
    (Path(committed_repo.path) / "hello.txt").write_text("local\n")
    vm.revert_commit(target)
    assert len(errors) == 1
    assert not vm.command_processor().can_undo
    assert committed_repo.head_commit.sha == target
    vm.set_repository(None)
    vm.revert_commit(target)
    assert errors[-1] == "No repository open."
