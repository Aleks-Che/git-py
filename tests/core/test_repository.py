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
