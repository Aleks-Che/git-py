"""Fast-forwardable merges must update files before advancing HEAD."""
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError
from src.core.operations import (
    checkout_branch,
    commit_changes,
    create_branch,
    is_merge_in_progress,
    merge_branch,
)
from src.core.repository import RepositoryManager


@pytest.fixture
def fast_forward_repo(committed_repo: RepositoryManager) -> RepositoryManager:
    repo = committed_repo
    root = Path(repo.path)
    (root / "removed.txt").write_text("remove me\n")
    (root / "notes.txt").write_text("unchanged notes\n")
    repo.repo.index.add("removed.txt")
    repo.repo.index.add("notes.txt")
    repo.repo.index.write()
    commit_changes(repo, "prepare base")
    create_branch(repo, "feature")
    checkout_branch(repo, "feature")
    (root / "hello.txt").write_text("feature version\n")
    (root / "removed.txt").unlink()
    (root / "added.txt").write_text("new file\n")
    repo.repo.index.add("added.txt")
    repo.repo.index.write()
    commit_changes(repo, "modify, delete and add files")
    checkout_branch(repo, "main")
    assert repo.repo.status() == {}
    return repo


@pytest.mark.parametrize("other_branch", [False, True])
@pytest.mark.parametrize("no_ff", [False, True])
def test_fast_forward_updates_index_and_worktree(
    fast_forward_repo: RepositoryManager, other_branch: bool, no_ff: bool,
) -> None:
    repo = fast_forward_repo
    root = Path(repo.path)
    before = repo.repo.head.target
    source = repo.repo.revparse_single("feature")
    if other_branch:
        create_branch(repo, "elsewhere")
        checkout_branch(repo, "elsewhere")

    assert merge_branch(repo, "feature", target="main", no_ff=no_ff) is no_ff

    # Reopen to check the persisted index as well as the application handle.
    fresh = pygit2.Repository(repo.path)
    assert fresh.head.shorthand == "main"
    assert fresh.head.peel().tree.id == source.tree.id
    if no_ff:
        assert fresh.head.peel().parent_ids == [before, source.id]
    else:
        assert fresh.head.target == source.id
    assert fresh.index.write_tree() == source.tree.id
    assert fresh.status() == {}
    assert (root / "hello.txt").read_text() == "feature version\n"
    assert (root / "added.txt").read_text() == "new file\n"
    assert not (root / "removed.txt").exists()
    assert not is_merge_in_progress(repo)


@pytest.mark.parametrize("local_change", ["staged", "unstaged", "untracked"])
def test_no_ff_refuses_overlapping_edits_before_creating_commit(
    fast_forward_repo: RepositoryManager, local_change: str,
) -> None:
    repo = fast_forward_repo
    root = Path(repo.path)
    path = "added.txt" if local_change == "untracked" else "hello.txt"
    (root / path).write_text("local work\n")
    if local_change == "staged":
        repo.repo.index.add(path)
        repo.repo.index.write()
    before = repo.repo.head.target
    index_before = repo.repo.index.write_tree()
    status_before = repo.repo.status()

    with pytest.raises(GitError):
        merge_branch(repo, "feature", no_ff=True)

    fresh = pygit2.Repository(repo.path)
    assert fresh.head.target == before
    assert fresh.index.write_tree() == index_before
    assert fresh.status() == status_before
    assert (root / path).read_text() == "local work\n"
    assert (root / "removed.txt").read_text() == "remove me\n"
    assert not is_merge_in_progress(repo)


@pytest.mark.parametrize("staged", [False, True])
def test_no_ff_preserves_unrelated_local_edits(
    fast_forward_repo: RepositoryManager, staged: bool,
) -> None:
    repo = fast_forward_repo
    root = Path(repo.path)
    (root / "notes.txt").write_text("local notes\n")
    if staged:
        repo.repo.index.add("notes.txt")
        repo.repo.index.write()
    source = repo.repo.revparse_single("feature")

    assert merge_branch(repo, "feature", no_ff=True) is True

    fresh = pygit2.Repository(repo.path)
    assert fresh.head.peel().tree.id == source.tree.id
    expected_status = (
        pygit2.GIT_STATUS_INDEX_MODIFIED if staged else pygit2.GIT_STATUS_WT_MODIFIED
    )
    assert fresh.status() == {"notes.txt": expected_status}
    assert (root / "notes.txt").read_text() == "local notes\n"
    assert (root / "hello.txt").read_text() == "feature version\n"
