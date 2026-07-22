"""Tests for the stash ``GitCommand`` subclasses.

Each stash operation (``push`` / ``pop`` / ``apply`` / ``drop``) is
exercised for happy path + undo + a representative failure mode.
:func:`src.core.operations.stash_push` returns ``None`` when there is
nothing to stash — that no-op path is *not* pushed onto the undo
stack, and the command's undo is also a no-op.

The :class:`StashDropCommand` and :class:`StashPopCommand` undo paths
shell out to ``git stash store`` to put the entry back.
"""
from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from PySide6.QtWidgets import QApplication
from src.core.exceptions import GitError
from src.core.operations import stash_oid_at, stash_push
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    CommandProcessor,
    StashApplyCommand,
    StashDropCommand,
    StashPopCommand,
    StashPushCommand,
)


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _make_dirty(repo: RepositoryManager) -> None:
    """Write a known line into ``hello.txt`` so the worktree is dirty."""
    assert repo.path is not None
    (Path(repo.path) / "hello.txt").write_text("wip line\n")


def _commit_file(repo: RepositoryManager, path: str, content: str) -> None:
    """Add one tracked file to HEAD so tests can keep unrelated dirty state."""
    assert repo.path is not None
    (Path(repo.path) / path).write_text(content)
    repo.repo.index.add(path)
    repo.repo.index.write()
    tree = repo.repo.index.write_tree()
    signature = pygit2.Signature("tester", "tester@example.com", 0, 0)
    repo.repo.create_commit(
        "HEAD",
        signature,
        signature,
        f"add {path}",
        tree,
        [repo.repo.head.target],
    )


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
    # With no intervening stash, Undo also reapplies the command's changes.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "wip line\n"


def test_stash_push_undo_drops_correct_stash(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    command = StashPushCommand(committed_repo, "ours")
    command.execute()
    ours_oid = stash_oid_at(committed_repo, 0)

    (Path(committed_repo.path) / "hello.txt").write_text("someone else's work\n")
    foreign_oid = stash_push(committed_repo, "foreign")
    command.undo()

    assert stash_oid_at(committed_repo, 0) == foreign_oid
    assert ours_oid is not None
    remaining_oids = [str(entry.commit_id) for entry in committed_repo.repo.listall_stashes()]
    assert ours_oid not in remaining_oids


def test_stash_push_undo_recovers_from_intervening_push(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    proc = CommandProcessor()
    proc.execute(StashPushCommand(committed_repo, "original command"))
    original_oid = stash_oid_at(committed_repo, 0)

    (Path(committed_repo.path) / "hello.txt").write_text("intervening work\n")
    intervening_oid = stash_push(committed_repo, "intervening push")
    proc.undo()

    assert original_oid is not None
    assert stash_oid_at(committed_repo, 0) == intervening_oid
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("intervening push")


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
    # Both the pre-pop worktree and the stash entry are back.
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("undoable pop")
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "hello, world\n"


def test_stash_pop_undo_restores_worktree_and_stash(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _commit_file(committed_repo, "other.txt", "other base\n")
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("stashed tracked change\n")
    (root / "stash-only.txt").write_text("stashed untracked file\n")
    popped_oid = stash_push(committed_repo, "dirty pop", include_untracked=True)
    (root / "other.txt").write_text("pre-pop dirty change\n")
    (root / "pre-existing.txt").write_text("pre-pop untracked\n")

    command = StashPopCommand(committed_repo, 0)
    command.execute()
    assert committed_repo.stash_list == []
    assert (root / "hello.txt").read_text() == "stashed tracked change\n"
    assert (root / "stash-only.txt").read_text() == "stashed untracked file\n"

    command.undo()
    assert (root / "hello.txt").read_text() == "hello, world\n"
    assert not (root / "stash-only.txt").exists()
    assert (root / "other.txt").read_text() == "pre-pop dirty change\n"
    assert (root / "pre-existing.txt").read_text() == "pre-pop untracked\n"
    assert stash_oid_at(committed_repo, 0) == popped_oid
    assert len(committed_repo.stash_list) == 1


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


def test_stash_apply_undo_restores_worktree(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _commit_file(committed_repo, "other.txt", "other base\n")
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("stashed tracked change\n")
    (root / "stash-only.txt").write_text("stashed untracked file\n")
    stash_push(committed_repo, "dirty apply", include_untracked=True)
    (root / "other.txt").write_text("pre-apply dirty change\n")
    (root / "pre-existing.txt").write_text("pre-apply untracked\n")

    command = StashApplyCommand(committed_repo, 0)
    command.execute()
    assert (root / "hello.txt").read_text() == "stashed tracked change\n"
    assert (root / "stash-only.txt").read_text() == "stashed untracked file\n"

    command.undo()
    assert (root / "hello.txt").read_text() == "hello, world\n"
    assert not (root / "stash-only.txt").exists()
    assert (root / "other.txt").read_text() == "pre-apply dirty change\n"
    assert (root / "pre-existing.txt").read_text() == "pre-apply untracked\n"
    assert len(committed_repo.stash_list) == 1


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
