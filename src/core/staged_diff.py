"""Read-only staged snapshots for commit-message generation, including unborn HEAD."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath

import pygit2

from src.core.exceptions import GitError
from src.core.repository import RepositoryManager


@dataclass(frozen=True)
class StagedSnapshot:
    identity: str
    branch: str
    diff: str


@dataclass(frozen=True)
class _ContextChange:
    summary: str
    patch: Callable[[int], str] | None = None
    added: bool = False


_OMITTED_PATCH = "[Patch omitted: context size limit.]\n"
# Some assets (SVG, PPM, PDF, RAW exports) can look like text to libgit2.
# Keep this classification independent of Qt and installed image plugins.
_ASSET_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".svgz", ".ico",
    ".tif", ".tiff", ".avif", ".heic", ".heif", ".raw", ".psd", ".dds", ".exr",
    ".hdr", ".pbm", ".pgm", ".ppm", ".pnm", ".xbm", ".xpm", ".pdf", ".eps",
    ".mp3", ".wav", ".flac", ".ogg", ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tar", ".woff", ".woff2",
    ".ttf", ".otf", ".bin", ".exe", ".dll", ".so", ".dylib", ".pyc",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".glb", ".gltf", ".fbx",
})


def _is_asset(repo: pygit2.Repository, path: str, oid: pygit2.Oid, mode: int) -> bool:
    if not mode or mode == pygit2.GIT_FILEMODE_COMMIT:
        return False
    return PurePosixPath(path).suffix.lower() in _ASSET_EXTENSIONS or repo[oid].is_binary


def _pack_context(changes: list[_ContextChange], max_chars: int) -> str:
    """Reserve every file operation before spending the remaining budget on patches."""
    chunks = [change.summary + (_OMITTED_PATCH if change.patch else "") for change in changes]
    remaining = max_chars - sum(map(len, chunks))
    if remaining < 0:
        raise GitError("Staged file list is too large. Split the changes into smaller commits.")
    # Real edits take priority over entire newly added files. A large patch must
    # not stop smaller changes later in the list from reaching the model.
    for position in sorted(range(len(changes)), key=lambda i: changes[i].added):
        change = changes[position]
        if change.patch is None:
            continue
        allowance = remaining + len(_OMITTED_PATCH)
        patch = change.patch(allowance)
        if patch and len(patch) <= allowance:
            chunks[position] = change.summary + patch
            remaining += len(_OMITTED_PATCH) - len(patch)
    return "".join(chunks)


def _quoted_path(path: str) -> str:
    return json.dumps(path, ensure_ascii=False)


def _move_summary(old: pygit2.DiffFile, new: pygit2.DiffFile) -> str:
    details = "content unchanged" if old.id == new.id else "content changed"
    if old.mode != new.mode:
        details += f"; mode {old.mode:o} -> {new.mode:o}"
    return f"Moved: {_quoted_path(old.path)} -> {_quoted_path(new.path)} ({details})\n"


def _new_file_patch(repo: pygit2.Repository, entry: pygit2.IndexEntry, allowance: int) -> str:
    if entry.mode == pygit2.GIT_FILEMODE_COMMIT:
        return f"+Subproject commit {entry.id}\n"
    blob = repo[entry.id]
    # Even four-byte UTF-8 characters cannot make this text addition fit.
    if blob.size > 4 * allowance:
        return ""
    patch = pygit2.Patch.create_from(
        None, blob, old_as_path=entry.path, new_as_path=entry.path
    )
    return patch.text.replace("new file mode 100644", f"new file mode {entry.mode:o}", 1)


def _added_change(repo: pygit2.Repository, entry: pygit2.IndexEntry) -> _ContextChange:
    summary = f"Added: {_quoted_path(entry.path)} (mode {entry.mode:o})\n"
    if _is_asset(repo, entry.path, entry.id, entry.mode):
        return _ContextChange(summary)
    return _ContextChange(
        summary, lambda limit: _new_file_patch(repo, entry, limit), added=True
    )


def _head_changes(repo: pygit2.Repository, index: pygit2.Index) -> list[_ContextChange]:
    diff = repo.head.peel(pygit2.Commit).tree.diff_to_index(index)
    # Do not let diff.renames=false turn moves into full delete/add patches.
    diff.find_similar(flags=pygit2.GIT_DIFF_FIND_RENAMES)
    deltas = list(diff.deltas)
    deleted = defaultdict(deque)
    for position, delta in enumerate(deltas):
        if delta.status == pygit2.GIT_DELTA_DELETED:
            old = delta.old_file
            deleted[(old.id, old.mode & 0o170000)].append(position)
    # Exact matches need no similarity search and remain cheap with thousands
    # of files, even if libgit2's bounded rename search did not match them.
    moves = {}
    consumed = set()
    for position, delta in enumerate(deltas):
        if delta.status == pygit2.GIT_DELTA_ADDED:
            new = delta.new_file
            candidates = deleted[(new.id, new.mode & 0o170000)]
            if candidates:
                source = candidates.popleft()
                moves[position] = deltas[source].old_file
                consumed.add(source)
    changes = []
    for position, delta in enumerate(deltas):
        if position in consumed:
            continue
        old, new = delta.old_file, delta.new_file
        if position in moves:
            changes.append(_ContextChange(_move_summary(moves[position], new)))
        elif delta.status == pygit2.GIT_DELTA_DELETED:
            changes.append(_ContextChange(
                f"Deleted: {_quoted_path(old.path)} (mode {old.mode:o}; content omitted)\n"
            ))
        elif delta.status == pygit2.GIT_DELTA_ADDED:
            changes.append(_added_change(repo, index[new.path]))
        else:
            if delta.status == pygit2.GIT_DELTA_RENAMED:
                summary = _move_summary(old, new)
            else:
                summary = (
                    f"Modified: {_quoted_path(new.path)} "
                    f"(mode {old.mode:o} -> {new.mode:o})\n"
                )
            changes.append(_ContextChange(
                summary,
                None if (
                    old.id == new.id
                    or _is_asset(repo, old.path, old.id, old.mode)
                    or _is_asset(repo, new.path, new.id, new.mode)
                ) else lambda _, i=position: diff[i].text,
            ))
    return changes


def _identity(repo: pygit2.Repository, index: pygit2.Index) -> str:
    head = repo.lookup_reference("HEAD")
    head_oid = "" if repo.head_is_unborn else str(repo.head.target)
    digest = hashlib.sha256(f"{head.target}\0{head_oid}\0".encode())
    for entry in index:
        digest.update(f"{entry.path}\0{entry.id}\0{entry.mode}\n".encode())
    return digest.hexdigest()


def staged_identity(path: str) -> str:
    """Reopen the repo and index so external staging and branch switches are visible."""
    try:
        repo = RepositoryManager(path).repo
        index = repo.index
        index.read()
        if index.conflicts is not None:
            raise GitError("Resolve staged conflicts before generating a commit message.")
        return _identity(repo, index)
    except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
        raise GitError(f"Cannot read the staged index: {exc}") from exc


def read_staged_snapshot(path: str, max_chars: int) -> StagedSnapshot:
    """Build bounded AI context from HEAD/index, with metadata for every operation.

    Deleted files, unchanged moves and assets have no content patch. Other patches
    are included when they fit; working-tree contents are never read.
    """
    try:
        repo = RepositoryManager(path).repo
        index = repo.index
        index.read()
        if index.conflicts is not None:
            raise GitError("Resolve staged conflicts before generating a commit message.")
        identity = _identity(repo, index)
        branch = (
            repo.lookup_reference("HEAD").target if repo.head_is_unborn else repo.head.shorthand
        )
        if repo.head_is_unborn:
            changes = [_added_change(repo, entry) for entry in index]
        else:
            changes = _head_changes(repo, index)
        text = _pack_context(changes, max_chars)
        if staged_identity(path) != identity:
            raise GitError("Staged changes moved while reading the diff. Please try again.")
        if not text.strip():
            raise GitError("Stage some changes before generating a commit message.")
        return StagedSnapshot(identity, str(branch).removeprefix("refs/heads/"), text)
    except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
        raise GitError(f"Cannot read staged changes: {exc}") from exc
