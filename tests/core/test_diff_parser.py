"""Tests for :mod:`src.core.diff_parser`.

Each test uses the ``committed_repo`` fixture to materialise a real
``pygit2.Diff`` (between the two initial commits or between a tree and
the index/worktree) and feeds it to ``parse_diff`` / ``diff_to_text``.
"""
from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from src.core.diff_parser import diff_to_text, parse_diff
from src.core.exceptions import GitError
from src.core.models import FileStatus
from src.core.repository import RepositoryManager


def test_parse_diff_rejects_non_pygit2_input() -> None:
    with pytest.raises(GitError):
        parse_diff("not a diff")  # type: ignore[arg-type]


def test_diff_to_text_rejects_non_pygit2_input() -> None:
    with pytest.raises(GitError):
        diff_to_text(object())  # type: ignore[arg-type]


def test_parse_diff_between_two_commits(committed_repo: RepositoryManager) -> None:
    parent_sha = committed_repo.head_commit.parents[0]
    diff = committed_repo.repo.diff(
        pygit2.Oid(bytes.fromhex(parent_sha)),
        committed_repo.head_commit.sha,
    )
    entries = parse_diff(diff, repo=committed_repo.repo)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.old_path == "hello.txt"
    assert entry.new_path == "hello.txt"
    assert entry.status == FileStatus.MODIFIED
    assert entry.is_binary is False
    # The diff text for a one-line change contains a + and a -.
    assert entry.raw_patch is not None
    assert "hello" in entry.raw_patch


def test_diff_to_text_matches_pygit2_patch(committed_repo: RepositoryManager) -> None:
    parent_sha = committed_repo.head_commit.parents[0]
    diff = committed_repo.repo.diff(
        pygit2.Oid(bytes.fromhex(parent_sha)),
        committed_repo.head_commit.sha,
    )
    assert diff_to_text(diff) == diff.patch
    assert "diff --git" in diff_to_text(diff)


def test_parse_diff_handles_added_file(tmp_git_repo: Path) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    (tmp_git_repo / "fresh.txt").write_text("new\n")
    mgr.repo.index.add("fresh.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    # The SHA of the empty tree is a well-known constant in Git.
    empty_tree = pygit2.Oid(bytes.fromhex("4b825dc642cb6eb9a060e54bf8d69288fbee4904"))
    diff = mgr.repo.diff(empty_tree, tree)
    entries = parse_diff(diff, repo=mgr.repo)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.status == FileStatus.NEW
    assert entry.new_path == "fresh.txt"
    assert entry.old_path is None
    assert entry.additions == 1
    assert entry.deletions == 0


def test_parse_diff_handles_deleted_file(committed_repo: RepositoryManager) -> None:
    assert committed_repo.path is not None
    (Path(committed_repo.path) / "hello.txt").unlink()
    committed_repo.repo.index.remove("hello.txt")
    committed_repo.repo.index.write()
    tree = committed_repo.repo.index.write_tree()
    parent_sha = committed_repo.head_commit.sha
    diff = committed_repo.repo.diff(pygit2.Oid(bytes.fromhex(parent_sha)), tree)
    entries = parse_diff(diff, repo=committed_repo.repo)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.status == FileStatus.DELETED
    assert entry.old_path == "hello.txt"
    assert entry.additions == 0
    assert entry.deletions >= 1
