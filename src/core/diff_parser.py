r"""Diff parsing helpers: raw ``pygit2.Diff`` -> structured :class:`DiffEntry`.

The functions here intentionally return plain dataclasses with optional
``raw_patch`` strings — never ``pygit2`` objects — so the ViewModel and
UI layers can hand them across thread boundaries and Qt models without
worrying about libgit2 object lifetime.

For the UI diff view, :func:`parse_diff_lines` breaks unified diff text
into typed lines (header, hunk, addition, deletion, …) so the widget can
apply per-line colour coding without re-parsing ``pygit2`` objects. The
returned :class:`ParsedDiffLine` records the line number in the
referenced file, so a per-file viewer can show the user *which* file
line each addition/deletion lives on (not just sequential diff line
numbers).
"""
from __future__ import annotations

import re
from enum import Enum, auto
from typing import NamedTuple

import pygit2

from src.core.exceptions import GitError
from src.core.models import DiffEntry, FileStatus


class DiffLineType(Enum):
    """Semantic type of a single line in a unified diff."""

    HEADER = auto()
    HUNK = auto()
    ADDITION = auto()
    DELETION = auto()
    CONTEXT = auto()
    EMPTY = auto()


class ParsedDiffLine(NamedTuple):
    """A single line of a unified diff together with its file line number.

    Attributes
    ----------
    line_type:
        Semantic classification (header, hunk, addition, …).
    text:
        The line exactly as it appears in the diff (including the
        leading ``+``/``-``/`` `` prefix where applicable).
    line_number:
        The line number in the *file* this entry refers to, or
        ``None`` for meta lines that don't map to a real file row
        (file/hunk headers, ``\\ No newline at end of file`` markers,
        and any orphan line outside of a hunk).

        * For ``ADDITION`` and ``CONTEXT`` lines this is the line
          number in the new (post-image) file.
        * For ``DELETION`` lines this is the line number in the old
          (pre-image) file.
        * For ``HUNK`` lines this is the new-file line number of the
          first line in the hunk — useful as a separator marker, but
          the viewer usually leaves the gutter blank for it.
    """

    line_type: DiffLineType
    text: str
    line_number: int | None

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


def parse_diff_lines(text: str) -> list[ParsedDiffLine]:
    """Break unified diff *text* into :class:`ParsedDiffLine` entries.

    The caller receives every line (including empty ones) tagged with its
    semantic role so a UI widget can apply per-line colour coding without
    re-parsing the raw ``pygit2`` objects. Each line also carries the
    line number in the file it refers to (or ``None`` for meta lines),
    so a per-file viewer can paint the real file line in the gutter
    instead of a sequential diff line index.

    Classification rules::

        * ``diff --git``, ``index``, ``---``, ``+++``, ``old mode``,
          ``new mode``, ``deleted file mode``, ``new file mode``,
          ``rename from``, ``rename to``, ``similarity index``,
          ``copy from``, ``copy to``, ``Binary files`` → HEADER
        * ``@@ … @@`` → HUNK
        * ``+…`` (but not ``+++``) → ADDITION
        * ``-…`` (but not ``---``) → DELETION
        * `` …`` (leading space) → CONTEXT
        * ``\\ No newline at end of file`` → EMPTY (treated like
          a marker, not a real line)
        * Everything else (including truly blank lines inside hunks) → EMPTY

    Line-number accounting::

        * A HUNK header is parsed for ``-old_start`` and ``+new_start``
          and resets the two running counters.
        * CONTEXT advances both counters.
        * ADDITION advances the new counter.
        * DELETION advances the old counter.
        * Lines outside any hunk (orphan lines from a malformed diff)
          get ``line_number = None`` so the viewer can leave the gutter
          blank rather than print a misleading number.
    """
    result: list[ParsedDiffLine] = []
    old_line = 0
    new_line = 0
    in_hunk = False
    for line in text.splitlines(keepends=False):
        stripped = line.rstrip("\n\r")
        if stripped.startswith("@@") and "@@" in stripped[3:]:
            old_start, new_start = _parse_hunk_header(stripped)
            old_line = old_start
            new_line = new_start
            in_hunk = True
            # The marker itself points at the new file's first line in
            # the hunk; for a fully-deleted file the new side is
            # empty (``new_start == 0``) and the marker instead
            # refers to the old line where the deleted content used
            # to start.
            result.append(
                ParsedDiffLine(
                    DiffLineType.HUNK, stripped, new_start or old_start,
                ),
            )
        elif stripped.startswith("+") and not stripped.startswith("+++"):
            result.append(
                ParsedDiffLine(
                    DiffLineType.ADDITION,
                    stripped,
                    new_line if in_hunk else None,
                ),
            )
            if in_hunk:
                new_line += 1
        elif stripped.startswith("-") and not stripped.startswith("---"):
            result.append(
                ParsedDiffLine(
                    DiffLineType.DELETION,
                    stripped,
                    old_line if in_hunk else None,
                ),
            )
            if in_hunk:
                old_line += 1
        elif stripped.startswith(" ") and len(stripped) >= 1:
            # Context lines inside hunks start with a single space.
            result.append(
                ParsedDiffLine(
                    DiffLineType.CONTEXT,
                    stripped,
                    new_line if in_hunk else None,
                ),
            )
            if in_hunk:
                old_line += 1
                new_line += 1
        elif _is_header_line(stripped):
            # A new file header ends the current hunk. The next
            # hunk header (if any) will reset the counters.
            in_hunk = False
            result.append(ParsedDiffLine(DiffLineType.HEADER, stripped, None))
        else:
            result.append(ParsedDiffLine(DiffLineType.EMPTY, stripped, None))
    return result


_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@",
)


def _parse_hunk_header(line: str) -> tuple[int, int]:
    """Return ``(old_start, new_start)`` for a ``@@ -X,Y +A,B @@`` line.

    ``0`` is preserved as-is — diffs for fully added/removed files use
    ``@@ -0,0 +1,N @@`` / ``@@ -1,N +0,0 @@`` and the caller can decide
    whether to display the zero or treat it as "no file on this side".
    A malformed header falls back to ``(1, 1)`` so the counters still
    produce sensible (if conservative) line numbers.
    """
    m = _HUNK_HEADER_RE.match(line)
    if not m:
        return 1, 1
    return int(m.group("old_start")), int(m.group("new_start"))


def _is_header_line(line: str) -> bool:
    """Return ``True`` if *line* is a file-level or extended-header
    line in a unified diff."""
    if not line:
        return False
    return (
        line.startswith("diff --git")
        or line.startswith("index ")
        or line.startswith("--- ")
        or line.startswith("+++ ")
        or line.startswith("old mode ")
        or line.startswith("new mode ")
        or line.startswith("deleted file mode ")
        or line.startswith("new file mode ")
        or line.startswith("rename from ")
        or line.startswith("rename to ")
        or line.startswith("similarity index ")
        or line.startswith("copy from ")
        or line.startswith("copy to ")
        or line.startswith("Binary files ")
    )


__all__ = [
    "DiffLineType",
    "ParsedDiffLine",
    "diff_to_text",
    "parse_diff",
    "parse_diff_lines",
]
