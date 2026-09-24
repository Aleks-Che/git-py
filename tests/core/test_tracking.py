"""Removing tracked caches must preserve files and unrelated staging."""
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError
from src.core.operations import commit_changes
from src.core.tracking import set_tracking, snapshot_tracking, tracked_ignored_paths


def _cache(repo, paths=("cache/first.pyc", "cache/second.pyc")):
    root = Path(repo.path)
    for path in paths:
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"committed cache")
        repo.repo.index.add(path)
    (root / ".gitignore").write_text("cache/\n", encoding="utf-8")
    repo.repo.index.add(".gitignore")
    repo.repo.index.write()
    commit_changes(repo, "track caches before ignoring", stage_all=False)
    return root


@pytest.mark.parametrize("linked", [False, True])
def test_stop_tracking_preserves_files_staging_and_undo_redo(
    committed_repo, linked_worktree, linked,
):
    repo = linked_worktree if linked else committed_repo
    root = _cache(repo)
    path = "cache/first.pyc"
    (root / path).write_bytes(b"regenerated cache")
    before = snapshot_tracking(repo, [path])
    other_index = (Path(committed_repo.repo.path) / "index").read_bytes()

    set_tracking(repo, before, tracked=False)

    assert path not in repo.repo.index
    assert repo.repo.status_file(path) & pygit2.GIT_STATUS_INDEX_DELETED
    assert not repo.repo.status_file(path) & pygit2.GIT_STATUS_WT_NEW
    assert (root / path).read_bytes() == b"regenerated cache"
    # A later file edit and unrelated staging must survive Undo/Redo.
    (root / path).write_bytes(b"later regenerated cache")
    (root / "hello.txt").write_bytes(b"unrelated staged change")
    external = pygit2.Repository(repo.path)
    external.index.add("hello.txt")
    external.index.write()
    other_oid = external.index["hello.txt"].id
    set_tracking(repo, before, tracked=True)
    assert str(repo.repo.index[path].id) == before.entries[0][1]
    assert repo.repo.index["hello.txt"].id == other_oid
    set_tracking(repo, before, tracked=False)
    assert repo.repo.index["hello.txt"].id == other_oid
    assert (root / path).read_bytes() == b"later regenerated cache"
    if linked:
        assert (Path(committed_repo.repo.path) / "index").read_bytes() == other_index
    commit_changes(repo, "stop tracking cache", stage_all=False)
    assert path not in repo.get_raw_status()
    assert repo.repo.path_is_ignored(path)
    assert (root / path).exists()


def test_eligibility_uses_nested_rules_negation_and_exclude(committed_repo):
    repo = committed_repo
    root = _cache(repo)
    (root / ".gitignore").write_text("", encoding="utf-8")
    (root / "cache/.gitignore").write_text("*.pyc\n!second.pyc\n", encoding="utf-8")
    (Path(repo.repo.path) / "info/exclude").write_text("hello.txt\n", encoding="utf-8")
    (root / "cache/untracked.pyc").write_bytes(b"ignored")
    assert tracked_ignored_paths(repo, [
        "cache/first.pyc", "cache/second.pyc", "hello.txt", "cache/untracked.pyc", "missing",
    ]) == {"cache/first.pyc", "hello.txt"}


@pytest.mark.parametrize("changed", ["rule", "index", "head", "branch", "partial_stage"])
def test_stop_tracking_rejects_stale_or_unsafe_batch_without_mutation(committed_repo, changed):
    repo = committed_repo
    root = _cache(repo)
    paths = ["cache/first.pyc", "cache/second.pyc"]
    snapshot = snapshot_tracking(repo, paths)
    if changed == "rule":
        (root / ".gitignore").write_text("cache/first.pyc\n", encoding="utf-8")
    elif changed in {"index", "partial_stage"}:
        (root / paths[1]).write_bytes(b"new staged content")
        external = pygit2.Repository(repo.path)
        external.index.add(paths[1])
        external.index.write()
        if changed == "partial_stage":
            snapshot = snapshot_tracking(repo, paths)
            (root / paths[1]).write_bytes(b"new worktree content")
    elif changed == "head":
        commit_changes(repo, "later commit", stage_all=False)
    else:
        repo.repo.create_branch("other", repo.repo.head.peel())
        repo.repo.set_head("refs/heads/other")
    index_file = Path(repo.repo.path) / "index"
    before = index_file.read_bytes()
    with pytest.raises(GitError):
        set_tracking(repo, snapshot, tracked=False)
    assert index_file.read_bytes() == before


@pytest.mark.parametrize("collision", ["same", "parent", "child", "head"])
def test_undo_rejects_new_index_entries_or_commit(committed_repo, collision):
    repo = committed_repo
    root = _cache(repo, paths=("cache/file",))
    snapshot = snapshot_tracking(repo, ["cache/file"])
    set_tracking(repo, snapshot, tracked=False)
    if collision == "head":
        commit_changes(repo, "commit removal", stage_all=False)
    else:
        path = {"same": "cache/file", "parent": "cache", "child": "cache/file/child"}[collision]
        oid = repo.repo.create_blob(b"external index content")
        repo.repo.index.add(pygit2.IndexEntry(path, oid, pygit2.GIT_FILEMODE_BLOB))
        repo.repo.index.write()
    index_file = Path(repo.repo.path) / "index"
    before = index_file.read_bytes()
    with pytest.raises(GitError):
        set_tracking(repo, snapshot, tracked=True)
    assert index_file.read_bytes() == before
    assert (root / "cache/file").read_bytes() == b"committed cache"


def test_new_ignored_file_in_unborn_repository_can_be_untracked(tmp_git_repo):
    from src.core.repository import RepositoryManager

    repo = RepositoryManager(str(tmp_git_repo))
    (tmp_git_repo / "cache.pyc").write_bytes(b"cache")
    repo.repo.index.add("cache.pyc")
    repo.repo.index.write()
    (tmp_git_repo / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    snapshot = snapshot_tracking(repo, ["cache.pyc"])
    set_tracking(repo, snapshot, tracked=False)
    assert "cache.pyc" not in repo.get_raw_status()
    set_tracking(repo, snapshot, tracked=True)
    assert "cache.pyc" in repo.repo.index
