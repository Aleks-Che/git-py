"""Database handles must not survive explicit release or manager replacement."""
from pathlib import Path

import pytest
from src.core.exceptions import GitError
from src.core.staged_diff import read_staged_snapshot, staged_identity


def _rename_packs(path: str) -> None:
    packs = list((Path(path) / ".git" / "objects" / "pack").glob("*.pack"))
    assert packs
    for pack in packs:
        moved = pack.with_suffix(".probe")
        pack.rename(moved)
        moved.rename(pack)


def test_release_keeps_repository_readable_and_preserves_index(packed_repo):
    manager = packed_repo
    history = manager.get_all_history()
    raw_repo = manager.repo
    index = raw_repo.index
    entries = [(entry.path, str(entry.id)) for entry in index]
    manager.release_handles()
    _rename_packs(manager.path)
    assert manager.repo is raw_repo
    assert [(entry.path, str(entry.id)) for entry in index] == entries
    assert manager.get_all_history() == history
    manager.release_handles()
    _rename_packs(manager.path)


@pytest.mark.parametrize("action", ["close", "open", "init", "clone"])
def test_lifecycle_releases_packs_even_with_retained_raw_repo(packed_repo, tmp_path, action):
    manager = packed_repo
    old_path = manager.path
    raw_repo = manager.repo  # Mimics a command/traceback retaining the native object.
    manager.get_all_history()
    if action == "close":
        manager.close()
        manager.close()
    elif action == "open":
        manager.open(old_path)
    elif action == "init":
        manager.init(str(tmp_path / "new"))
    else:
        manager.clone(old_path, str(tmp_path / "clone"))
        # Local clones can reuse the same native pack cache.
        manager.release_handles()
    _rename_packs(old_path)
    assert raw_repo is not None


def test_staged_read_error_releases_packs_with_retained_traceback(packed_repo):
    path = packed_repo.path
    assert staged_identity(path)
    _rename_packs(path)
    with pytest.raises(GitError, match="Stage some changes") as error:
        read_staged_snapshot(path, 2000)
    # The exception still owns the failed reader's stack and local repo/index.
    assert error.value.__traceback__ is not None
    _rename_packs(path)
