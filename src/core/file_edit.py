"""Lossless text-file snapshots and guarded worktree writes, independent of Qt."""
from __future__ import annotations

import codecs
import os
import stat
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.core.exceptions import GitError


@dataclass(frozen=True)
class TextFileSnapshot:
    root: str
    path: str
    data: bytes
    text: str
    encoding: str
    newline: str
    bom: bytes = b""

    def encode(self, text: str) -> bytes:
        original = self.data[len(self.bom):].decode(self.encoding)
        original_lines = original.splitlines(keepends=True)
        normalized_lines = self.text.splitlines(keepends=True)
        new_lines = text.splitlines(keepends=True)
        # Preserve original endings on unchanged lines, including mixed LF/CRLF files.
        pieces = []
        for tag, start, end, new_start, new_end in SequenceMatcher(
            None, normalized_lines, new_lines,
        ).get_opcodes():
            if tag == "equal":
                pieces.extend(original_lines[start:end])
            else:
                pieces.extend(
                    line.replace("\n", self.newline) for line in new_lines[new_start:new_end]
                )
        return self.bom + "".join(pieces).encode(self.encoding)


def _worktree_file(root: str, path: str) -> Path:
    base = Path(root).resolve()
    relative = Path(path)
    if relative.is_absolute() or any(part.lower() in ("..", ".git") for part in relative.parts):
        raise GitError("Only regular files inside the working directory can be edited.")
    target = base / relative
    if not target.resolve().is_relative_to(base) or target.is_symlink():
        raise GitError("Cannot edit a symbolic link or a file outside the working directory.")
    if not stat.S_ISREG(target.stat().st_mode):
        raise GitError("Only regular text files can be edited.")
    return target


def read_text_file(root: str, path: str, max_bytes: int) -> TextFileSnapshot:
    """Read the complete worktree file; never decode with replacement characters."""
    try:
        target = _worktree_file(root, path)
        with target.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise GitError(f"File exceeds the editor size limit ({max_bytes} bytes).")
        encoding = "utf-8"
        bom = b""
        for prefix, candidate in (
            (codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
            (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"),
            (codecs.BOM_UTF8, "utf-8"),
        ):
            if data.startswith(prefix):
                encoding = candidate
                bom = prefix
                break
        decoded = data[len(bom):].decode(encoding)
        if "\0" in decoded:
            raise GitError("Binary files cannot be edited as text.")
        # QPlainTextEdit uses LF internally. Remember the file's newline convention.
        newline = "\r\n" if "\r\n" in decoded else "\r" if "\r" in decoded else "\n"
        text = decoded.replace("\r\n", "\n").replace("\r", "\n")
        return TextFileSnapshot(root, path, data, text, encoding, newline, bom)
    except (OSError, UnicodeError, ValueError) as exc:
        raise GitError(f"Cannot edit {path!r}: {exc}") from exc


def replace_text_file(root: str, path: str, expected: bytes, data: bytes) -> None:
    """Atomically replace a file only while its disk contents match the snapshot."""
    temporary = None
    try:
        target = _worktree_file(root, path)
        if target.read_bytes() != expected:
            raise GitError(f"Cannot save {path!r}: the file changed on disk. Your edits are kept.")
        mode = target.stat().st_mode
        with NamedTemporaryFile(dir=target.parent, prefix=".git-py-edit-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(stat.S_IMODE(mode))
        if _worktree_file(root, path).read_bytes() != expected:
            raise GitError(f"Cannot save {path!r}: the file changed on disk. Your edits are kept.")
        os.replace(temporary, target)
    except (OSError, ValueError) as exc:
        raise GitError(f"Cannot save {path!r}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
