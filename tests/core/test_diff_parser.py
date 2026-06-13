"""Tests for :mod:`src.core.diff_parser`.

Each test uses the ``committed_repo`` fixture to materialise a real
``pygit2.Diff`` (between the two initial commits or between a tree and
the index/worktree) and feeds it to ``parse_diff`` / ``diff_to_text``.
"""
from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from src.core.diff_parser import (
    DiffLineType,
    diff_to_text,
    parse_diff,
    parse_diff_lines,
)
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


# ----- parse_diff_lines: classification & line numbers ----------------


def test_parse_diff_lines_empty_text_returns_empty_list() -> None:
    assert parse_diff_lines("") == []


def test_parse_diff_lines_classifies_header_hunk_and_change_lines() -> None:
    text = (
        "diff --git a/foo b/foo\n"
        "index 1234..5678 100644\n"
        "--- a/foo\n"
        "+++ b/foo\n"
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        "-line2\n"
        "+line2-new\n"
        "+line2-extra\n"
        " line3\n"
    )
    parsed = parse_diff_lines(text)
    types = [p.line_type for p in parsed]
    assert types == [
        DiffLineType.HEADER,
        DiffLineType.HEADER,
        DiffLineType.HEADER,
        DiffLineType.HEADER,
        DiffLineType.HUNK,
        DiffLineType.CONTEXT,
        DiffLineType.DELETION,
        DiffLineType.ADDITION,
        DiffLineType.ADDITION,
        DiffLineType.CONTEXT,
    ]


def test_parse_diff_lines_assigns_file_line_numbers() -> None:
    """Additions and context use the new-file counter, deletions the old one."""
    text = (
        "@@ -10,3 +10,4 @@\n"
        " keep-a\n"
        "-keep-b\n"
        "+keep-b-new\n"
        "+inserted\n"
        " keep-c\n"
    )
    parsed = parse_diff_lines(text)
    # Trace: old=10, new=10 (hunk). " keep-a" consumes both → old=11, new=11.
    # "-keep-b" consumes old only → old=12, reports old=11.
    # "+keep-b-new" consumes new only → new=12, reports new=11.
    # "+inserted" → new=13, reports new=12.
    # " keep-c" reports new=13 (the new-file line it occupies).
    assert [(p.line_type, p.line_number) for p in parsed] == [
        (DiffLineType.HUNK, 10),       # hunk header → new_start
        (DiffLineType.CONTEXT, 10),    # new:10 (old:10)
        (DiffLineType.DELETION, 11),   # old:11
        (DiffLineType.ADDITION, 11),   # new:11
        (DiffLineType.ADDITION, 12),   # new:12
        (DiffLineType.CONTEXT, 13),    # new:13
    ]


def test_parse_diff_lines_resets_counters_per_hunk() -> None:
    text = (
        "@@ -1,2 +1,2 @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        "@@ -50,2 +50,2 @@\n"
        " x\n"
        "-y\n"
        "+Y\n"
    )
    parsed = parse_diff_lines(text)
    # First hunk: old=1, new=1. " a" → ctx, new=1, then new=2/old=2.
    # "-b" → del, old=2. "+B" → add, new=2.
    # Second hunk resets: old=50, new=50. " x" → ctx, new=50, then new=51/old=51.
    # "-y" → del, old=51. "+Y" → add, new=51.
    assert [p.line_number for p in parsed] == [
        1,   # hunk 1 header (new_start=1)
        1,   # a (context)
        2,   # b (deletion, old=2)
        2,   # B (addition, new=2)
        50,  # hunk 2 header (new_start=50) — counters reset
        50,  # x (context)
        51,  # y (deletion, old=51)
        51,  # Y (addition, new=51)
    ]


def test_parse_diff_lines_handles_added_file_hunk() -> None:
    """``@@ -0,0 +1,3 @@`` has no old file — the new counter starts at 1."""
    text = (
        "@@ -0,0 +1,3 @@\n"
        "+line1\n"
        "+line2\n"
        "+line3\n"
    )
    parsed = parse_diff_lines(text)
    assert [p.line_number for p in parsed] == [1, 1, 2, 3]


def test_parse_diff_lines_handles_deleted_file_hunk() -> None:
    """``@@ -1,3 +0,0 @@`` has no new file — the old counter starts at 1."""
    text = (
        "@@ -1,3 +0,0 @@\n"
        "-line1\n"
        "-line2\n"
        "-line3\n"
    )
    parsed = parse_diff_lines(text)
    assert [p.line_number for p in parsed] == [1, 1, 2, 3]


def test_parse_diff_lines_hunk_without_count_field() -> None:
    """Some producers emit ``@@ -1 +1 @@`` (count of 1 implied)."""
    text = "@@ -1 +1 @@\n a\n-b\n+B\n"
    parsed = parse_diff_lines(text)
    # old=1, new=1. " a" → ctx, new=1, then new=2/old=2.
    # "-b" → del, old=2. "+B" → add, new=2.
    assert [p.line_number for p in parsed] == [1, 1, 2, 2]


def test_parse_diff_lines_header_lines_have_none_line_number() -> None:
    parsed = parse_diff_lines(
        "diff --git a/foo b/foo\n"
        "index 1234..5678 100644\n"
        "--- a/foo\n"
        "+++ b/foo\n"
    )
    assert all(p.line_number is None for p in parsed)
    assert all(p.line_type == DiffLineType.HEADER for p in parsed)


def test_parse_diff_lines_orphan_lines_before_any_hunk_have_none() -> None:
    """A malformed / truncated diff may emit lines without a hunk context."""
    parsed = parse_diff_lines("+stray\n-stray2\n")
    assert [p.line_type for p in parsed] == [DiffLineType.ADDITION, DiffLineType.DELETION]
    assert all(p.line_number is None for p in parsed)


def test_parse_diff_lines_empty_marker_does_not_advance_counters() -> None:
    text = (
        "@@ -1,2 +1,2 @@\n"
        " keep\n"
        "-old\n"
        "\\ No newline at end of file\n"
        "+new\n"
        "\\ No newline at end of file\n"
        " keep2\n"
    )
    parsed = parse_diff_lines(text)
    assert [p.line_type for p in parsed] == [
        DiffLineType.HUNK,
        DiffLineType.CONTEXT,
        DiffLineType.DELETION,
        DiffLineType.EMPTY,
        DiffLineType.ADDITION,
        DiffLineType.EMPTY,
        DiffLineType.CONTEXT,
    ]
    # The ``\ No newline`` markers don't consume line numbers — the
    # trailing context still maps to new line 3 (context + deletion
    # + addition all advanced the new counter).
    assert [p.line_number for p in parsed] == [1, 1, 2, None, 2, None, 3]


def test_parse_diff_lines_multiple_files_reset_counters() -> None:
    text = (
        "diff --git a/a.txt b/a.txt\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "-x\n"
        "+X\n"
        "diff --git a/b.txt b/b.txt\n"
        "--- a/b.txt\n"
        "+++ b/b.txt\n"
        "@@ -100,1 +100,1 @@\n"
        "-y\n"
        "+Y\n"
    )
    parsed = parse_diff_lines(text)
    # Find the two file headers' @@ lines and the surrounding changes.
    add_lines = [p for p in parsed if p.line_type == DiffLineType.ADDITION]
    assert [p.line_number for p in add_lines] == [1, 100]


def test_parse_diff_lines_malformed_hunk_falls_back_safely() -> None:
    """A bad hunk header shouldn't crash — counters default to 1."""
    parsed = parse_diff_lines("@@ not a hunk @@\n+a\n+b\n")
    assert parsed[0].line_type == DiffLineType.HUNK
    assert parsed[1].line_type == DiffLineType.ADDITION
    assert parsed[1].line_number == 1
    assert parsed[2].line_number == 2


def test_parse_diff_lines_preserves_leading_space_on_context() -> None:
    parsed = parse_diff_lines("@@ -1,1 +1,1 @@\n hello\n")
    assert parsed[1].text == " hello"
    assert parsed[1].line_type == DiffLineType.CONTEXT

