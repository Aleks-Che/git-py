"""Diff parsing helpers: raw ``pygit2.Diff`` -> structured :class:`DiffEntry`.

The functions here intentionally return plain dataclasses with optional
``raw_patch`` strings — never ``pygit2`` objects — so the ViewModel and
UI layers can hand them across thread boundaries and Qt models without
worrying about libgit2 object lifetime.
"""
from __future__ import annotations

import pygit2

from src.core.exceptions import GitError
from src.core.models import DiffEntry, FileStatus

# Map ``pygit2`` delta status to our :class:`FileStatus` enum.
_DELTA_TO_STATUS: dict[int, FileStatus] = {
    pygit2.GIT_DELTA_ADDED: FileStatus.NEW,
    pygit2.GIT_DELTA_DELETED: FileStatus.DELETED,
    pygit2.GIT_DELTA_MODIFIED: FileStatus.MODIFIED,
    pygit2.GIT_DELTA_RENAMED: FileStatus.RENAMED,
    pygit2.GIT_DELTA_COPIED: FileStatus.COPIED,
    pygit2.GIT_DELTA_TYPECHANGE: FileStatus.TYPE_CHANGED,
    pygit2.GIT_DELTA_CONFLICTED: FileStatus.CONFLICTED,
    pygit2.GIT_DELTA_IGNORED: FileStatus.IGNORED,
    pygit2.GIT_DELTA_UNTRACKED: FileStatus.UNTRACKED,
}


def parse_diff(
    diff: pygit2.Diff,
    repo: pygit2.Repository | None = None,
) -> list[DiffEntry]:
    """Convert a ``pygit2.Diff`` into a list of :class:`DiffEntry`.

    Unmodified entries (``GIT_DELTA_UNMODIFIED``) are skipped — callers
    only care about changes.

    For ``GIT_DELTA_ADDED``/``GIT_DELTA_DELETED`` files libgit2's
    ``line_stats`` is unreliable (added lines show up as "deletions
    from the empty ancestor" and the reverse); we count newlines in
    the blob instead so :attr:`DiffEntry.additions` /
    :attr:`deletions` match ``git diff --stat``. ``repo`` is required
    to read the blob; if omitted, the counts fall back to ``line_stats``
    and may be off for fully added/fully deleted files.
    """
    if not isinstance(diff, pygit2.Diff):
        raise GitError(f"parse_diff expected pygit2.Diff, got {type(diff).__name__}")
    result: list[DiffEntry] = []
    for patch in diff:
        delta = patch.delta
        status = _DELTA_TO_STATUS.get(delta.status, FileStatus.MODIFIED)
        additions, deletions, _ = patch.line_stats
        if delta.status == pygit2.GIT_DELTA_ADDED:
            additions = _blob_line_count(repo, delta.new_file.id)
            deletions = 0
        elif delta.status == pygit2.GIT_DELTA_DELETED:
            additions = 0
            deletions = _blob_line_count(repo, delta.old_file.id)
        raw = patch.text if hasattr(patch, "text") else None
        old_path = delta.old_file.path if delta.old_file.id else None
        new_path = delta.new_file.path if delta.new_file.id else None
        result.append(
            DiffEntry(
                old_path=old_path,
                new_path=new_path,
                status=status,
                additions=additions,
                deletions=deletions,
                is_binary=bool(delta.is_binary),
                raw_patch=raw or None,
            ),
        )
    return result


def _blob_line_count(repo: pygit2.Repository | None, oid: pygit2.Oid) -> int:
    """Count newlines in the blob identified by ``oid`` (0 for an empty/missing blob)."""
    if repo is None or not oid:
        return 0
    try:
        return repo[oid].data.count(b"\n")
    except KeyError:
        return 0


def diff_to_text(diff: pygit2.Diff) -> str:
    """Return the unified-diff text for the whole ``pygit2.Diff`` object."""
    if not isinstance(diff, pygit2.Diff):
        raise GitError(f"diff_to_text expected pygit2.Diff, got {type(diff).__name__}")
    return diff.patch


__all__ = ["diff_to_text", "parse_diff"]
