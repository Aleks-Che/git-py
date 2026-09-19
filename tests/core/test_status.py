"""The non-blocking status reader must preserve Git/index semantics."""
import os
import subprocess
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError, GitNotInstalledError
from src.core.status import _parse_status


@pytest.mark.parametrize("xy, expected", [
    ("??", pygit2.GIT_STATUS_WT_NEW),
    (" M", pygit2.GIT_STATUS_WT_MODIFIED),
    (" D", pygit2.GIT_STATUS_WT_DELETED),
    (" T", pygit2.GIT_STATUS_WT_TYPECHANGE),
    ("A ", pygit2.GIT_STATUS_INDEX_NEW),
    ("D ", pygit2.GIT_STATUS_INDEX_DELETED),
    ("T ", pygit2.GIT_STATUS_INDEX_TYPECHANGE),
    ("MM", pygit2.GIT_STATUS_INDEX_MODIFIED | pygit2.GIT_STATUS_WT_MODIFIED),
    *[(xy, pygit2.GIT_STATUS_CONFLICTED) for xy in ("DD", "AU", "UD", "UA", "DU", "AA", "UU")],
])
def test_status_codes_preserve_staging_and_conflicts(xy, expected):
    assert _parse_status(f"{xy} file\0".encode()) == {"file": expected}


def test_paths_are_not_split_on_whitespace_quotes_or_arrows():
    names = [" space.txt", "строка\nс табом\t.txt", 'quote".txt', "a -> b.txt"]
    data = b"".join(f"?? {name}\0".encode() for name in names)
    assert _parse_status(data) == dict.fromkeys(names, pygit2.GIT_STATUS_WT_NEW)


@pytest.mark.parametrize("data", [b"?? missing terminator", b"??\0", b"ZZ bad\0", b"??!bad\0"])
def test_invalid_output_is_not_presented_as_a_clean_checkout(data):
    with pytest.raises(GitError):
        _parse_status(data)


@pytest.mark.parametrize("state", ["clean", "partial", "rename", "conflict", "untracked"])
def test_reader_matches_pygit2_and_never_rewrites_index(committed_repo, state):
    repo = committed_repo.repo
    root = Path(repo.workdir)
    index = repo.index
    if state == "partial":
        (root / "hello.txt").write_bytes(b"staged\n")
        index.add("hello.txt")
        index.write()
        (root / "hello.txt").write_bytes(b"unstaged\n")
    elif state == "rename":
        (root / "hello.txt").rename(root / "renamed файл.txt")
        index.remove("hello.txt")
        index.add("renamed файл.txt")
        index.write()
        # User settings must not enable expensive rename/copy detection.
        repo.config["status.renames"] = "copies"
    elif state == "conflict":
        entry = index["hello.txt"]
        index.add_conflict(entry, entry, entry)
        index.write()
    elif state == "untracked":
        (root / "nested").mkdir()
        (root / "nested" / "файл с пробелами.txt").write_bytes(b"new\n")
        (root / ".git" / "info" / "exclude").write_text("ignored.txt\n")
        (root / "ignored.txt").write_bytes(b"ignored\n")
        repo.config["status.showUntrackedFiles"] = "no"
    before = (Path(repo.path) / "index").read_bytes()
    assert committed_repo.get_raw_status() == dict(repo.status())
    assert (Path(repo.path) / "index").read_bytes() == before


def test_reader_uses_linked_index_despite_inherited_git_environment(
    committed_repo, linked_worktree, monkeypatch,
):
    (Path(linked_worktree.path) / "linked-only.txt").write_bytes(b"new\n")
    monkeypatch.setenv("GIT_DIR", committed_repo.repo.path)
    monkeypatch.setenv("GIT_WORK_TREE", committed_repo.path)
    monkeypatch.setenv("GIT_INDEX_FILE", str(Path(committed_repo.repo.path) / "index"))
    assert linked_worktree.get_raw_status() == {"linked-only.txt": pygit2.GIT_STATUS_WT_NEW}
    assert committed_repo.get_raw_status() == {}


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction regression")
def test_ignored_junction_does_not_create_false_wip(committed_repo, tmp_path):
    import _winapi

    target = tmp_path / "dependencies"
    target.mkdir()
    (target / "keep.txt").write_bytes(b"dependency\n")
    link = Path(committed_repo.path) / "node_modules"
    exclude = Path(committed_repo.repo.path) / "info" / "exclude"
    exclude.write_text("node_modules/\n")
    _winapi.CreateJunction(str(target), str(link))
    try:
        assert committed_repo.get_raw_status() == {}
        assert (target / "keep.txt").read_bytes() == b"dependency\n"
    finally:
        link.rmdir()


def test_reader_disables_index_writes_and_console_windows(committed_repo, monkeypatch):
    original = subprocess.run
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return original(args, **kwargs)

    monkeypatch.setattr("src.core.status.subprocess.run", run)
    assert committed_repo.get_raw_status() == {}
    args, kwargs = calls[0]
    assert "--no-optional-locks" in args
    assert "--no-renames" in args
    assert "--untracked-files=all" in args
    assert kwargs["timeout"] > 0
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


@pytest.mark.parametrize("failure, expected", [
    (FileNotFoundError("git missing"), GitNotInstalledError),
    (OSError("access denied"), GitError),
    (subprocess.TimeoutExpired("git", 30), GitError),
    (subprocess.CompletedProcess([], 128, b"", b"bad index"), GitError),
])
def test_failures_surface_without_falling_back_to_blocking_status(
    committed_repo, monkeypatch, failure, expected,
):
    def run(*args, **kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr("src.core.status.subprocess.run", run)
    with pytest.raises(expected):
        committed_repo.get_raw_status()
