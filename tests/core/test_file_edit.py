"""Complete file reads, format preservation, and non-destructive saves."""
import codecs
from pathlib import Path

import pytest
from src.core.exceptions import GitError
from src.core.file_edit import read_text_file, replace_text_file


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("final_newline", [False, True])
@pytest.mark.parametrize(
    ("encoding", "bom"),
    [("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
     ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE)],
)
def test_text_file_roundtrip_preserves_format(tmp_path, encoding, bom, newline, final_newline):
    original = "first" + newline + "текст" + (newline if final_newline else "")
    data = bom + original.encode(encoding)
    path = tmp_path / "file.txt"
    path.write_bytes(data)
    snapshot = read_text_file(str(tmp_path), "file.txt", 1024)
    assert snapshot.encode(snapshot.text) == data
    changed = snapshot.encode(snapshot.text.replace("first", "edited"))
    replace_text_file(str(tmp_path), "file.txt", data, changed)
    assert path.read_bytes() == bom + original.replace("first", "edited").encode(encoding)


@pytest.mark.parametrize("data", [b"abc\0def", b"\xff\xfe\xff", b"\x80abc"])
def test_binary_or_invalid_text_is_rejected(tmp_path, data):
    (tmp_path / "file").write_bytes(data)
    with pytest.raises(GitError):
        read_text_file(str(tmp_path), "file", 1024)


def test_size_limit_never_returns_truncated_text(tmp_path):
    (tmp_path / "file").write_bytes(b"x" * 11)
    with pytest.raises(GitError, match="size limit"):
        read_text_file(str(tmp_path), "file", 10)


@pytest.mark.parametrize("path", ["../outside.txt", ".git/config", "missing.txt", "."])
def test_non_worktree_files_are_rejected(tmp_path, path):
    with pytest.raises(GitError):
        read_text_file(str(tmp_path), path, 1024)


def test_save_rejects_external_change(tmp_path):
    path = tmp_path / "file"
    path.write_bytes(b"external")
    with pytest.raises(GitError, match="changed on disk"):
        replace_text_file(str(tmp_path), "file", b"original", b"edited")
    assert path.read_bytes() == b"external"


def test_failed_replace_keeps_file_and_removes_temporary(tmp_path, monkeypatch):
    import src.core.file_edit as module

    path = tmp_path / "file"
    path.write_bytes(b"original")

    def fail(*args):
        raise PermissionError("read-only")

    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(GitError, match="read-only"):
        replace_text_file(str(tmp_path), "file", b"original", b"edited")
    assert path.read_bytes() == b"original"
    assert set(tmp_path.iterdir()) == {Path(path)}


def test_edit_keeps_mixed_endings_on_unchanged_lines(tmp_path):
    original = b"first\r\nsecond\nthird\r\nlast"
    (tmp_path / "file").write_bytes(original)
    snapshot = read_text_file(str(tmp_path), "file", 1024)
    assert snapshot.encode(snapshot.text) == original
    assert snapshot.encode(snapshot.text.replace("first", "edited")) == (
        b"edited\r\nsecond\nthird\r\nlast"
    )
