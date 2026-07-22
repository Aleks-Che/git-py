"""Tests for :class:`src.core.repository.RepositoryManager`.

Covers open/init/clone lifecycle, the typed properties (``head_commit``,
``branches``, ``tags``, ``stash_list``), the query methods
(``get_status``, ``get_history``, ``get_commit``), and the error
translations that convert ``pygit2.GitError`` into domain exceptions.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError, InvalidRefError, RepositoryNotFoundError
from src.core.models import (
    BranchInfo,
    CommitInfo,
    FileChange,
    FileStatus,
)
from src.core.operations import stash_push
from src.core.repository import RepositoryManager

# ----- lifecycle -----------------------------------------------------------


def test_open_existing_repo(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager()
    mgr.open(str(tmp_git_repo))
    assert mgr.is_open
    assert mgr.path == str(tmp_git_repo)
    assert mgr.repo is not None


def test_open_via_constructor(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    assert mgr.is_open
    assert mgr.path == str(tmp_git_repo)


def test_open_nonexistent_path_raises(tmp_path: Path) -> None:
    with pytest.raises(RepositoryNotFoundError):
        RepositoryManager(str(tmp_path / "missing"))


def test_open_non_git_path_raises(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(RepositoryNotFoundError):
        RepositoryManager(str(not_a_repo))


def test_open_with_nonexistent_path_does_not_leak_repo_handle(tmp_path: Path) -> None:
    """M2 — a failed ``open()`` must not corrupt the manager's state.

    Before the fix, ``open()`` assigned to ``self._repo`` *inside* the
    ``try`` block, which meant a half-constructed object could leave the
    manager pointing at a stale handle. After the fix the assignment
    happens only after ``pygit2.Repository()`` returns successfully; a
    failure leaves ``_repo`` and ``_path`` exactly as they were.
    """
    mgr = RepositoryManager()
    assert mgr._repo is None
    assert mgr._path is None
    with pytest.raises(RepositoryNotFoundError):
        mgr.open(str(tmp_path / "does-not-exist"))
    assert mgr._repo is None
    assert mgr._path is None
    assert not mgr.is_open


def test_open_non_git_after_successful_open_leaves_prior_repo_intact(
    tmp_path: Path, tmp_git_repo: Path,
) -> None:
    """M2 — opening a non-repo on an already-open manager must keep the previous handle.

    The previous implementation would only assign ``_path`` after both
    ``p.exists()`` and ``pygit2.Repository()`` succeeded, but on failure
    it still updated nothing else. The fix keeps the manager's state
    untouched so the user can recover.
    """
    mgr = RepositoryManager(str(tmp_git_repo))
    assert mgr.is_open
    prior_repo = mgr.repo
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    with pytest.raises(RepositoryNotFoundError):
        mgr.open(str(not_a_repo))
    assert mgr.is_open
    assert mgr.path == str(tmp_git_repo)
    assert mgr.repo is prior_repo


def test_init_creates_empty_repo(tmp_path: Path) -> None:
    target = tmp_path / "new"
    mgr = RepositoryManager()
    mgr.init(str(target))
    assert (target / ".git").exists()
    assert mgr.repo.head_is_unborn
    assert mgr.is_bare is False


def test_init_with_custom_initial_head(tmp_path: Path) -> None:
    target = tmp_path / "new"
    mgr = RepositoryManager()
    mgr.init(str(target), initial_head="trunk")
    assert mgr.repo.head_is_unborn
    # An unborn HEAD can't be read via .shorthand; the symbolic name lives in .git/HEAD.
    head_file = (target / ".git" / "HEAD").read_text().strip()
    assert head_file == "ref: refs/heads/trunk"


def test_init_bare_repo(tmp_path: Path) -> None:
    target = tmp_path / "bare.git"
    mgr = RepositoryManager()
    mgr.init(str(target), bare=True)
    assert mgr.is_bare is True
    assert (target / "HEAD").exists()
    assert (target / "objects").exists()


def test_is_valid_recognises_normal_and_bare_repos(tmp_git_repo: Path) -> None:
    assert RepositoryManager.is_valid(str(tmp_git_repo)) is True
    assert RepositoryManager.is_valid("/no/such/path") is False
    assert RepositoryManager.is_valid(str(tmp_git_repo / "missing")) is False


def test_close_drops_handle_without_touching_disk(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    mgr.close()
    assert mgr.is_open is False
    assert mgr.path is None
    assert (tmp_git_repo / ".git").is_dir()  # on-disk repo is untouched


# ----- properties ---------------------------------------------------------


def test_head_commit_raises_when_unborn(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    with pytest.raises(GitError, match="unborn"):
        _ = mgr.head_commit


def test_head_commit_returns_commit_info(committed_repo: RepositoryManager) -> None:
    info = committed_repo.head_commit
    assert isinstance(info, CommitInfo)
    assert info.message.strip() == "greet the world"
    assert len(info.sha) == 40
    assert info.short_sha == info.sha[:7]
    assert info.author_name == "tester"
    assert info.parents  # second commit has a parent


def test_branches_includes_head_and_remote_only_when_present(
    committed_repo: RepositoryManager,
) -> None:
    branches = committed_repo.branches
    assert len(branches) == 1
    assert branches[0] == BranchInfo(
        name="main",
        is_head=True,
        is_remote=False,
        target_sha=branches[0].target_sha,
    )


def test_branches_resolves_symref_remote_target(
    committed_repo: RepositoryManager,
) -> None:
    """Regression: a symbolic remote branch like ``origin/HEAD`` must
    get a real commit SHA as ``target_sha``, not the symbolic path
    ``refs/remotes/origin/main``."""
    repo = committed_repo.repo
    head_sha = committed_repo.head_commit.sha
    head_oid = pygit2.Oid(bytes.fromhex(head_sha))

    repo.references.create("refs/remotes/origin/main", head_oid)
    repo.references.create("refs/remotes/origin/HEAD", "refs/remotes/origin/main", force=True)

    by_name = {b.name: b for b in committed_repo.branches}

    assert "origin/main" in by_name
    assert "origin/HEAD" in by_name
    sha = by_name["origin/HEAD"].target_sha
    assert sha and len(sha) == 40 and sha == head_sha, f"{sha!r} != {head_sha}"


def test_branches_skips_broken_symref(
    committed_repo: RepositoryManager,
) -> None:
    """A remote symref whose target reference does not exist is omitted."""
    repo = committed_repo.repo
    repo.references.create(
        "refs/remotes/origin/DEAD", "refs/remotes/origin/nonexistent", force=True
    )
    assert not any(b.name == "origin/DEAD" for b in committed_repo.branches)


def test_get_all_history_includes_remote_symref_tip(
    committed_repo: RepositoryManager,
) -> None:
    """A commit only reachable via a symbolic remote ref must still appear."""
    repo = committed_repo.repo
    head_sha = committed_repo.head_commit.sha
    head_oid = pygit2.Oid(bytes.fromhex(head_sha))

    repo.references.create("refs/remotes/origin/main", head_oid)
    repo.references.create("refs/remotes/origin/HEAD", "refs/remotes/origin/main", force=True)

    shas = {c.sha for c in committed_repo.get_all_history()}
    assert head_sha in shas


def test_tags_includes_lightweight_and_annotated(committed_repo: RepositoryManager) -> None:
    repo = committed_repo.repo
    sig = pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)
    head_oid = pygit2.Oid(bytes.fromhex(committed_repo.head_commit.sha))
    # Lightweight tag (no tagger/message, just a ref).
    repo.references.create("refs/tags/v0.1", head_oid)
    # Annotated tag.
    repo.create_tag("v0.2", head_oid, pygit2.GIT_OBJECT_COMMIT, sig, "release notes")

    tags = committed_repo.tags
    by_name = {t.name: t for t in tags}
    assert set(by_name) == {"v0.1", "v0.2"}
    assert by_name["v0.1"].is_annotated is False
    assert by_name["v0.2"].is_annotated is True
    assert by_name["v0.2"].message == "release notes"


def test_repository_tags_returns_git_error_for_missing_ref(
    committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """A tag deleted between listing and lookup becomes a domain error."""
    repo = committed_repo.repo
    head_oid = pygit2.Oid(bytes.fromhex(committed_repo.head_commit.sha))
    repo.references.create("refs/tags/race", head_oid)
    original_lookup = repo.lookup_reference

    def _lookup(ref_name: str):
        if ref_name == "refs/tags/race":
            original_lookup(ref_name).delete()
        return original_lookup(ref_name)

    monkeypatch.setattr(repo, "lookup_reference", _lookup)
    with pytest.raises(GitError, match="Cannot resolve tag"):
        _ = committed_repo.tags


def test_stash_list_is_empty_by_default(committed_repo: RepositoryManager) -> None:
    assert committed_repo.stash_list == []


# ----- queries -------------------------------------------------------------


def test_get_status_reports_untracked_file(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    (tmp_git_repo / "scratch.txt").write_text("x\n")
    changes = mgr.get_status()
    assert changes == [FileChange(path="scratch.txt", status=FileStatus.UNTRACKED)]


def test_get_status_prioritises_index_over_worktree(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    (tmp_git_repo / "f.txt").write_text("a\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    (tmp_git_repo / "f.txt").write_text("a modified\n")  # also dirty in worktree

    changes = {c.path: c.status for c in mgr.get_status()}
    assert changes["f.txt"] == FileStatus.NEW  # staged-new wins over wt-modified


def test_get_status_marks_conflicts(committed_repo: RepositoryManager) -> None:
    # We exercise the conflict/ignored paths in the bitfield -> FileStatus
    # mapping without having to plant a real conflict in the index.
    assert RepositoryManager._map_status(pygit2.GIT_STATUS_CONFLICTED) == FileStatus.CONFLICTED  # noqa: SLF001
    assert RepositoryManager._map_status(pygit2.GIT_STATUS_IGNORED) == FileStatus.IGNORED  # noqa: SLF001


def test_get_history_walks_newest_first(committed_repo: RepositoryManager) -> None:
    history = committed_repo.get_history()
    assert [c.message.strip() for c in history] == ["greet the world", "init: hello"]


def test_get_history_respects_max_count(committed_repo: RepositoryManager) -> None:
    assert len(committed_repo.get_history(max_count=1)) == 1


def test_get_history_on_empty_repo_returns_empty_list(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    assert mgr.get_history() == []


def test_get_history_unknown_branch_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        committed_repo.get_history(branch="does-not-exist")


def test_get_all_history_returns_empty_for_unborn_head(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    assert mgr.get_all_history() == []


def test_get_all_history_includes_commits_only_reachable_via_tags(
    committed_repo: RepositoryManager,
) -> None:
    # A tag on a non-tip commit should still bring that commit into the DAG.
    repo = committed_repo.repo
    init_sha = committed_repo.get_history()[-1].sha  # oldest commit
    repo.references.create("refs/tags/at-init", pygit2.Oid(bytes.fromhex(init_sha)))
    history = committed_repo.get_all_history()
    shas = {c.sha for c in history}
    assert committed_repo.head_commit.sha in shas
    assert init_sha in shas


def test_get_all_history_merges_branches(
    committed_repo: RepositoryManager, make_commit
) -> None:
    # Add a feature branch off the initial commit and a commit on it.
    init_sha = committed_repo.get_history()[-1].sha
    feat_parents = [pygit2.Oid(bytes.fromhex(init_sha))]
    make_commit(
        "feat: add thing",
        files={"a.txt": "a\n"},
        ref="refs/heads/feature",
        parents=feat_parents,
    )
    history = committed_repo.get_all_history()
    shas = {c.sha for c in history}
    assert committed_repo.head_commit.sha in shas  # main tip
    assert any(c.message.strip() == "feat: add thing" for c in history)
    # Newest first.
    n = len(history)
    assert all(history[i].author_time >= history[i + 1].author_time for i in range(n - 1))


def test_get_all_history_respects_max_count(committed_repo: RepositoryManager) -> None:
    assert len(committed_repo.get_all_history(max_count=1)) == 1


def test_get_all_history_deduplicates_across_tips(
    committed_repo: RepositoryManager, make_commit
) -> None:
    # A commit reachable from two tags is reported exactly once.
    repo = committed_repo.repo
    head_sha = committed_repo.head_commit.sha
    repo.references.create("refs/tags/v1", pygit2.Oid(bytes.fromhex(head_sha)))
    repo.references.create("refs/tags/v2", pygit2.Oid(bytes.fromhex(head_sha)))
    history = committed_repo.get_all_history()
    counts = sum(1 for c in history if c.sha == head_sha)
    assert counts == 1


def test_get_all_history_keeps_topological_order_under_duplicate_timestamps(
    tmp_git_repo: Path,
) -> None:
    """Regression: with ``GIT_SORT_TIME`` only a parent commit could
    appear **after** its child when they shared a timestamp, which
    broke :mod:`src.core.graph_v2`. We now use
    ``GIT_SORT_TOPOLOGICAL | GIT_SORT_TIME`` so every commit is
    preceded only by its descendants.

    The fixture stamps the parent *after* the child on purpose: a
    pure time-based walk would emit ``[child, parent]``. Topological
    order must emit ``[child, parent]`` too (child first because it
    is the tip), but more importantly must never let a parent leak
    ahead of any of its descendants.
    """
    repo = pygit2.Repository(str(tmp_git_repo))
    base_time = 1_700_000_000

    # ``child`` has a strictly earlier timestamp than its ``parent``
    # so a time-sorted walk would mis-order them.
    child_sig = pygit2.Signature("tester", "t@example.com", base_time, 0)
    parent_sig = pygit2.Signature("tester", "t@example.com", base_time + 10, 0)
    # Inherit tree across commits so file content does not matter here.
    tree_oid = repo.TreeBuilder().write()
    parent_oid = repo.create_commit(
        "refs/heads/main", parent_sig, parent_sig,
        "parent", tree_oid, [],
    )
    child_oid = repo.create_commit(
        "refs/heads/main", child_sig, child_sig,
        "child", tree_oid, [parent_oid],
    )

    mgr = RepositoryManager(str(tmp_git_repo))
    history = mgr.get_all_history()
    shas = [c.sha for c in history]
    assert shas == [str(child_oid), str(parent_oid)], (
        f"Topological order broken under duplicate/inverted timestamps: {shas}"
    )
    assert history[0].author_time < history[1].author_time  # invariant used to break it


def test_get_all_history_merges_parallel_branches_in_topological_order(
    tmp_git_repo: Path,
) -> None:
    """Two parallel branches off a common root must interleave
    correctly: root, then either branch tip, then the other. The
    previous time-sorted multi-walk implementation could scatter the
    branches arbitrarily."""
    repo = pygit2.Repository(str(tmp_git_repo))
    base = 1_700_000_000

    def make_sig(offset: int) -> pygit2.Signature:
        return pygit2.Signature(
            "tester", "t@example.com", base + offset, 0,
        )

    tree_oid = repo.TreeBuilder().write()
    root_oid = repo.create_commit(
        "refs/heads/main", make_sig(0), make_sig(0), "root", tree_oid, [],
    )
    main_oid = repo.create_commit(
        "refs/heads/main", make_sig(10), make_sig(10), "main tip",
        tree_oid, [root_oid],
    )
    feat_oid = repo.create_commit(
        "refs/heads/feature", make_sig(20), make_sig(20), "feature tip",
        tree_oid, [root_oid],
    )

    mgr = RepositoryManager(str(tmp_git_repo))
    history = mgr.get_all_history()
    shas = [c.sha for c in history]

    # Root must come last (it is the oldest topologically).
    assert shas[-1] == str(root_oid)
    # Both tips must come before the root.
    assert shas.index(str(main_oid)) < shas.index(str(root_oid))
    assert shas.index(str(feat_oid)) < shas.index(str(root_oid))


def test_get_commit_resolves_short_sha(committed_repo: RepositoryManager) -> None:
    full = committed_repo.head_commit.sha
    short = full[:7]
    info = committed_repo.get_commit(short)
    assert info.sha == full


def test_get_commit_resolves_branch_name(committed_repo: RepositoryManager) -> None:
    info = committed_repo.get_commit("main")
    assert info.sha == committed_repo.head_commit.sha


def test_get_commit_unknown_revision_raises(committed_repo: RepositoryManager) -> None:
    with pytest.raises(InvalidRefError):
        committed_repo.get_commit("0" * 40)


# ----- accessors -----------------------------------------------------------


def test_repo_property_raises_when_closed(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager()
    with pytest.raises(GitError, match="No repository is open"):
        _ = mgr.repo


# ----- diff text ---------------------------------------------------------


def test_get_stash_diff_text_returns_unified_diff(
    committed_repo: RepositoryManager,
    tmp_git_repo: Path,
) -> None:
    """A stash entry's diff is its tree vs the original commit it was taken from."""
    (tmp_git_repo / "hello.txt").write_text("hello, stash\n")
    stash_push(committed_repo, "wip")

    stash = committed_repo.stash_list
    assert stash, "stash list should contain the entry we just created"

    text = committed_repo.get_stash_diff_text(stash[0].sha)

    assert "diff --git a/hello.txt b/hello.txt" in text
    assert "-hello, world" in text
    assert "+hello, stash" in text


def test_get_stash_diff_text_handles_unknown_sha(
    committed_repo: RepositoryManager,
) -> None:
    with pytest.raises(InvalidRefError):
        committed_repo.get_stash_diff_text("deadbeef" * 5)


def test_get_commit_file_diff_text_returns_patch_for_path(
    committed_repo: RepositoryManager,
    tmp_git_repo: Path,
) -> None:
    """Asking for a specific path returns just that file's patch, not
    the full multi-file diff."""
    # Add a second file and commit it together with a modification of
    # hello.txt so the multi-file diff is non-trivial.
    (tmp_git_repo / "extra.txt").write_text("extra\n")
    (tmp_git_repo / "hello.txt").write_text("hello, world!\n")
    committed_repo.repo.index.add("extra.txt")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    tree = committed_repo.repo.index.write_tree()
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    head_sha = committed_repo.head_commit.sha
    new_oid = committed_repo.repo.create_commit(
        "refs/heads/main", sig, sig, "two files", tree, [head_sha],
    )

    text = committed_repo.get_commit_file_diff_text(str(new_oid), "hello.txt")
    assert "diff --git a/hello.txt b/hello.txt" in text
    assert "+hello, world!" in text
    # The other file's patch is filtered out.
    assert "diff --git a/extra.txt b/extra.txt" not in text
    assert "+extra" not in text


def test_get_commit_file_diff_text_returns_empty_for_untouched_path(
    committed_repo: RepositoryManager,
) -> None:
    """A path the commit did not touch yields an empty string."""
    head_sha = committed_repo.head_commit.sha
    assert committed_repo.get_commit_file_diff_text(
        head_sha, "does_not_exist.txt",
    ) == ""


def test_get_commit_file_diff_text_handles_unknown_sha(
    committed_repo: RepositoryManager,
) -> None:
    with pytest.raises(InvalidRefError):
        committed_repo.get_commit_file_diff_text("deadbeef" * 5, "hello.txt")
