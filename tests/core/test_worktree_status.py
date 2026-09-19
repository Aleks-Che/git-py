"""Snapshots preserve the index, including partial staging and selected versions."""
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError
from src.core.worktree_status import read_worktree_status


def test_worktree_snapshot_preserves_staged_content(committed_repo):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_bytes(b"staged\n")
    index = committed_repo.repo.index
    index.add("hello.txt")
    index.write()
    before = (Path(committed_repo.repo.path) / "index").read_bytes()
    first = read_worktree_status(committed_repo, "hello.txt", False)
    staged = read_worktree_status(committed_repo, "hello.txt", True)
    (root / "hello.txt").write_bytes(b"new save\n")
    second = read_worktree_status(committed_repo, "hello.txt", False)
    assert second.raw_status["hello.txt"] == (
        pygit2.GIT_STATUS_INDEX_MODIFIED | pygit2.GIT_STATUS_WT_MODIFIED
    )
    assert first.selected_version != second.selected_version
    assert staged.selected_version == read_worktree_status(
        committed_repo, "hello.txt", True,
    ).selected_version
    assert (Path(committed_repo.repo.path) / "index").read_bytes() == before
    (root / "hello.txt").unlink()
    deleted = read_worktree_status(committed_repo, "hello.txt", False)
    assert deleted.raw_status["hello.txt"] & pygit2.GIT_STATUS_WT_DELETED


def test_missing_index_reports_error_instead_of_an_empty_status(committed_repo):
    (Path(committed_repo.repo.path) / "index").unlink()
    with pytest.raises(GitError, match="index does not exist"):
        read_worktree_status(committed_repo, None, False)
