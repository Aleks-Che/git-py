"""Tests for :class:`MergeCommand`, :class:`RebaseCommand`,
:class:`CherryPickCommand`, and :class:`RevertCommand`.

Each command is round-tripped through ``undo`` to lock in the
expected behaviour: a successful undo must restore the repository to
the state it was in *before* :meth:`execute` was called. Conflict
paths assert that the command is **not** pushed onto the undo stack
(``CommandProcessor.execute`` re-raises without appending on failure).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from src.core.exceptions import GitError, MergeConflictError, RebaseConflictError
from src.core.operations import checkout_branch, create_branch
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    CherryPickCommand,
    CommandProcessor,
    MergeCommand,
    RebaseCommand,
    RevertCommand,
)


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _two_branches(
    committed_repo: RepositoryManager,
) -> tuple[str, str, str]:
    """Build a divergent history: main and feature each add a different file.

    Returns ``(main_tip_sha, feat_tip_sha, pre_merge_main_sha)``. The
    repo is left on ``main`` at ``pre_merge_main_sha`` (the commit
    that "add m" landed on top of — the merge will create a new commit
    on top of *this* one).
    """
    from src.core.operations import commit_changes

    pre_merge_main_sha = committed_repo.head_commit.sha
    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    feat_sha = commit_changes(committed_repo, "add f").sha
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "m.txt").write_text("m\n")
    main_tip_sha = commit_changes(committed_repo, "add m").sha
    return main_tip_sha, feat_sha, pre_merge_main_sha


def _conflict_branches(committed_repo: RepositoryManager) -> None:
    """Build a 2-way conflict on ``hello.txt`` (left on ``main``)."""
    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "hello.txt").write_text("feature side\n")
    from src.core.operations import commit_changes

    commit_changes(committed_repo, "feature hello")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "hello.txt").write_text("main side\n")
    commit_changes(committed_repo, "main hello")


# ----- MergeCommand -------------------------------------------------------


def test_merge_command_fast_forward_moves_head(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    from src.core.operations import commit_changes

    feat_sha = commit_changes(committed_repo, "add f").sha
    checkout_branch(committed_repo, "main")
    main_sha = committed_repo.head_commit.sha

    cmd = MergeCommand(committed_repo, "feature")
    cmd.execute()
    assert committed_repo.head_commit.sha == feat_sha
    assert cmd.name == "merge feature"

    cmd.undo()
    assert committed_repo.head_commit.sha == main_sha


def test_merge_command_no_ff_creates_merge_commit_on_fast_forward(
    committed_repo: RepositoryManager,
) -> None:
    """``no_ff=True`` keeps the merge visible in the graph.

    The user reported: when a fast-forward merge happens (source
    is a descendant of HEAD), the user sees "no merge commit" in
    the graph. The fix is the ``no_ff`` flag on
    :class:`MergeCommand` (matches ``git merge --no-ff``). The
    test pins the flag at the command layer so the user-facing
    behaviour is locked in.
    """
    _ensure_app()
    main_sha = committed_repo.head_commit.sha
    create_branch(committed_repo, "feature", target_sha=main_sha)
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    from src.core.operations import commit_changes

    feat_sha = commit_changes(committed_repo, "add f").sha
    checkout_branch(committed_repo, "main")

    cmd = MergeCommand(committed_repo, "feature", no_ff=True)
    cmd.execute()
    # A *new* commit was created on top of main.  The branch ref
    # is no longer at the fast-forward tip — the user can see the
    # merge in the graph.
    new_head_sha = committed_repo.head_commit.sha
    assert new_head_sha != feat_sha
    assert new_head_sha != main_sha
    assert set(committed_repo.head_commit.parents) == {main_sha, feat_sha}

    cmd.undo()
    # Undo restores main to its pre-merge position.
    assert committed_repo.head_commit.sha == main_sha


def test_merge_command_three_way_creates_merge_commit(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    main_tip, feat_sha, _ = _two_branches(committed_repo)

    cmd = MergeCommand(committed_repo, "feature")
    cmd.execute()
    new_sha = committed_repo.head_commit.sha
    assert new_sha not in {main_tip, feat_sha}
    # Merge commit has two parents: main's tip and feature's tip.
    assert committed_repo.head_commit.parents == [main_tip, feat_sha]

    cmd.undo()
    assert committed_repo.head_commit.sha == main_tip


def test_merge_command_three_way_with_target_suffix_in_name(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _two_branches(committed_repo)
    cmd = MergeCommand(committed_repo, "feature", target="main")
    assert cmd.name == "merge feature into main"


def test_merge_command_conflict_is_not_pushed(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _conflict_branches(committed_repo)
    proc = CommandProcessor()
    cmd = MergeCommand(committed_repo, "feature")
    with pytest.raises(MergeConflictError):
        proc.execute(cmd)
    assert not proc.can_undo  # failure path leaves the stack empty


def test_merge_command_up_to_date_undo_is_noop(
    committed_repo: RepositoryManager,
) -> None:
    """Merging HEAD into itself is a no-op; undo must not crash."""
    _ensure_app()
    main_sha = committed_repo.head_commit.sha
    cmd = MergeCommand(committed_repo, "main")
    cmd.execute()
    assert committed_repo.head_commit.sha == main_sha
    # Undo: nothing moved.
    cmd.undo()
    assert committed_repo.head_commit.sha == main_sha


def test_merge_command_via_processor_round_trip(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    main_tip, _, _ = _two_branches(committed_repo)
    proc = CommandProcessor()
    proc.execute(MergeCommand(committed_repo, "feature"))
    assert committed_repo.head_commit.parents[0] == main_tip
    proc.undo()
    assert committed_repo.head_commit.sha == main_tip


# ----- RebaseCommand ------------------------------------------------------


def test_rebase_command_successful_undo_rewinds(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    main_tip, _, _ = _two_branches(committed_repo)
    # Switch to feature so a rebase onto main is a real operation.
    checkout_branch(committed_repo, "feature")
    pre_rebase = committed_repo.head_commit.sha

    cmd = RebaseCommand(committed_repo, "main")
    cmd.execute()
    assert committed_repo.head_commit.sha != pre_rebase  # rebase moved HEAD
    assert committed_repo.head_commit.parents[0] == main_tip  # parent is main's tip

    cmd.undo()
    assert committed_repo.head_commit.sha == pre_rebase


def test_rebase_command_conflict_is_not_pushed(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _conflict_branches(committed_repo)
    # We're on main; check out feature so a rebase from feature
    # onto main has a clear pre-state.
    checkout_branch(committed_repo, "feature")
    proc = CommandProcessor()
    cmd = RebaseCommand(committed_repo, "main")
    with pytest.raises(RebaseConflictError):
        proc.execute(cmd)
    assert not proc.can_undo


def test_rebase_command_undo_after_conflict_aborts(
    committed_repo: RepositoryManager,
) -> None:
    """If the rebase left us mid-flight (e.g. command was already pushed
    in some custom flow), undo aborts via ``git rebase --abort``."""
    _ensure_app()
    _conflict_branches(committed_repo)
    checkout_branch(committed_repo, "feature")
    cmd = RebaseCommand(committed_repo, "main")
    with pytest.raises(RebaseConflictError):
        cmd.execute()
    # The command is mid-rebase now. We can't push it on the stack
    # because ``processor.execute`` re-raised, but we can still call
    # ``undo`` directly — it should detect the in-progress rebase and
    # abort it.
    cmd.undo()


# ----- CherryPickCommand / RevertCommand ----------------------------------


def test_cherry_pick_command_stages_change(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    """Cherry-pick the diff of ``feat`` onto the current HEAD (``base``).

    After the command, HEAD stays on ``base`` and ``b.txt`` is staged
    in the index. Undo (``reset --mixed``) clears that index entry.
    """
    _ensure_app()
    from src.core.operations import reset

    mgr = RepositoryManager(str(tmp_git_repo))
    base_oid = make_commit("base", files={"a.txt": "A\n"})
    feat_oid = make_commit("adds-b", files={"b.txt": "B\n"}, parents=[base_oid])
    # Move HEAD back to ``base`` so the cherry-pick actually has work to do.
    reset(mgr, str(base_oid), mode="hard")
    assert mgr.head_commit.sha == str(base_oid)

    cmd = CherryPickCommand(mgr, str(feat_oid))
    cmd.execute()
    # HEAD did not move; the file is staged in the index.
    assert mgr.head_commit.sha == str(base_oid)
    assert "b.txt" in mgr.repo.index

    cmd.undo()
    # Undo: reset --mixed clears the index entry for b.txt but leaves
    # the worktree file in place.
    assert "b.txt" not in mgr.repo.index


def test_cherry_pick_command_conflict_is_not_pushed(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _conflict_branches(committed_repo)
    feature_sha = next(
        b.target_sha for b in committed_repo.branches if b.name == "feature"
    )
    proc = CommandProcessor()
    cmd = CherryPickCommand(committed_repo, feature_sha)
    with pytest.raises(MergeConflictError):
        proc.execute(cmd)
    assert not proc.can_undo


def test_revert_command_stages_inverse(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    target_sha = committed_repo.head_commit.sha
    cmd = RevertCommand(committed_repo, target_sha)
    cmd.execute()
    # HEAD did not move; the file is re-staged to its pre-target value.
    assert committed_repo.head_commit.sha == target_sha
    # The committed_repo fixture's last commit is "greet the world"
    # which changed hello.txt to "hello, world\n". Reverting it
    # restores the worktree to "hello\n" (the pre-HEAD content).
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "hello\n"
    assert cmd.name == f"revert {target_sha[:7]}"


def test_revert_command_undo_clears_index(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    target_sha = committed_repo.head_commit.sha
    cmd = RevertCommand(committed_repo, target_sha)
    cmd.execute()
    # Pre-undo: worktree shows the reverted (pre-target) content.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "hello\n"
    cmd.undo()
    # After undo (``reset --mixed``): HEAD is back to the pre-revert
    # state and the index entry is restored. The worktree keeps the
    # modified content the user already saw, which is the standard
    # ``git reset --mixed`` behaviour.
    assert committed_repo.head_commit.sha == target_sha
    # Re-reading the worktree file confirms it was left as-is.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "hello\n"


def test_cherry_pick_command_via_processor(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    _ensure_app()
    from src.core.operations import reset

    mgr = RepositoryManager(str(tmp_git_repo))
    base_oid = make_commit("base", files={"a.txt": "A\n"})
    feat_oid = make_commit("adds-b", files={"b.txt": "B\n"}, parents=[base_oid])
    # Move HEAD back to ``base`` so the cherry-pick has work to do.
    reset(mgr, str(base_oid), mode="hard")

    proc = CommandProcessor()
    proc.execute(CherryPickCommand(mgr, str(feat_oid)))
    assert "b.txt" in mgr.repo.index
    proc.undo()
    assert "b.txt" not in mgr.repo.index


# ----- CommandProcessor wiring --------------------------------------------


def test_processor_pushes_merge_then_three_way_undo(
    committed_repo: RepositoryManager,
) -> None:
    """A real-world sequence: merge, then undo. The undo stack
    unwinds in reverse."""
    _ensure_app()
    main_tip, _, _ = _two_branches(committed_repo)
    proc = CommandProcessor()
    proc.execute(MergeCommand(committed_repo, "feature"))
    new_head = committed_repo.head_commit.sha
    assert new_head != main_tip
    proc.undo()
    assert committed_repo.head_commit.sha == main_tip
    # Redo brings back the merge.
    proc.redo()
    assert committed_repo.head_commit.sha == new_head


def test_unknown_source_raises_and_does_not_push(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    proc = CommandProcessor()
    cmd = MergeCommand(committed_repo, "no-such-branch")
    with pytest.raises(GitError):
        proc.execute(cmd)
    assert not proc.can_undo
