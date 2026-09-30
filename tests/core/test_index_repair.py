"""Repair must clear phantom changes without silently staging genuine edits."""
import os
import subprocess
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError
from src.core.index_repair import repair_file_status


def _entries(manager):
    index = manager.repo.index
    index.read(force=True)
    return [(entry.path, str(entry.id), entry.mode) for entry in index]


@pytest.mark.parametrize("selected_staged", [False, True])
def test_repair_clears_stale_size_and_preserves_staged_content(
    committed_repo, make_stale_index, selected_staged,
):
    paths = make_stale_index(committed_repo)
    repo = committed_repo.repo
    root = Path(repo.workdir)
    if selected_staged:
        (root / paths[0]).write_bytes(b"staged\r\nversion\r\n")
        repo.index.add(paths[0])
        repo.index.write()
        (root / paths[0]).write_bytes(b"staged\nversion\n")
    (root / "hello.txt").write_bytes(b"staged changes\n")
    repo.index.add("hello.txt")
    repo.index.write()
    (root / "hello.txt").write_bytes(b"additional unstaged changes\n")
    assert all(committed_repo.get_raw_status()[p] & pygit2.GIT_STATUS_WT_MODIFIED for p in paths)
    assert all(repo[repo.index[p].id].data == (root / p).read_bytes() for p in paths)
    before = _entries(committed_repo)
    head = repo.head.target
    contents = {p: (root / p).read_bytes() for p in [*paths, "hello.txt"]}

    repair_file_status(committed_repo, paths)

    expected_dirty = {"hello.txt", paths[0]} if selected_staged else {"hello.txt"}
    assert set(committed_repo.get_raw_status()) == expected_dirty
    assert all(
        not committed_repo.get_raw_status().get(p, 0) & pygit2.GIT_STATUS_WT_MODIFIED
        for p in paths
    )
    assert _entries(committed_repo) == before
    assert repo.head.target == head
    assert all((root / p).read_bytes() == data for p, data in contents.items())
    assert not (Path(repo.path) / "index.lock").exists()
    assert not list(Path(repo.path).glob("git-py-repair-*"))


@pytest.mark.parametrize("state", ["modified", "deleted", "untracked", "partial", "conflict"])
def test_repair_rejects_real_changes_atomically(committed_repo, make_stale_index, state):
    paths = make_stale_index(committed_repo)
    repo = committed_repo.repo
    root = Path(repo.workdir)
    target = root / paths[1]
    if state == "modified":
        target.write_bytes(b"real edits\n")
    elif state == "deleted":
        target.unlink()
    elif state == "untracked":
        repo.index.remove(paths[1])
        repo.index.write()
    elif state == "partial":
        target.write_bytes(b"staged version\n")
        repo.index.add(paths[1])
        repo.index.write()
        target.write_bytes(b"another version\n")
    else:
        entry = repo.index[paths[1]]
        repo.index.add_conflict(entry, entry, entry)
        repo.index.write()
    before = (Path(repo.path) / "index").read_bytes()
    contents = target.read_bytes() if target.exists() else None
    with pytest.raises(GitError):
        repair_file_status(committed_repo, paths)
    assert (Path(repo.path) / "index").read_bytes() == before
    assert (target.read_bytes() if target.exists() else None) == contents
    assert not (Path(repo.path) / "index.lock").exists()


def test_repair_preserves_existing_lock(committed_repo, make_stale_index):
    paths = make_stale_index(committed_repo)
    lock = Path(committed_repo.repo.path) / "index.lock"
    lock.write_bytes(b"another operation")
    with pytest.raises(GitError, match="locked"):
        repair_file_status(committed_repo, paths)
    assert lock.read_bytes() == b"another operation"


def test_repair_rejects_changes_appearing_during_repair(
    committed_repo, make_stale_index, monkeypatch,
):
    from src.core import index_repair

    paths = make_stale_index(committed_repo)
    before = (Path(committed_repo.repo.path) / "index").read_bytes()
    original = index_repair._git

    def run(repo, index, args):
        if args[0] == "add":
            (Path(repo.workdir) / paths[0]).write_bytes(b"concurrent edit\n")
        return original(repo, index, args)

    monkeypatch.setattr(index_repair, "_git", run)
    with pytest.raises(GitError, match="real unstaged changes"):
        repair_file_status(committed_repo, paths)
    assert (Path(committed_repo.repo.path) / "index").read_bytes() == before
    assert (Path(committed_repo.path) / paths[0]).read_bytes() == b"concurrent edit\n"


def test_linked_worktree_ignores_inherited_index_and_pathspec_environment(
    committed_repo, linked_worktree, make_stale_index, monkeypatch,
):
    paths = make_stale_index(linked_worktree)
    main_index = Path(committed_repo.repo.path) / "index"
    before = main_index.read_bytes()
    monkeypatch.setenv("GIT_INDEX_FILE", str(main_index))
    monkeypatch.setenv("GIT_WORK_TREE", committed_repo.path)
    monkeypatch.setenv("GIT_DIR", committed_repo.repo.path)
    monkeypatch.setenv("GIT_GLOB_PATHSPECS", "1")
    repair_file_status(linked_worktree, paths)
    assert linked_worktree.get_raw_status() == {}
    assert main_index.read_bytes() == before


def test_repair_does_not_stage_intent_to_add_empty_file(committed_repo):
    (Path(committed_repo.path) / "empty.txt").write_bytes(b"")
    subprocess.run(
        ["git", "-C", committed_repo.path, "add", "-N", "--", "empty.txt"],
        check=True, capture_output=True,
    )
    index_path = Path(committed_repo.repo.path) / "index"
    before = index_path.read_bytes()
    with pytest.raises(GitError, match="real unstaged changes"):
        repair_file_status(committed_repo, ["empty.txt"])
    assert index_path.read_bytes() == before


@pytest.mark.parametrize("failure", [
    FileNotFoundError("git missing"),
    subprocess.TimeoutExpired("git", 30),
    OSError("access denied"),
    subprocess.CompletedProcess([], 128, b"", b"cannot read file"),
])
def test_failed_git_leaves_index_and_releases_lock(
    committed_repo, make_stale_index, monkeypatch, failure,
):
    paths = make_stale_index(committed_repo)
    index_path = Path(committed_repo.repo.path) / "index"
    before = index_path.read_bytes()

    def fail(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr("src.core.index_repair.subprocess.run", fail)
    with pytest.raises(GitError):
        repair_file_status(committed_repo, paths)
    assert index_path.read_bytes() == before
    assert not index_path.with_name("index.lock").exists()
    assert not list(index_path.parent.glob("git-py-repair-*"))


@pytest.mark.skipif(os.name == "nt", reason="Executable worktree mode requires POSIX")
def test_repair_does_not_stage_executable_bit(committed_repo, make_stale_index):
    paths = make_stale_index(committed_repo)
    committed_repo.repo.config["core.filemode"] = True
    target = Path(committed_repo.path) / paths[0]
    target.chmod(0o755)
    with pytest.raises(GitError, match="real unstaged changes"):
        repair_file_status(committed_repo, paths)
