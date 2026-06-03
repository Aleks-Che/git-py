"""Tests for the Git operation wrappers in :mod:`src.core.operations`.

Each operation is tested for the happy path plus the failure mode that
maps to a specific domain exception. Real network operations
(clone/push/pull/fetch) are exercised against a local bare repo
``origin`` so the test suite never touches the network.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import (
    GitError,
    GitNotInstalledError,
    InvalidRefError,
    MergeConflictError,
)
from src.core.models import FileStatus
from src.core.operations import (
    checkout_branch,
    cherry_pick,
    commit_changes,
    create_branch,
    delete_branch,
    fetch,
    merge_branch,
    pull,
    push,
    rebase_branch,
    rename_branch,
    reset,
    stash_pop,
    stash_push,
)
from src.core.repository import RepositoryManager

# ----- commit --------------------------------------------------------------


def test_commit_changes_creates_a_new_head(
    committed_repo: RepositoryManager,
) -> None:
    before = committed_repo.head_commit.sha
    (committed_repo.path and Path(committed_repo.path) / "new.txt").write_text("n\n")
    info = commit_changes(committed_repo, "add new")
    assert info.parents == [before]
    assert committed_repo.head_commit.sha == info.sha
    assert "add new" in committed_repo.head_commit.message


def test_commit_changes_rejects_empty_message(committed_repo: RepositoryManager) -> None:
    with pytest.raises(GitError, match="must not be empty"):
        commit_changes(committed_repo, "   ")


def test_commit_changes_on_unborn_head_raises(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    with pytest.raises(GitError, match="unborn"):
        commit_changes(mgr, "first")


# ----- branches ------------------------------------------------------------


def test_create_and_delete_branch(committed_repo: RepositoryManager) -> None:
    create_branch(committed_repo, "feature")
    assert any(b.name == "feature" for b in committed_repo.branches)
    delete_branch(committed_repo, "feature")
    assert not any(b.name == "feature" for b in committed_repo.branches)


def test_create_branch_with_explicit_target(
    committed_repo: RepositoryManager,
) -> None:
    parent_sha = committed_repo.head_commit.parents[0]
    create_branch(committed_repo, "old", target_sha=parent_sha)
    branch = next(b for b in committed_repo.branches if b.name == "old")
    assert branch.target_sha == parent_sha


def test_create_branch_unknown_target_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        create_branch(committed_repo, "bad", target_sha="0" * 40)


def test_delete_branch_refuses_current_without_force(
    committed_repo: RepositoryManager,
) -> None:
    with pytest.raises(GitError, match="current branch"):
        delete_branch(committed_repo, "main")


def test_delete_branch_unknown_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        delete_branch(committed_repo, "does-not-exist")


def test_checkout_branch_switches_head(committed_repo: RepositoryManager) -> None:
    # Branch off the very first commit (no parents) so the switch is observable.
    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.parents[0])
    checkout_branch(committed_repo, "feature")
    assert committed_repo.head_commit.parents == []


def test_checkout_unknown_branch_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        checkout_branch(committed_repo, "nope")


def test_rename_branch_changes_ref_name(committed_repo: RepositoryManager) -> None:
    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    rename_branch(committed_repo, "feature", "renamed")
    names = {b.name for b in committed_repo.branches if not b.is_remote}
    assert "feature" not in names
    assert "renamed" in names


def test_rename_branch_preserves_target_sha(committed_repo: RepositoryManager) -> None:
    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    target = committed_repo.head_commit.sha
    rename_branch(committed_repo, "feature", "renamed")
    branch = next(b for b in committed_repo.branches if b.name == "renamed")
    assert branch.target_sha == target


def test_rename_branch_unknown_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        rename_branch(committed_repo, "does-not-exist", "renamed")


def test_rename_branch_collides_without_force(committed_repo: RepositoryManager) -> None:
    create_branch(committed_repo, "a")
    create_branch(committed_repo, "b")
    with pytest.raises(GitError, match="already exists"):
        rename_branch(committed_repo, "a", "b")


def test_rename_branch_collides_with_force(committed_repo: RepositoryManager) -> None:
    create_branch(committed_repo, "a")
    create_branch(committed_repo, "b")
    rename_branch(committed_repo, "a", "b", force=True)
    names = [b.name for b in committed_repo.branches if not b.is_remote]
    assert "a" not in names
    assert names.count("b") == 1


# ----- merge / cherry-pick / revert ----------------------------------------


def test_merge_fast_forward_moves_ref(committed_repo: RepositoryManager) -> None:
    # committed_repo starts with 2 commits (c1, c2 on main). Branch off
    # the current HEAD so the feature commit is a strict descendant of
    # main and the merge becomes a fast-forward.
    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    feat_head = commit_changes(committed_repo, "add f").sha
    checkout_branch(committed_repo, "main")
    result = merge_branch(committed_repo, "feature")
    assert result is False  # fast-forward
    assert committed_repo.head_commit.sha == feat_head


def test_merge_three_way_returns_true(committed_repo: RepositoryManager) -> None:
    # Two diverging branches: feature and main each add a different file,
    # then merge -> a real three-way merge creates one extra commit.
    before = len(committed_repo.get_history())
    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "m.txt").write_text("m\n")
    commit_changes(committed_repo, "add m")
    result = merge_branch(committed_repo, "feature")
    assert result is True  # real three-way merge
    assert len(committed_repo.get_history()) == before + 3  # +1 on feature, +1 on main, +1 merge


def test_merge_conflict_raises(committed_repo: RepositoryManager) -> None:
    # Both branches edit hello.txt with different content -> conflict.
    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "hello.txt").write_text("feature says hi\n")
    commit_changes(committed_repo, "feature hello")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "hello.txt").write_text("main says hi\n")
    commit_changes(committed_repo, "main hello")
    with pytest.raises(MergeConflictError) as exc_info:
        merge_branch(committed_repo, "feature")
    assert exc_info.value.conflicting_paths == ["hello.txt"]


def test_cherry_pick_copies_commit_onto_head(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    base = make_commit("base", files={"a.txt": "A\n"})
    feat_oid = make_commit("feat-adds-b", files={"b.txt": "B\n"}, parents=[base])
    # HEAD is on main at `feat-adds-b`; cherry-pick it onto the same HEAD
    # should still return a commit with the same message.
    info = cherry_pick(mgr, str(feat_oid))
    assert info.message.strip() == "feat-adds-b"


def test_reset_mixed_moves_head_and_keeps_worktree(
    committed_repo: RepositoryManager,
) -> None:
    target = committed_repo.head_commit.parents[0]
    reset(committed_repo, target, mode="mixed")
    # HEAD is rewound; index is reset to match HEAD.
    assert committed_repo.head_commit.sha == target
    # ``git status`` reports nothing in the index; the worktree file is
    # dirty vs the rewound HEAD, so it shows up as a worktree modification.
    statuses = {c.path: c.status for c in committed_repo.get_status()}
    assert statuses == {"hello.txt": FileStatus.MODIFIED}


def test_reset_invalid_mode_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(GitError, match="Invalid reset mode"):
        reset(committed_repo, "HEAD", mode="gentle")


def test_reset_unknown_target_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        reset(committed_repo, "0" * 40)


# ----- stash ---------------------------------------------------------------


def test_stash_push_and_pop_roundtrip(committed_repo: RepositoryManager) -> None:
    assert committed_repo.path is not None
    (Path(committed_repo.path) / "hello.txt").write_text("uncommitted\n")
    oid = stash_push(committed_repo, "wip")
    assert oid is not None
    assert len(committed_repo.stash_list) == 1
    # libgit2 prefixes the user message with "On <branch>: " — we just
    # check the user suffix is preserved.
    assert committed_repo.stash_list[0].message.endswith("wip")
    assert committed_repo.get_status() == []
    stash_pop(committed_repo)
    assert any(c.path == "hello.txt" for c in committed_repo.get_status())


def test_stash_push_returns_none_when_clean(committed_repo: RepositoryManager) -> None:
    assert stash_push(committed_repo) is None


# ----- rebase --------------------------------------------------------------


def test_rebase_branch_raises_when_git_cli_missing(
    committed_repo: RepositoryManager,
    monkeypatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(GitNotInstalledError):
        rebase_branch(committed_repo, "HEAD")


# ----- remotes (local bare repo as origin) --------------------------------


@pytest.fixture
def origin_and_clone(tmp_git_repo: Path) -> tuple[RepositoryManager, RepositoryManager, Path]:
    """Build a bare ``origin`` and a working ``clone`` of it, both on disk.

    Returns ``(origin_manager, clone_manager, clone_path)``. Both have at
    least one commit on the default branch (whatever libgit2 picked —
    ``main`` on modern systems, ``master`` on older ones).
    """
    base = tmp_git_repo
    origin_path = base / "origin.git"
    clone_path = base / "clone"
    pygit2.init_repository(str(origin_path), bare=True)
    pygit2.clone_repository(str(origin_path), str(clone_path))
    sig = pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)
    (clone_path / "f.txt").write_text("x\n")
    clone = pygit2.Repository(str(clone_path))
    clone.index.add("f.txt")
    clone.index.write()
    tree = clone.index.write_tree()
    clone.create_commit("HEAD", sig, sig, "init", tree, [])
    branch_name = clone.head.shorthand
    push(clone, "origin", f"refs/heads/{branch_name}")
    return (
        RepositoryManager(str(origin_path)),
        RepositoryManager(str(clone_path)),
        clone_path,
    )


def test_push_and_fetch_via_local_origin(origin_and_clone) -> None:
    _origin, clone, _ = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = clone.head_commit and next(b.name for b in clone.branches if b.is_head)
    assert branch is not None
    # Add a new commit and push it.
    (clone_root / "g.txt").write_text("g\n")
    commit_changes(clone, "add g")
    push(clone, "origin", f"refs/heads/{branch}")
    # Now create a second clone, fetch, and verify the new commit is reachable.
    second = clone_root.parent / "second"
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(second))
    second_mgr = RepositoryManager(str(second))
    fetch(second_mgr, "origin")
    second_mgr.repo.lookup_reference(f"refs/remotes/origin/{branch}").resolve()
    # After resetting local main to the fetched remote, the new commit is on top.
    reset(second_mgr, f"origin/{branch}", mode="hard")
    assert second_mgr.head_commit.message.strip() == "add g"


def test_pull_brings_remote_changes_into_local(origin_and_clone) -> None:
    _origin, clone, _ = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)
    # Push a new commit to origin from the working clone.
    (clone_root / "g.txt").write_text("g\n")
    commit_changes(clone, "add g")
    push(clone, "origin", f"refs/heads/{branch}")
    # Spin up a fresh clone and pull — it should pick up the new commit.
    second = clone_root.parent / "second"
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(second))
    second_mgr = RepositoryManager(str(second))
    pull(second_mgr, "origin", f"refs/heads/{branch}")
    assert second_mgr.head_commit.message.strip() == "add g"


def test_push_to_unknown_remote_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        push(committed_repo, "no-such-remote", "refs/heads/main")


def test_fetch_from_unknown_remote_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        fetch(committed_repo, "no-such-remote")
