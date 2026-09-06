"""Staged snapshots must match the index, including partial staging and first commits."""

from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError
from src.core.repository import RepositoryManager
from src.core.staged_diff import read_staged_snapshot, staged_identity


def test_snapshot_excludes_unstaged_and_untracked_content(committed_repo):
    repo = committed_repo.repo
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("staged content\n")
    repo.index.add("hello.txt")
    repo.index.write()
    (root / "hello.txt").write_text("unstaged content must not be sent\n")
    (root / "private.txt").write_text("untracked must not be sent\n")
    index_before = (Path(repo.path) / "index").read_bytes()
    head_before = repo.head.target
    snapshot = read_staged_snapshot(str(root), 10_000)
    assert "+staged content" in snapshot.diff
    assert "-hello, world" in snapshot.diff
    assert "unstaged content" not in snapshot.diff
    assert "private.txt" not in snapshot.diff
    assert snapshot.branch == "main"
    assert (Path(repo.path) / "index").read_bytes() == index_before
    assert repo.head.target == head_before


def test_initial_commit_uses_index_blobs(tmp_git_repo):
    repo = RepositoryManager(str(tmp_git_repo)).repo
    (tmp_git_repo / "new.txt").write_text("staged new file\n")
    repo.index.add("new.txt")
    repo.index.write()
    (tmp_git_repo / "new.txt").write_text("later edit\n")
    snapshot = read_staged_snapshot(str(tmp_git_repo), 10_000)
    assert "+staged new file" in snapshot.diff
    assert "later edit" not in snapshot.diff
    assert "new file mode 100644" in snapshot.diff
    assert snapshot.branch == "main"
    assert repo.head_is_unborn


def test_snapshot_keeps_deleted_renamed_and_binary_metadata(committed_repo):
    root = Path(committed_repo.path)
    repo = committed_repo.repo
    (root / "hello.txt").rename(root / "renamed.txt")
    repo.index.remove("hello.txt")
    repo.index.add("renamed.txt")
    (root / "data.bin").write_bytes(b"\x00\xff\x01")
    repo.index.add("data.bin")
    repo.index.write()
    snapshot = read_staged_snapshot(str(root), 10_000)
    assert "rename from hello.txt" in snapshot.diff
    assert "rename to renamed.txt" in snapshot.diff
    assert "Binary files" in snapshot.diff
    assert "data.bin" in snapshot.diff
    repo.index.remove("renamed.txt")
    repo.index.write()
    snapshot = read_staged_snapshot(str(root), 10_000)
    assert "deleted file mode" in snapshot.diff
    assert "-hello, world" in snapshot.diff


def test_identity_detects_same_path_new_content_and_same_oid_other_branch(committed_repo):
    root = Path(committed_repo.path)
    repo = committed_repo.repo
    before = staged_identity(str(root))
    (root / "hello.txt").write_text("new content\n")
    # Unstaged edits must not invalidate the staged snapshot.
    assert staged_identity(str(root)) == before
    repo.index.add("hello.txt")
    repo.index.write()
    staged = staged_identity(str(root))
    assert staged != before
    repo.branches.local.create("feature/test", repo[repo.head.target])
    repo.set_head("refs/heads/feature/test")
    assert staged_identity(str(root)) != staged


def test_oversized_diff_is_rejected_instead_of_silently_truncated(committed_repo):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("lots of new content\n" * 100)
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    with pytest.raises(GitError, match="too large"):
        read_staged_snapshot(str(root), 100)


def test_no_staged_changes_rejected(committed_repo):
    with pytest.raises(GitError, match="Stage some changes"):
        read_staged_snapshot(committed_repo.path, 10_000)


def test_conflicts_rejected(committed_repo):
    repo = committed_repo.repo
    oid = repo.create_blob(b"conflict\n")
    entry = pygit2.IndexEntry("hello.txt", oid, pygit2.GIT_FILEMODE_BLOB)
    repo.index.add_conflict(entry, entry, entry)
    repo.index.write()
    with pytest.raises(GitError, match="Resolve staged conflicts"):
        read_staged_snapshot(committed_repo.path, 10_000)
