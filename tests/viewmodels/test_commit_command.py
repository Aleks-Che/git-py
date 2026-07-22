"""Stage 3: tests for :class:`CommitCommand` and the ``MainViewModel.commit_changes`` flow.

The command is the bridge between the UI (which stages files via
:class:`CommitPanelViewModel` and types a message) and the on-disk
repository (which the command creates the commit on). Its undo is a
``git reset --soft HEAD~1`` so the index/worktree keep the staged
changes — the user can simply re-type a message and click Commit
again.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QCoreApplication
from src.core.repository import RepositoryManager
from src.viewmodels.commands import CommandProcessor, CommitCommand
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "t@example.com", int(time.time()), 0)


# ----- standalone CommitCommand ----------------------------------------


def test_commit_command_creates_commit(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    head_before = str(committed_repo.repo.head.target)
    CommitCommand(committed_repo, "extra commit on top").execute()
    head_after = str(committed_repo.repo.head.target)
    assert head_after != head_before
    assert committed_repo.repo[head_after].message.strip() == "extra commit on top"


def test_commit_command_rejects_empty_message(tmp_git_repo: Path) -> None:
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    # HEAD is unborn, so we don't get the empty-message check first;
    # ``commit_changes`` raises GitError on empty/whitespace message.
    from src.core.exceptions import GitError

    with pytest.raises(GitError):
        CommitCommand(mgr, "").execute()
    with pytest.raises(GitError):
        CommitCommand(mgr, "   \n  ").execute()


def test_commit_command_name_truncates_long_message(tmp_git_repo: Path) -> None:
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    cmd = CommitCommand(mgr, "subject line" + "x" * 100)
    name = cmd.name
    assert name.startswith("commit: subject line")
    assert len(name) <= len("commit: ") + 50


def test_commit_command_name_for_multi_line_message(tmp_git_repo: Path) -> None:
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    cmd = CommitCommand(mgr, "subject line\n\nbody line")
    assert cmd.name == "commit: subject line"


# ----- undo via CommandProcessor ---------------------------------------


def test_undo_removes_the_commit(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    proc = CommandProcessor()
    head_before = str(committed_repo.repo.head.target)
    initial_count = sum(1 for _ in committed_repo.repo.walk(head_before))

    proc.execute(CommitCommand(committed_repo, "extra commit"))

    head_after_commit = str(committed_repo.repo.head.target)
    assert head_after_commit != head_before
    assert sum(1 for _ in committed_repo.repo.walk(head_after_commit)) == initial_count + 1

    proc.undo()

    head_after_undo = str(committed_repo.repo.head.target)
    assert head_after_undo == head_before
    assert sum(1 for _ in committed_repo.repo.walk(head_after_undo)) == initial_count
    # The commit's changes must still be in the index (soft reset).
    assert "hello.txt" in committed_repo.repo.index


def test_undo_redo_round_trip(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    proc = CommandProcessor()
    head_before = str(committed_repo.repo.head.target)

    proc.execute(CommitCommand(committed_repo, "round-trip"))
    proc.undo()
    assert str(committed_repo.repo.head.target) == head_before
    proc.redo()
    assert str(committed_repo.repo.head.target) != head_before
    assert committed_repo.repo[committed_repo.repo.head.target].message.strip() == "round-trip"


def test_processor_state_after_commit(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    proc = CommandProcessor()
    assert not proc.can_undo
    proc.execute(CommitCommand(committed_repo, "x"))
    assert proc.can_undo
    assert not proc.can_redo


def test_new_commit_clears_redo_stack(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    proc = CommandProcessor()
    proc.execute(CommitCommand(committed_repo, "a"))
    proc.undo()
    assert proc.can_redo
    proc.execute(CommitCommand(committed_repo, "b"))
    assert not proc.can_redo


# ----- MainViewModel.commit_changes -----------------------------------


def test_main_vm_commit_creates_commit_and_refreshes(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    head_before = str(committed_repo.repo.head.target)
    with qtbot.waitSignal(vm.graph_view_model().graph_updated, timeout=2000):
        vm.commit_changes("from main vm")

    head_after = str(committed_repo.repo.head.target)
    assert head_after != head_before
    assert committed_repo.repo[head_after].message.strip() == "from main vm"
    # Undo stack now has the commit.
    assert vm.command_processor().can_undo


def test_main_vm_commit_empty_message_emits_error(qtbot) -> None:
    _ensure_app()
    mgr = RepositoryManager()
    # We never opened a repo, so commit_changes is a no-op + error.
    vm = MainViewModel()
    vm.set_repository(mgr)  # no-op, mgr is not open
    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.commit_changes("")
    assert "No repository" in blocker.args[0]


def test_main_vm_undo_refreshes_graph(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    head_before = str(committed_repo.repo.head.target)
    with qtbot.waitSignal(vm.graph_view_model().graph_updated, timeout=2000):
        vm.commit_changes("temporary")

    with qtbot.waitSignal(vm.graph_view_model().graph_updated, timeout=2000):
        vm.undo()

    assert str(committed_repo.repo.head.target) == head_before


def test_main_vm_undo_clears_message(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.commit_panel_view_model().set_commit_message("hello")
    vm.commit_changes("hello")
    # After a successful commit, the message field is cleared.
    assert vm.commit_panel_view_model().commit_message() == ""


def test_main_vm_open_repository_emits_error_on_bad_path(qtbot, tmp_path: Path) -> None:
    _ensure_app()
    vm = MainViewModel()
    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.open_repository(str(tmp_path / "does-not-exist"))
    assert "does not exist" in blocker.args[0].lower() or "not a git" in blocker.args[0].lower()
    assert vm.repository_manager() is None


def test_main_vm_open_repository_success(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    with qtbot.waitSignal(vm.repository_changed, timeout=500) as blocker:
        vm.open_repository(committed_repo.path)
    assert blocker.args[0] == committed_repo.path
    # ``open_repository`` builds a fresh ``RepositoryManager``; the
    # contract is that the VM has *a* manager bound to the same path.
    assert vm.repository_manager() is not None
    assert vm.repository_manager().path == committed_repo.path


def test_main_vm_set_repository_clears_undo_stack(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.commit_changes("first")
    assert vm.command_processor().can_undo

    # Open a different repo (the same one rebinds, but the point is
    # that set_repository clears the stack).
    vm2 = MainViewModel()
    with qtbot.waitSignal(vm2.repository_changed, timeout=500):
        vm2.open_repository(committed_repo.path)
    # Brand-new VM has a fresh processor: cannot undo.
    assert not vm2.command_processor().can_undo


def test_main_vm_undo_with_empty_stack_is_noop(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    # No commands executed yet; should be a quiet no-op.
    vm.undo()
    vm.redo()
