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
    assert 'Moved: "hello.txt" -> "renamed.txt" (content unchanged)' in snapshot.diff
    assert 'Added: "data.bin"' in snapshot.diff
    assert "Binary files" not in snapshot.diff
    assert "diff --git" not in snapshot.diff
    repo.index.remove("renamed.txt")
    repo.index.write()
    snapshot = read_staged_snapshot(str(root), 10_000)
    assert 'Deleted: "hello.txt"' in snapshot.diff
    assert "hello, world" not in snapshot.diff


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


def test_oversized_patch_is_explicitly_omitted_but_file_is_kept(committed_repo):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("lots of new content\n" * 100)
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    snapshot = read_staged_snapshot(str(root), 100)
    assert len(snapshot.diff) <= 100
    assert 'Modified: "hello.txt"' in snapshot.diff
    assert "Patch omitted: context size limit" in snapshot.diff
    assert "lots of new content" not in snapshot.diff


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


def _commit_index(repo):
    signature = pygit2.Signature("tester", "tester@example.com")
    return repo.create_commit(
        "HEAD", signature, signature, "fixture", repo.index.write_tree(),
        [] if repo.head_is_unborn else [repo.head.target],
    )


def _stage_blob(repo, path, data, mode=pygit2.GIT_FILEMODE_BLOB):
    repo.index.add(pygit2.IndexEntry(path, repo.create_blob(data), mode))


def test_mass_moves_ignore_rename_config_and_do_not_send_file_bodies(tmp_git_repo):
    repo = RepositoryManager(str(tmp_git_repo)).repo
    entries = []
    for number in range(1100):
        path = f"old/file-{number:04}.txt"
        data = f"file {number}\n".encode() + b"large unchanged file body\n" * 100
        _stage_blob(repo, path, data)
        entries.append(repo.index[path])
    repo.index.write()
    _commit_index(repo)
    for entry in entries:
        repo.index.remove(entry.path)
        repo.index.add(pygit2.IndexEntry(entry.path.replace("old/", "new/"), entry.id, entry.mode))
    repo.index.write()
    repo.config["diff.renames"] = False
    repo.config["diff.renameLimit"] = 1
    index_before = (Path(repo.path) / "index").read_bytes()
    head_before = repo.head.target
    snapshot = read_staged_snapshot(str(tmp_git_repo), 120_000)
    assert snapshot.diff.count("Moved:") == len(entries)
    assert "large unchanged file body" not in snapshot.diff
    assert "diff --git" not in snapshot.diff
    for entry in entries:
        assert f'"{entry.path}" -> "{entry.path.replace("old/", "new/")}"' in snapshot.diff
    assert len(snapshot.diff) < 120_000
    assert (Path(repo.path) / "index").read_bytes() == index_before
    assert repo.head.target == head_before


def test_large_deleted_file_is_metadata_only(committed_repo):
    repo = committed_repo.repo
    _stage_blob(repo, "obsolete.txt", b"deleted body must not reach LLM\n" * 20_000)
    repo.index.write()
    _commit_index(repo)
    repo.index.remove("obsolete.txt")
    repo.index.write()
    snapshot = read_staged_snapshot(committed_repo.path, 200)
    assert 'Deleted: "obsolete.txt"' in snapshot.diff
    assert "deleted body" not in snapshot.diff
    assert len(snapshot.diff) < 200


def test_move_with_edits_keeps_only_actual_staged_edits(committed_repo):
    repo = committed_repo.repo
    original = "".join(f"unchanged line {i}\n" for i in range(200))
    _stage_blob(repo, "before.txt", original.encode())
    repo.index.write()
    _commit_index(repo)
    repo.index.remove("before.txt")
    edited = original.replace("unchanged line 100", "staged improvement")
    _stage_blob(repo, "after.txt", edited.encode())
    repo.index.write()
    Path(committed_repo.path, "after.txt").write_text("unstaged must not be sent\n")
    snapshot = read_staged_snapshot(committed_repo.path, 1000)
    assert 'Moved: "before.txt" -> "after.txt" (content changed)' in snapshot.diff
    assert "+staged improvement" in snapshot.diff
    assert "-unchanged line 100" in snapshot.diff
    assert "unchanged line 0\n" not in snapshot.diff
    assert "unstaged must not be sent" not in snapshot.diff


@pytest.mark.parametrize("initial", [False, True])
@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("photo.PNG", b"not actually binary but still an image"),
        ("icon.svg", b'<svg><path d="huge vector data"/></svg>'),
        ("camera.raw", b"raw sensor values\n" * 100),
        ("document.pdf", b"%PDF-1.7\nstream of document data"),
        ("mesh.gltf", b'{"meshes": ["asset data"]}'),
        ("unknown.custom", b"opaque binary\x00\xff\x01"),
    ],
)
def test_added_assets_only_send_operation_and_filename(tmp_git_repo, initial, path, data):
    repo = RepositoryManager(str(tmp_git_repo)).repo
    if not initial:
        _stage_blob(repo, "tracked.txt", b"existing\n")
        repo.index.write()
        _commit_index(repo)
    _stage_blob(repo, path, data)
    repo.index.write()
    snapshot = read_staged_snapshot(str(tmp_git_repo), 200)
    assert snapshot.diff == f'Added: "{path}" (mode 100644)\n'


@pytest.mark.parametrize("path", ["icon.svg", "unknown.custom"])
def test_modified_assets_are_metadata_only(committed_repo, path):
    repo = committed_repo.repo
    _stage_blob(repo, path, b"old content" if path.endswith("svg") else b"old\x00data")
    repo.index.write()
    _commit_index(repo)
    _stage_blob(repo, path, b"new content" if path.endswith("svg") else b"new\x00data")
    repo.index.write()
    snapshot = read_staged_snapshot(committed_repo.path, 200)
    assert snapshot.diff == f'Modified: "{path}" (mode 100644 -> 100644)\n'


def test_budget_reserves_all_filenames_and_prioritizes_small_edits(committed_repo):
    repo = committed_repo.repo
    _stage_blob(repo, "a-large-new.txt", b"lots of new content\n" * 10_000)
    _stage_blob(repo, "hello.txt", b"small meaningful edit\n")
    _stage_blob(repo, "z-small-new.txt", b"new setting\n")
    repo.index.write()
    snapshot = read_staged_snapshot(committed_repo.path, 800)
    assert len(snapshot.diff) <= 800
    assert 'Added: "a-large-new.txt"' in snapshot.diff
    assert "Patch omitted: context size limit" in snapshot.diff
    assert "+small meaningful edit" in snapshot.diff
    assert "+new setting" in snapshot.diff
    assert "lots of new content" not in snapshot.diff


def test_file_list_that_cannot_fit_is_rejected_instead_of_dropping_paths(committed_repo):
    repo = committed_repo.repo
    repo.index.remove("hello.txt")
    repo.index.write()
    with pytest.raises(GitError, match="file list is too large"):
        read_staged_snapshot(committed_repo.path, 10)


def test_mode_change_survives_compaction(committed_repo):
    repo = committed_repo.repo
    original = repo.index["hello.txt"]
    repo.index.add(
        pygit2.IndexEntry(original.path, original.id, pygit2.GIT_FILEMODE_BLOB_EXECUTABLE)
    )
    repo.index.write()
    snapshot = read_staged_snapshot(committed_repo.path, 100)
    assert "mode 100644 -> 100755" in snapshot.diff
    assert "hello, world" not in snapshot.diff


def test_identical_content_with_multiple_paths_does_not_hide_additions(committed_repo):
    repo = committed_repo.repo
    entry = repo.index["hello.txt"]
    repo.index.remove("hello.txt")
    for path in ["copy-a.txt", "copy-b.txt"]:
        repo.index.add(pygit2.IndexEntry(path, entry.id, entry.mode))
    repo.index.write()
    snapshot = read_staged_snapshot(committed_repo.path, 1000)
    assert snapshot.diff.count("Moved:") == 1
    assert snapshot.diff.count("Added:") == 1
    assert "copy-a.txt" in snapshot.diff and "copy-b.txt" in snapshot.diff
