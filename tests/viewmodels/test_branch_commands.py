"""Tests for the branch-mutating :class:`GitCommand` subclasses.

Covers :class:`CheckoutCommand`, :class:`CreateBranchCommand`,
:class:`DeleteBranchCommand`, and :class:`RenameBranchCommand` —
both the standalone command and the ``CommandProcessor`` integration.
Every command is round-tripped through ``undo`` to lock in the
expected behaviour: a successful undo must restore the repository
to the state it was in *before* :meth:`execute` was called.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from src.core.exceptions import DirtyWorkTreeError, GitError, InvalidRefError
from src.core.operations import create_branch
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    CheckoutCommand,
    CommandProcessor,
    CreateBranchCommand,
    DeleteBranchCommand,
    RenameBranchCommand,
)


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _add_worktree_change(repo: RepositoryManager) -> None:
    assert repo.path is not None
    (Path(repo.path) / "hello.txt").write_text("uncommitted\n")


# ----- CheckoutCommand -----------------------------------------------


def test_checkout_command_switches_head(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    create_branch(
        committed_repo,
        "feature",
        target_sha=committed_repo.head_commit.parents[0],
    )
    cmd = CheckoutCommand(committed_repo, "feature")
    cmd.execute()
    assert committed_repo.head_commit.parents == []
    assert cmd.name == "checkout feature"


def test_checkout_command_undo_returns_to_previous(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    # Branch at the parent commit so the head target genuinely moves.
    create_branch(
        committed_repo,
        "feature",
        target_sha=committed_repo.head_commit.parents[0],
    )
    previous_sha = str(committed_repo.repo.head.target)
    previous_shorthand = committed_repo.repo.head.shorthand

    cmd = CheckoutCommand(committed_repo, "feature")
    cmd.execute()
    assert str(committed_repo.repo.head.target) != previous_sha

    cmd.undo()
    assert str(committed_repo.repo.head.target) == previous_sha
    assert committed_repo.repo.head.shorthand == previous_shorthand == "main"


def test_checkout_command_undo_on_unborn_head_is_noop(tmp_git_repo: Path) -> None:
    """A fresh repo with no commits has nothing to return to."""
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    # Manually create a branch on unborn HEAD (libgit2 will still refuse
    # to switch to it because HEAD is unborn) — so we just confirm the
    # execute() failure path doesn't blow up the VM.
    with pytest.raises(GitError):
        CheckoutCommand(mgr, "main").execute()


def test_checkout_command_dirty_worktree_raises(
    committed_repo: RepositoryManager,
) -> None:
    """A different file on the destination branch + worktree change → dirty."""
    _ensure_app()
    # Branch ``feature`` at the parent commit (no ``hello.txt`` change in tree).
    # Modifying ``hello.txt`` in the worktree while ``main`` is checked out
    # means a SAFE checkout to ``feature`` would overwrite the change.
    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.parents[0])
    _add_worktree_change(committed_repo)

    with pytest.raises(DirtyWorkTreeError):
        CheckoutCommand(committed_repo, "feature").execute()


def test_checkout_command_unknown_branch_raises(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    with pytest.raises(InvalidRefError):
        CheckoutCommand(committed_repo, "nope").execute()


def test_checkout_command_via_processor_can_be_undone(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    proc = CommandProcessor()
    proc.execute(CheckoutCommand(committed_repo, "feature"))
    assert committed_repo.repo.head.shorthand == "feature"
    assert proc.can_undo
    proc.undo()
    assert committed_repo.repo.head.shorthand == "main"


# ----- CreateBranchCommand -------------------------------------------


def test_create_branch_command_creates_branch(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    cmd = CreateBranchCommand(committed_repo, "feature", committed_repo.head_commit.sha)
    cmd.execute()
    assert any(b.name == "feature" for b in committed_repo.branches)
    assert cmd.name == "create branch feature"


def test_create_branch_command_undo_removes_branch(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    cmd = CreateBranchCommand(committed_repo, "feature", committed_repo.head_commit.sha)
    cmd.execute()
    assert any(b.name == "feature" for b in committed_repo.branches)
    cmd.undo()
    assert not any(b.name == "feature" for b in committed_repo.branches)


def test_create_branch_command_undo_noop_when_pre_existing(
    committed_repo: RepositoryManager,
) -> None:
    """If the branch already existed before execute, undo must not destroy it."""
    _ensure_app()
    create_branch(committed_repo, "preexisting", target_sha=committed_repo.head_commit.sha)
    # We pretend the command "succeeded" — in practice it would have raised
    # before, but the undo must be defensive.
    cmd = CreateBranchCommand(committed_repo, "preexisting", committed_repo.head_commit.sha)
    cmd._existed_before = True  # simulate race / pre-existing ref
    cmd.undo()
    assert any(b.name == "preexisting" for b in committed_repo.branches)


def test_create_branch_command_explicit_target(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    target_sha = committed_repo.head_commit.parents[0]
    cmd = CreateBranchCommand(committed_repo, "old", target_sha)
    cmd.execute()
    branch = next(b for b in committed_repo.branches if b.name == "old")
    assert branch.target_sha == target_sha


# ----- DeleteBranchCommand -------------------------------------------


def test_delete_branch_command_removes_branch(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    cmd = DeleteBranchCommand(committed_repo, "feature")
    cmd.execute()
    assert not any(b.name == "feature" for b in committed_repo.branches)
    assert cmd.name == "delete branch feature"


def test_delete_branch_command_undo_recreates_on_same_sha(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    target_sha = committed_repo.head_commit.sha
    create_branch(committed_repo, "feature", target_sha=target_sha)
    cmd = DeleteBranchCommand(committed_repo, "feature")
    cmd.execute()
    assert not any(b.name == "feature" for b in committed_repo.branches)
    cmd.undo()
    branch = next(b for b in committed_repo.branches if b.name == "feature")
    assert branch.target_sha == target_sha


def test_delete_branch_command_undo_noop_when_branch_was_missing(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    # Pretend the branch never existed: undo must be a quiet no-op so
    # we don't accidentally create a ref out of thin air.
    cmd = DeleteBranchCommand(committed_repo, "ghost")
    cmd._existed_before = False
    cmd.undo()
    assert not any(b.name == "ghost" for b in committed_repo.branches)


def test_delete_branch_command_refuses_current(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    with pytest.raises(GitError, match="current branch"):
        DeleteBranchCommand(committed_repo, "main").execute()


# ----- RenameBranchCommand -------------------------------------------


def test_rename_branch_command_swaps_name(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    create_branch(committed_repo, "oldname", target_sha=committed_repo.head_commit.sha)
    cmd = RenameBranchCommand(committed_repo, "oldname", "newname")
    cmd.execute()
    names = {b.name for b in committed_repo.branches}
    assert "oldname" not in names
    assert "newname" in names
    assert cmd.name == "rename branch oldname → newname"


def test_rename_branch_command_undo_swaps_back(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    create_branch(committed_repo, "oldname", target_sha=committed_repo.head_commit.sha)
    target_sha = committed_repo.head_commit.sha
    cmd = RenameBranchCommand(committed_repo, "oldname", "newname")
    cmd.execute()
    cmd.undo()
    names = {b.name for b in committed_repo.branches}
    assert "newname" not in names
    assert "oldname" in names
    branch = next(b for b in committed_repo.branches if b.name == "oldname")
    assert branch.target_sha == target_sha


def test_rename_branch_command_collides_without_force(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    create_branch(committed_repo, "a")
    create_branch(committed_repo, "b")
    with pytest.raises(GitError, match="already exists"):
        RenameBranchCommand(committed_repo, "a", "b").execute()


def test_rename_branch_command_collides_with_force(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    create_branch(committed_repo, "a")
    create_branch(committed_repo, "b")
    RenameBranchCommand(committed_repo, "a", "b", force=True).execute()
    names = [b.name for b in committed_repo.branches]
    assert "a" not in names
    assert names.count("b") == 1


# ----- CommandProcessor wiring ---------------------------------------


def test_processor_round_trip_for_create_then_delete(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    proc = CommandProcessor()
    proc.execute(
        CreateBranchCommand(committed_repo, "feature", committed_repo.head_commit.sha),
    )
    assert any(b.name == "feature" for b in committed_repo.branches)
    proc.execute(DeleteBranchCommand(committed_repo, "feature"))
    assert not any(b.name == "feature" for b in committed_repo.branches)
    proc.undo()  # undo the delete
    assert any(b.name == "feature" for b in committed_repo.branches)
    proc.redo()  # redo the delete
    assert not any(b.name == "feature" for b in committed_repo.branches)
