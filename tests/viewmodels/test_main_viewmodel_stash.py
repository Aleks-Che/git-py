"""Tests for the stash verb methods on :class:`MainViewModel`.

The contract mirrors the rest of the MainViewModel surface:

* the call goes through :class:`CommandProcessor` (so Undo / Redo work);
* on success every downstream view (graph, commit panel, branch panel)
  is refreshed — the left panel's stash group must reflect the change;
* on failure the error is surfaced through ``error_occurred`` and the
  command is *not* pushed onto the undo stack.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _make_dirty(repo: RepositoryManager, text: str = "wip\n") -> None:
    assert repo.path is not None
    (Path(repo.path) / "hello.txt").write_text(text)


# ----- stash_push -------------------------------------------------------


def test_stash_push_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    ok = vm.stash_push("via-vm")
    assert ok is True
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("via-vm")
    assert vm.command_processor().can_undo


def test_stash_push_refreshes_views(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    bp = vm.branch_panel_view_model()
    events: list[None] = []
    bp.references_changed.connect(lambda: events.append(None))
    vm.stash_push("refresh test")
    assert events  # at least one refresh emission


def test_stash_push_clean_worktree_is_noop(committed_repo: RepositoryManager) -> None:
    """A push on a clean worktree still returns True (the command is a
    successful no-op) but does not create a new stash entry."""
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_push() is True
    assert committed_repo.stash_list == []


def test_stash_push_without_repo_emits_error() -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_push() is False
    assert errors
    assert "No repository" in errors[0]


# ----- stash_pop --------------------------------------------------------


def test_stash_pop_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "pop me")
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "pop me")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_pop(0) is True
    assert committed_repo.stash_list == []
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "pop me"


def test_stash_pop_empty_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_pop(0) is False
    assert errors
    assert not vm.command_processor().can_undo


def test_stash_pop_invalid_index_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "only one")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_pop(99) is False
    assert errors
    assert not vm.command_processor().can_undo


# ----- stash_apply ------------------------------------------------------


def test_stash_apply_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "apply me")
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "apply me")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_apply(0) is True
    # Apply keeps the entry.
    assert len(committed_repo.stash_list) == 1
    # Worktree has the content.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "apply me"


def test_stash_apply_empty_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_apply(0) is False
    assert errors


# ----- stash_drop -------------------------------------------------------


def test_stash_drop_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "drop me")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_drop(0) is True
    assert committed_repo.stash_list == []
    assert vm.command_processor().can_undo


def test_stash_drop_invalid_index_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_drop(0) is False
    assert errors


# ----- undo / redo ------------------------------------------------------


def test_stash_push_undo_restores_worktree(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "wip-undo")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.stash_push("undoable")
    assert len(committed_repo.stash_list) == 1
    vm.undo()
    # The worktree is restored.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "wip-undo"


def test_stash_pop_undo_restores_stash_entry(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "pop-undo")
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "pop-undo")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.stash_pop(0)
    assert committed_repo.stash_list == []
    vm.undo()
    # The stash entry is back.
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("pop-undo")
