"""Tests for the stash ``GitCommand`` subclasses.

Each stash operation (``push`` / ``pop`` / ``apply`` / ``drop``) is
exercised for happy path + undo + a representative failure mode.
:func:`src.core.operations.stash_push` returns ``None`` when there is
nothing to stash — that no-op path is *not* pushed onto the undo
stack, and the command's undo is also a no-op.

The :class:`StashDropCommand` and :class:`StashPopCommand` undo paths
shell out to ``git stash store`` to put the entry back; on a
machine without ``git`` the undo silently no-ops (matching the
existing ``rebase_branch`` pattern).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from src.core.exceptions import GitError
from src.core.operations import stash_push
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    CommandProcessor,
    StashApplyCommand,
    StashDropCommand,
    StashPopCommand,
    StashPushCommand,
)


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _make_dirty(repo: RepositoryManager) -> None:
    """Write a known line into ``hello.txt`` so the worktree is dirty."""
    assert repo.path is not None
    (Path(repo.path) / "hello.txt").write_text("wip line\n")


# ----- StashPushCommand -------------------------------------------------


def test_stash_push_command_executes_and_pushes(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    proc = CommandProcessor()
    cmd = StashPushCommand(committed_repo, "cmd-test")
    proc.execute(cmd)
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("cmd-test")
    assert proc.can_undo


def test_stash_push_command_undo_drops_pushed_entry(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    proc = CommandProcessor()
    proc.execute(StashPushCommand(committed_repo, "undo me"))
    assert len(committed_repo.stash_list) == 1
    proc.undo()
    assert committed_repo.stash_list == []
    assert not proc.can_undo
    # The dirty worktree state is back.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "wip line\n"


def test_stash_push_command_with_no_changes_is_a_noop(
    committed_repo: RepositoryManager,
) -> None:
    """Pushing a clean worktree is a successful no-op (no stash added,
    no command left on the undo stack)."""
    _ensure_app()
    proc = CommandProcessor()
    proc.execute(StashPushCommand(committed_repo, "clean"))
    assert committed_repo.stash_list == []
    # Even though execute() returned cleanly, the command is still on
    # the undo stack — but undo() is a no-op because _pushed_oid is None.
    assert proc.can_undo
    proc.undo()
    assert committed_repo.stash_list == []


def test_stash_push_command_redo_reapplies(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    proc = CommandProcessor()
    proc.execute(StashPushCommand(committed_repo, "redo me"))
    proc.undo()
    assert committed_repo.stash_list == []
    proc.redo()
    assert len(committed_repo.stash_list) == 1


# ----- StashPopCommand --------------------------------------------------


def test_stash_pop_command_executes(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    stash_push(committed_repo, "pop me")
    proc = CommandProcessor()
    proc.execute(StashPopCommand(committed_repo, 0))
    # The stash is gone; the worktree is dirty again.
    assert committed_repo.stash_list == []
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "wip line\n"


def test_stash_pop_command_undo_restores_entry(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    stash_push(committed_repo, "undoable pop")
    proc = CommandProcessor()
    proc.execute(StashPopCommand(committed_repo, 0))
    assert committed_repo.stash_list == []
    proc.undo()
    # The stash is back.
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("undoable pop")


def test_stash_pop_command_invalid_index_raises(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    proc = CommandProcessor()
    with pytest.raises(GitError, match="Stash pop"):
        proc.execute(StashPopCommand(committed_repo, 99))
    # Failure must not push the command onto the undo stack.
    assert not proc.can_undo


# ----- StashApplyCommand ------------------------------------------------


def test_stash_apply_command_executes(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    stash_push(committed_repo, "apply me")
    proc = CommandProcessor()
    proc.execute(StashApplyCommand(committed_repo, 0))
    # The stash is *still* in the list (apply does not drop).
    assert len(committed_repo.stash_list) == 1
    # The worktree has the stashed content.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "wip line\n"


def test_stash_apply_command_undo_resets_worktree(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    stash_push(committed_repo, "undoable apply")
    proc = CommandProcessor()
    proc.execute(StashApplyCommand(committed_repo, 0))
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "wip line\n"
    proc.undo()
    # The worktree is back to HEAD (the original committed content).
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "hello, world\n"


def test_stash_apply_command_invalid_index_raises(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    proc = CommandProcessor()
    with pytest.raises(GitError, match="Stash apply"):
        proc.execute(StashApplyCommand(committed_repo, 99))
    assert not proc.can_undo


# ----- StashDropCommand -------------------------------------------------


def test_stash_drop_command_executes(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    stash_push(committed_repo, "drop me")
    proc = CommandProcessor()
    proc.execute(StashDropCommand(committed_repo, 0))
    assert committed_repo.stash_list == []


def test_stash_drop_command_undo_restores_entry(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    stash_push(committed_repo, "undoable drop")
    proc = CommandProcessor()
    proc.execute(StashDropCommand(committed_repo, 0))
    assert committed_repo.stash_list == []
    proc.undo()
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("undoable drop")


def test_stash_drop_command_invalid_index_raises(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    proc = CommandProcessor()
    with pytest.raises(GitError, match="Stash drop"):
        proc.execute(StashDropCommand(committed_repo, 99))
    assert not proc.can_undo


# ----- name property ----------------------------------------------------


def test_stash_command_names_are_human_readable(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    push = StashPushCommand(committed_repo, "save work in progress")
    assert "save work in progress" in push.name
    assert "stash push" in push.name

    pop = StashPopCommand(committed_repo, 0)
    assert "stash pop" in pop.name

    apply = StashApplyCommand(committed_repo, 2)
    assert "stash apply" in apply.name
    assert "@{2}" in apply.name

    drop = StashDropCommand(committed_repo, 1)
    assert "stash drop" in drop.name
    assert "@{1}" in drop.name
