"""Ignore rules must take effect on an already-open repository."""
from pathlib import Path

import pytest
from src.core.operations import add_to_gitignore


@pytest.mark.parametrize("original", [None, b"", b"*.log", b"*.log\n", b"*.log\r\n"])
def test_ignore_directory_refreshes_status_without_reopening(committed_repo, original):
    root = Path(committed_repo.path)
    gitignore = root / ".gitignore"
    if original is not None:
        gitignore.write_bytes(original)
    files = {"cache/first.txt", "cache/nested/second.txt", "cache-other/keep.txt"}
    for path in files:
        full_path = root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(b"untracked\n")
    assert files <= committed_repo.repo.status().keys()

    add_to_gitignore(committed_repo, "cache/")

    assert set(committed_repo.repo.status()) == {".gitignore", "cache-other/keep.txt"}
    assert gitignore.read_text(encoding="utf-8").splitlines() == (
        (["*.log"] if original else []) + ["cache/"]
    )
    assert all((root / path).read_bytes() == b"untracked\n" for path in files)
    written = gitignore.read_bytes()
    add_to_gitignore(committed_repo, "cache/")
    assert gitignore.read_bytes() == written
