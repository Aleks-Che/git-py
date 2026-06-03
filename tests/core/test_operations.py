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
    RebaseConflictError,
)
from src.core.models import FileStatus
from src.core.operations import (
    _url_needs_cli_fallback as url_needs_cli_fallback,
)
from src.core.operations import (
    abort_merge,
    abort_rebase,
    add_remote,
    checkout_branch,
    cherry_pick,
    commit_changes,
    complete_merge,
    complete_rebase_continue,
    create_branch,
    delete_branch,
    fetch,
    is_merge_in_progress,
    is_rebase_in_progress,
    list_remotes,
    merge_branch,
    pull,
    push,
    rebase_branch,
    remove_remote,
    rename_branch,
    reset,
    revert,
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


# ----- remote management (add / remove / list) ----------------------------


def test_list_remotes_empty(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    assert list_remotes(mgr) == []


def test_add_remote_creates_remote(committed_repo: RepositoryManager) -> None:
    name = add_remote(committed_repo, "upstream", "https://example.com/upstream.git")
    assert name == "upstream"
    remotes = list_remotes(committed_repo)
    assert [r.name for r in remotes] == ["upstream"]
    assert remotes[0].url == "https://example.com/upstream.git"
    # ``+refs/heads/*:refs/remotes/upstream/*`` is libgit2's default
    # fetch refspec when none was given explicitly.
    assert "refs/heads" in remotes[0].fetch_refspec


def test_add_remote_already_exists_raises(committed_repo: RepositoryManager) -> None:
    add_remote(committed_repo, "origin", "https://example.com/origin.git")
    with pytest.raises(GitError, match="already exists"):
        add_remote(committed_repo, "origin", "https://example.com/x.git")


def test_add_remote_rejects_empty_name(committed_repo: RepositoryManager) -> None:
    with pytest.raises(GitError, match="name"):
        add_remote(committed_repo, "", "https://x.git")


def test_add_remote_rejects_empty_url(committed_repo: RepositoryManager) -> None:
    with pytest.raises(GitError, match="URL"):
        add_remote(committed_repo, "upstream", "   ")


def test_remove_remote_deletes_it(committed_repo: RepositoryManager) -> None:
    add_remote(committed_repo, "origin", "https://example.com/origin.git")
    assert list_remotes(committed_repo)
    remove_remote(committed_repo, "origin")
    assert list_remotes(committed_repo) == []


def test_remove_remote_unknown_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        remove_remote(committed_repo, "no-such-remote")


def test_list_remotes_returns_snapshots(committed_repo: RepositoryManager) -> None:
    add_remote(committed_repo, "origin", "https://example.com/origin.git")
    add_remote(committed_repo, "upstream", "git@example.com:foo.git")
    remotes = list_remotes(committed_repo)
    assert {r.name for r in remotes} == {"origin", "upstream"}
    # Returned list is a fresh copy (mutating it does not affect the next call).
    remotes.clear()
    assert len(list_remotes(committed_repo)) == 2


def test_push_auth_error_uses_domain_exception(
    monkeypatch, committed_repo: RepositoryManager,
) -> None:
    """A simulated auth failure surfaces as :class:`AuthError`."""

    class _FakeRemote:
        url = "https://example.com/repo.git"

        def push(self, *args: object, **kwargs: object) -> None:
            msg = "authentication failed for 'https://x@example.com/repo.git'"
            raise pygit2.GitError(msg)

    class _FakeRemotes:
        def __getitem__(self, name: str) -> _FakeRemote:
            return _FakeRemote()

    monkeypatch.setattr(committed_repo.repo, "remotes", _FakeRemotes())
    from src.core.exceptions import AuthError

    with pytest.raises(AuthError):
        push(committed_repo, "origin", "refs/heads/main")


# ----- merge / rebase state, abort, and finalize ---------------------------


def _create_conflict_setup(
    committed_repo: RepositoryManager,
) -> None:
    """Build a 2-way conflict in ``hello.txt`` on the current branch.

    Starts from ``committed_repo`` (HEAD on ``main`` with one tracked
    file ``hello.txt``). Branches ``feature`` off HEAD, edits
    ``hello.txt`` to a feature version, commits. Switches back to
    ``main``, edits ``hello.txt`` to a main version, commits. The two
    branches now diverge on the same line.
    """
    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    assert committed_repo.path is not None
    (Path(committed_repo.path) / "hello.txt").write_text("feature says hi\n")
    commit_changes(committed_repo, "feature hello")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "hello.txt").write_text("main says hi\n")
    commit_changes(committed_repo, "main hello")


def test_is_merge_in_progress_false_on_clean(committed_repo: RepositoryManager) -> None:
    assert is_merge_in_progress(committed_repo) is False


def test_is_merge_in_progress_true_during_conflict(
    committed_repo: RepositoryManager,
) -> None:
    _create_conflict_setup(committed_repo)
    with pytest.raises(MergeConflictError):
        merge_branch(committed_repo, "feature")
    assert is_merge_in_progress(committed_repo) is True


def test_abort_merge_restores_clean_state(committed_repo: RepositoryManager) -> None:
    _create_conflict_setup(committed_repo)
    with pytest.raises(MergeConflictError):
        merge_branch(committed_repo, "feature")
    abort_merge(committed_repo)
    assert is_merge_in_progress(committed_repo) is False
    # Worktree returns to the main version of hello.txt.
    assert committed_repo.path is not None
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "main says hi\n"


def test_abort_merge_without_in_progress_raises(
    committed_repo: RepositoryManager,
) -> None:
    with pytest.raises(GitError, match="No merge in progress"):
        abort_merge(committed_repo)


def test_abort_merge_without_git_cli_raises(
    committed_repo: RepositoryManager,
    monkeypatch,
) -> None:
    _create_conflict_setup(committed_repo)
    with pytest.raises(MergeConflictError):
        merge_branch(committed_repo, "feature")
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(GitNotInstalledError):
        abort_merge(committed_repo)


def test_complete_merge_finalizes_resolved_conflict(
    committed_repo: RepositoryManager,
) -> None:
    _create_conflict_setup(committed_repo)
    feature_head = next(
        b.target_sha for b in committed_repo.branches if b.name == "feature"
    )
    with pytest.raises(MergeConflictError):
        merge_branch(committed_repo, "feature")
    # User resolves: pick "main" version, then stage.
    assert committed_repo.path is not None
    (Path(committed_repo.path) / "hello.txt").write_text("resolved!\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    new_sha = complete_merge(committed_repo, "feature", target="main")
    assert is_merge_in_progress(committed_repo) is False
    assert committed_repo.head_commit.sha == new_sha
    # Two parents: main's previous HEAD and the feature tip.
    parents = committed_repo.head_commit.parents
    assert len(parents) == 2
    assert feature_head in parents
    # Target branch ref now points at the merge commit.
    main_ref = committed_repo.repo.lookup_reference("refs/heads/main")
    assert str(main_ref.target) == new_sha


def test_complete_merge_with_remaining_conflicts_raises(
    committed_repo: RepositoryManager,
) -> None:
    _create_conflict_setup(committed_repo)
    with pytest.raises(MergeConflictError):
        merge_branch(committed_repo, "feature")
    # No resolution attempted.
    with pytest.raises(MergeConflictError, match="conflicts remain"):
        complete_merge(committed_repo, "feature", target="main")


def test_complete_merge_without_in_progress_raises(
    committed_repo: RepositoryManager,
) -> None:
    with pytest.raises(GitError, match="No merge in progress"):
        complete_merge(committed_repo, "feature")


def test_complete_merge_unknown_source_raises(
    committed_repo: RepositoryManager,
) -> None:
    _create_conflict_setup(committed_repo)
    with pytest.raises(MergeConflictError):
        merge_branch(committed_repo, "feature")
    # Resolve the conflict so the "conflicts remain" guard is passed.
    assert committed_repo.path is not None
    (Path(committed_repo.path) / "hello.txt").write_text("ok\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    with pytest.raises(InvalidRefError):
        complete_merge(committed_repo, "no-such-source")


# ----- rebase ----------------------------------------------------------------


def test_is_rebase_in_progress_false_on_clean(
    committed_repo: RepositoryManager,
) -> None:
    assert is_rebase_in_progress(committed_repo) is False


def test_is_rebase_in_progress_true_during_conflict(
    committed_repo: RepositoryManager,
) -> None:
    # Build a divergent history: main has commit A, feature has commit
    # A' (same parent, different content) so rebase feature onto main
    # conflicts on hello.txt.
    _create_conflict_setup(committed_repo)
    # We are on main; switch to feature and try to rebase onto main.
    checkout_branch(committed_repo, "feature")
    with pytest.raises(RebaseConflictError):
        rebase_branch(committed_repo, "main")
    assert is_rebase_in_progress(committed_repo) is True


def test_abort_rebase_restores_clean_state(
    committed_repo: RepositoryManager,
) -> None:
    _create_conflict_setup(committed_repo)
    feature_head_before = committed_repo.repo.lookup_reference(
        "refs/heads/feature",
    ).target
    checkout_branch(committed_repo, "feature")
    with pytest.raises(RebaseConflictError):
        rebase_branch(committed_repo, "main")
    abort_rebase(committed_repo)
    assert is_rebase_in_progress(committed_repo) is False
    feature_head_after = committed_repo.repo.lookup_reference(
        "refs/heads/feature",
    ).target
    assert feature_head_after == feature_head_before


def test_abort_rebase_without_in_progress_raises(
    committed_repo: RepositoryManager,
) -> None:
    with pytest.raises(GitError, match="No rebase in progress"):
        abort_rebase(committed_repo)


def test_complete_rebase_continue_without_in_progress_raises(
    committed_repo: RepositoryManager,
) -> None:
    with pytest.raises(GitError, match="No rebase in progress"):
        complete_rebase_continue(committed_repo)


# ----- cherry-pick / revert (smoke) ------------------------------------------


def test_cherry_pick_clean_returns_commit_info(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    base = make_commit("base", files={"a.txt": "A\n"})
    feat = make_commit("adds-b", files={"b.txt": "B\n"}, parents=[base])
    # cherry_pick only stages the change; HEAD does not move.
    info = cherry_pick(mgr, str(feat))
    assert info.sha == mgr.head_commit.sha
    # b.txt was added by the cherry-pick; check it's staged in the index.
    assert "b.txt" in mgr.repo.index


def test_cherry_pick_conflict_raises_with_paths(
    committed_repo: RepositoryManager,
) -> None:
    # feature modifies hello.txt, main modifies hello.txt differently
    # (so cherry-picking feature onto main conflicts).
    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    assert committed_repo.path is not None
    (Path(committed_repo.path) / "hello.txt").write_text("feature side\n")
    commit_changes(committed_repo, "feature side")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "hello.txt").write_text("main side\n")
    commit_changes(committed_repo, "main side")
    feature_sha = next(
        b.target_sha for b in committed_repo.branches if b.name == "feature"
    )
    with pytest.raises(MergeConflictError) as exc_info:
        cherry_pick(committed_repo, feature_sha)
    assert "hello.txt" in exc_info.value.conflicting_paths


def test_revert_clean_returns_commit_info(
    committed_repo: RepositoryManager,
) -> None:
    target_sha = committed_repo.head_commit.sha
    info = revert(committed_repo, target_sha)
    # ``revert()`` mirrors ``cherry_pick()`` — it stages the inverse
    # change but does not commit. The returned CommitInfo is HEAD,
    # which has not moved.
    assert info.sha == target_sha


# ----- SSH URL fallback detection ----------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        # SCP-style (common GitHub / GitLab SSH)
        ("git@github.com:user/repo.git", True),
        ("git@gitlab.com:group/project.git", True),
        ("git@codeberg.org:user/repo.git", True),
        # ssh:// URL-style
        ("ssh://git@github.com/user/repo.git", True),
        ("ssh://git@github.com:22/user/repo.git", True),
        ("ssh://user@host.xz:path/to/repo.git", True),
        # git+ssh:// variant
        ("git+ssh://git@github.com/user/repo.git", True),
        # HTTPS / other — handled by pygit2
        ("https://github.com/user/repo.git", False),
        ("http://example.com/repo.git", False),
        ("https://gitlab.com/user/project.git", False),
        ("file:///path/to/repo", False),
        ("git://git.kernel.org/xxx.git", False),
        # Empty / None / weird
        ("", False),
        ("user@host:path", True),  # SCP-style without explicit "git" user
    ],
)
def test_url_needs_cli_fallback(url: str, expected: bool) -> None:
    """SSH URLs are detected so fetch can route them through the git CLI."""
    assert url_needs_cli_fallback(url) is expected


def test_fetch_from_local_origin_still_works(
    origin_and_clone,
) -> None:
    """Regression: file://-backed ``origin`` still goes through pygit2."""
    _origin, clone, _ = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    (clone_root / "h.txt").write_text("h\n")
    commit_changes(clone, "add h")
    push(clone, "origin", f"refs/heads/{branch}")

    second = clone_root.parent / "fetch_clone"
    if second.exists():
        shutil.rmtree(second)
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(second))
    second_mgr = RepositoryManager(str(second))

    # This must succeed — the origin URL is file://, detected as
    # **not** needing the CLI fallback, so pygit2 handles it.
    fetch(second_mgr, "origin")
    second_mgr.repo.lookup_reference(f"refs/remotes/origin/{branch}").resolve()
