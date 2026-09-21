"""Three-way text comparison and immutable index snapshots; no UI dependencies."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pygit2

from src.core.exceptions import GitError
from src.core.file_edit import replace_text_file
from src.core.repository import RepositoryManager


def is_binary_blob(data: bytes) -> bool:
    return b"\0" in data[:8192]


def decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8", "cp1251"):
        try:
            return data.decode(encoding).replace("\r\n", "\n"), encoding
        except UnicodeDecodeError:
            pass
    raise GitError("The conflict contains text in an unsupported encoding.")


@dataclass(frozen=True)
class MergeRegion:
    ours: tuple[str, ...]
    theirs: tuple[str, ...]
    base: tuple[str, ...]
    automatic: tuple[str, ...] | None  # None means a choice is required.


@dataclass(frozen=True)
class ConflictSnapshot:
    path: str
    base: bytes
    ours: bytes
    theirs: bytes
    ours_label: str = "Target"
    theirs_label: str = "Incoming"
    identity: tuple[str, ...] = ()
    worktree: bytes | None = None

    @property
    def binary(self) -> bool:
        return any(is_binary_blob(data) for data in (self.base, self.ours, self.theirs))

    def encode(self, text: str) -> bytes:
        original = self.ours or self.theirs or self.base
        _, encoding = decode_text(original)
        crlf = original.count(b"\r\n")
        if crlf > original.count(b"\n") - crlf:
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        try:
            return text.encode(encoding)
        except UnicodeEncodeError as exc:
            raise GitError(f"The result cannot be saved using {encoding}.") from exc


def load_conflict(repo: RepositoryManager, path: str) -> ConflictSnapshot:
    """Read all stages afresh. Never silently replace missing blobs with empty text."""
    try:
        r = repo.repo
        r.index.read(force=True)
        entries = next((entries for entries in (r.index.conflicts or [])
                        if any(e is not None and e.path == path for e in entries)), None)
        if entries is None:
            raise GitError("This file is no longer conflicted. Refresh the repository.")
        if any(e is not None and e.mode not in (0o100644, 0o100755) for e in entries):
            raise GitError("Resolve symbolic-link and submodule conflicts with an external tool.")
        if any(e is not None and e.path != path for e in entries):
            raise GitError("Resolve rename conflicts with an external tool.")
        target = _conflict_path(repo, path)
        worktree = target.read_bytes() if target.exists() else None
        data = [bytes(r[e.id].data) if e is not None else b"" for e in entries]
        ours_label = _commit_label(r, str(r.head.target),
                                   r.head.shorthand if not r.head_is_detached else "")
        theirs_label = "Incoming"
        operation = ""
        for name in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            marker = Path(r.path) / name
            if marker.exists():
                oid = marker.read_text(encoding="ascii").strip().splitlines()[0]
                operation = f"{name}:{oid}"
                theirs_label = _commit_label(r, oid)
                break
        identity = (str(Path(r.path).resolve()), r.head.name, str(r.head.target), operation,
                    *(f"{e.id}:{e.mode}:{e.path}" if e else "" for e in entries))
        return ConflictSnapshot(path, *data, ours_label, theirs_label, identity, worktree)
    except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
        raise GitError(f"Cannot read conflict {path!r}: {exc}") from exc


def _commit_label(r: pygit2.Repository, oid: str, branch: str = "") -> str:
    commit = r[pygit2.Oid(hex=oid)]
    if not branch:
        names = []
        for name in r.references:
            if name.startswith(("refs/heads/", "refs/remotes/")):
                ref = r.references[name].resolve()
                if str(ref.target) == oid:
                    names.append(ref.shorthand)
        branch = ", ".join(names) or "commit"
    subject = commit.message.splitlines()[0] if commit.message else ""
    return f"{branch} · {oid[:8]} · {subject}"


def _conflict_path(repo: RepositoryManager, path: str) -> Path:
    root = Path(repo.path).resolve()
    relative = Path(path)
    target = root / relative
    if (relative.is_absolute() or any(p.lower() in ("..", ".git") for p in relative.parts)
            or target.is_symlink() or not target.resolve().is_relative_to(root)):
        raise GitError("Conflict path must be a regular file inside the working directory.")
    return target


def write_resolution(repo: RepositoryManager, snapshot: ConflictSnapshot, data: bytes) -> None:
    """Guard against external edits while a manual or AI draft was being prepared."""
    current = load_conflict(repo, snapshot.path)
    if current.identity != snapshot.identity or current.worktree != snapshot.worktree:
        raise GitError("Конфликт или файл изменился на диске. "
                       "Откройте его заново; черновик сохранён.")
    target = _conflict_path(repo, snapshot.path)
    created = False
    try:
        if snapshot.worktree is None:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation refuses a file created since the check above.
            with target.open("xb") as stream:
                stream.write(data)
            created = True
        else:
            replace_text_file(repo.path, snapshot.path, snapshot.worktree, data)
        repo.repo.index.read(force=True)
        repo.repo.index.add(snapshot.path)
        repo.repo.index.write()
    except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
        # Keep the unresolved index and return the original working file if staging failed.
        if created and target.read_bytes() == data:
            target.unlink()
        elif snapshot.worktree is not None and target.exists() and target.read_bytes() == data:
            replace_text_file(repo.path, snapshot.path, data, snapshot.worktree)
        repo.repo.index.read(force=True)
        raise GitError(f"Cannot save resolution for {snapshot.path!r}: {exc}") from exc


def compare_three_way(base: str, ours: str, theirs: str) -> list[MergeRegion]:
    """Group overlapping base edits and keep independent changes automatically.

    Adjacent replacements do not conflict; insertions at a replacement boundary
    are grouped conservatively. Lines retain their endings, including EOF.
    """
    ancestor = base.splitlines(keepends=True)
    sides = [ours.splitlines(keepends=True), theirs.splitlines(keepends=True)]
    edits = []
    for side, lines in enumerate(sides):
        for tag, a, b, c, d in SequenceMatcher(None, ancestor, lines, autojunk=False).get_opcodes():
            if tag != "equal":
                edits.append((a, b, side, lines[c:d]))
    edits.sort(key=lambda edit: (edit[0], edit[1], edit[2]))
    regions = []
    cursor = 0
    index = 0
    while index < len(edits):
        start, end, _, _ = edits[index]
        group = [edits[index]]
        index += 1
        while index < len(edits):
            a, b, _, _ = edits[index]
            if a > end or (a == end and b > a and all(x[1] > x[0] for x in group)):
                break
            group.append(edits[index])
            end = max(end, b)
            index += 1
        if cursor < start:
            common = tuple(ancestor[cursor:start])
            regions.append(MergeRegion(common, common, common, common))
        versions = []
        for side in (0, 1):
            content = []
            pos = start
            for a, b, edited_side, replacement in group:
                if edited_side == side:
                    content.extend(ancestor[pos:a])
                    content.extend(replacement)
                    pos = b
            content.extend(ancestor[pos:end])
            versions.append(tuple(content))
        original = tuple(ancestor[start:end])
        left, right = versions
        automatic = (left if left == right or right == original
                     else right if left == original else None)
        regions.append(MergeRegion(left, right, original, automatic))
        cursor = end
    if cursor < len(ancestor):
        common = tuple(ancestor[cursor:])
        regions.append(MergeRegion(common, common, common, common))
    return regions
