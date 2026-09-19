"""Stash creation and worktree cleanup are separate failure boundaries."""
from __future__ import annotations

import os
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import StashSaveError
from src.core.operations import _file_lock_details, stash_push


@pytest.mark.parametrize("reason", [
    "The process cannot access the file because it is being used by another process.",
    "The process cannot access the file because another process has locked a portion of the file.",
    "Процесс не может получить доступ к файлу, так как этот файл занят другим процессом.",
])
def test_lock_diagnostic_keeps_directory_and_apostrophe_in_path(reason):
    path = "C:/O'Brien/проект/scripts/"
    assert _file_lock_details(f"could not rmdir '{path}': {reason}") == (path, True, True)


@pytest.mark.parametrize("reason", ["Access is denied.", "Permission denied", "Disk full"])
def test_permission_and_io_errors_are_not_described_as_file_locks(reason):
    assert _file_lock_details(f"could not remove 'file.txt': {reason}") == (
        "file.txt", False, False,
    )


def test_existing_stash_is_not_reported_as_created_on_failure(committed_repo, monkeypatch):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("older change\n")
    older = stash_push(committed_repo, "older")
    (root / "hello.txt").write_text("new change\n")

    def fail(*args, **kwargs):
        raise pygit2.GitError("could not open 'hello.txt': Access is denied.")

    monkeypatch.setattr(pygit2.Repository, "stash", fail)
    with pytest.raises(StashSaveError) as raised:
        stash_push(committed_repo)
    error = raised.value
    assert error.saved_oid is None
    assert not error.is_locked
    assert "сохранён" not in str(error)
    assert error.__cause__ is not None
    assert [entry.sha for entry in committed_repo.stash_list] == [older]
    assert (root / "hello.txt").read_text() == "new change\n"


def test_saved_stash_is_reported_even_for_non_lock_cleanup_error(committed_repo, monkeypatch):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("saved change\n")
    original_stash = pygit2.Repository.stash

    def fail_after_save(repo, signature, message, **kwargs):
        original_stash(repo, signature, message, keep_all=True, **kwargs)
        raise pygit2.GitError("could not remove 'hello.txt': Permission denied")

    monkeypatch.setattr(pygit2.Repository, "stash", fail_after_save)
    with pytest.raises(StashSaveError) as raised:
        stash_push(committed_repo)
    error = raised.value
    assert error.saved_oid == committed_repo.stash_list[0].sha
    assert error.saved_oid[:12] in str(error)
    assert not error.is_locked
    assert "Permission denied" in str(error)
    assert "очистка рабочей папки не завершена" in str(error)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows sharing violations")
@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_real_windows_lock_after_stash_save(committed_repo, directory):
    import ctypes

    root = Path(committed_repo.path)
    tracked = root / "hello.txt"
    tracked.write_bytes(b"keep my changes\n")
    locked_path = root / "locked"
    if directory:
        locked_path.mkdir()
        (locked_path / "untracked.txt").write_bytes(b"new file\n")
    else:
        locked_path.write_bytes(b"new file\n")
    repo = committed_repo.repo
    old_head = repo.head.target
    old_index = (Path(repo.path) / "index").read_bytes()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # Allow read/write sharing, but deny delete sharing. BACKUP_SEMANTICS
    # permits opening a directory; no file or process is modified by the lock.
    handle = kernel.CreateFileW(str(locked_path), 0x80000000, 3, None, 3, 0x02000000, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        with pytest.raises(StashSaveError) as raised:
            stash_push(committed_repo)
        error = raised.value
        assert error.is_locked
        assert Path(error.blocked_path) == locked_path
        assert ("Папка занята" if directory else "Файл занят") in str(error)
        assert error.saved_oid == committed_repo.stash_list[0].sha
        assert "could not" not in str(error)
        assert "could not" in error.details
        assert repo.head.target == old_head
        assert (Path(repo.path) / "index").read_bytes() == old_index
        assert tracked.read_text() == "keep my changes\n"
        saved = repo[pygit2.Oid(hex=error.saved_oid)]
        assert repo[saved.tree["hello.txt"].id].data == b"keep my changes\n"
        new_path = "locked/untracked.txt" if directory else "locked"
        assert repo[saved.parents[2].tree[new_path].id].data == b"new file\n"
    finally:
        kernel.CloseHandle(handle)
