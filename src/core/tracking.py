"""Remove ignored files from the index while preserving their working copies."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pygit2

from src.core.exceptions import GitError
from src.core.repository import RepositoryManager, unwrap


@dataclass(frozen=True)
class TrackingSnapshot:
    head: tuple[str, str | None]
    entries: tuple[tuple[str, str, int], ...]


def _head(repo: pygit2.Repository) -> tuple[str, str | None]:
    return (
        str(repo.lookup_reference("HEAD").target),
        None if repo.head_is_unborn else str(repo.head.target),
    )


def tracked_ignored_paths(repo: RepositoryManager, paths: Sequence[str]) -> set[str]:
    """Match ignore rules even for tracked files, including nested rules/exclusions."""
    with unwrap(repo) as r:
        try:
            index = r.index
            index.read(force=True)
            return {
                path for path in paths
                if path in index
                and index[path].mode != pygit2.GIT_FILEMODE_COMMIT
                and r.path_is_ignored(path)
            }
        except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
            raise GitError(f"Failed to check tracked ignored files: {exc}") from exc


def snapshot_tracking(repo: RepositoryManager, paths: Sequence[str]) -> TrackingSnapshot:
    """Capture the exact index versions; never read file contents into the snapshot."""
    with unwrap(repo) as r:
        try:
            if not paths:
                raise GitError("Select files to stop tracking.")
            index = r.index
            index.read(force=True)
            return TrackingSnapshot(
                _head(r),
                tuple((path, str(index[path].id), int(index[path].mode))
                      for path in dict.fromkeys(paths)),
            )
        except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
            raise GitError(f"Failed to read tracked files: {exc}") from exc


def set_tracking(
    repo: RepositoryManager, snapshot: TrackingSnapshot, *, tracked: bool,
) -> None:
    """Apply/undo removal, validating every path before a single index write.

    Only selected entries are changed. Later worktree edits and unrelated staged
    changes survive both directions. Ref/HEAD changes and edits to the affected
    index entries reject Undo/Redo instead of overwriting newer work.
    """
    with unwrap(repo) as r:
        try:
            if r.is_bare or r.state() != pygit2.GIT_REPOSITORY_STATE_NONE:
                raise GitError("Finish the current Git operation before changing tracking.")
            if _head(r) != snapshot.head:
                raise GitError("HEAD or branch changed; cannot change tracking from this snapshot.")
            index = r.index
            index.read(force=True)
            if index.conflicts is not None:
                raise GitError("Resolve index conflicts before changing tracking.")
            # Build ancestor paths once so Undo also detects a file/directory
            # replacement in the index, without quadratic scans for a batch.
            ancestors = set()
            if tracked:
                for entry in index:
                    parts = entry.path.split("/")
                    ancestors.update("/".join(parts[:i]) for i in range(1, len(parts)))
            for path, oid, mode in snapshot.entries:
                if tracked:
                    parts = path.split("/")
                    if (path in index or path in ancestors
                            or any("/".join(parts[:i]) in index for i in range(1, len(parts)))):
                        raise GitError(f"Index changed for {path!r}; cannot restore tracking.")
                else:
                    if path not in index or (str(index[path].id), int(index[path].mode)) != (
                        oid, mode,
                    ):
                        raise GitError(f"Index changed for {path!r}; cannot stop tracking.")
                    if mode == pygit2.GIT_FILEMODE_COMMIT or not r.path_is_ignored(path):
                        raise GitError(
                            f"{path!r} is not an ignored file. Add an ignore rule first.",
                        )
                    flags = r.status_file(path)
                    staged = flags & (
                        pygit2.GIT_STATUS_INDEX_NEW | pygit2.GIT_STATUS_INDEX_MODIFIED
                        | pygit2.GIT_STATUS_INDEX_TYPECHANGE | pygit2.GIT_STATUS_INDEX_RENAMED
                    )
                    unstaged = flags & (
                        pygit2.GIT_STATUS_WT_MODIFIED | pygit2.GIT_STATUS_WT_DELETED
                        | pygit2.GIT_STATUS_WT_TYPECHANGE | pygit2.GIT_STATUS_WT_RENAMED
                        | pygit2.GIT_STATUS_WT_UNREADABLE
                    )
                    if staged and unstaged:
                        raise GitError(
                            f"{path!r} has staged content different from both HEAD and the "
                            "working copy. Commit or unstage it before stopping tracking.",
                        )
            for path, oid, mode in snapshot.entries:
                if tracked:
                    index.add(pygit2.IndexEntry(path, pygit2.Oid(hex=oid), mode))
                else:
                    index.remove(path)
            index.write()
        except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
            raise GitError(f"Failed to change file tracking: {exc}") from exc
