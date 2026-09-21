"""Read status in a Git process so libgit2 cannot hold the GUI's Python GIL."""
from __future__ import annotations

import os
import subprocess

import pygit2

from src.core.exceptions import GitError, GitNotInstalledError

_INDEX_FLAGS = {
    " ": 0,
    "A": pygit2.GIT_STATUS_INDEX_NEW,
    "M": pygit2.GIT_STATUS_INDEX_MODIFIED,
    "D": pygit2.GIT_STATUS_INDEX_DELETED,
    "T": pygit2.GIT_STATUS_INDEX_TYPECHANGE,
}
_WORKTREE_FLAGS = {
    " ": 0,
    "A": pygit2.GIT_STATUS_WT_NEW,
    "M": pygit2.GIT_STATUS_WT_MODIFIED,
    "D": pygit2.GIT_STATUS_WT_DELETED,
    "T": pygit2.GIT_STATUS_WT_TYPECHANGE,
}
_CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _parse_status(output: bytes) -> dict[str, int]:
    """Parse porcelain v1 -z with rename detection disabled, like pygit2.status."""
    result = {}
    if not output:
        return result
    if not output.endswith(b"\0"):
        raise GitError("Incomplete Git status output.")
    for entry in output[:-1].split(b"\0"):
        if len(entry) < 4 or entry[2:3] != b" ":
            raise GitError("Invalid Git status record.")
        xy = entry[:2].decode("ascii", errors="replace")
        path = entry[3:].decode("utf-8", errors="surrogateescape")
        if xy == "??":
            flags = pygit2.GIT_STATUS_WT_NEW
        elif xy in _CONFLICTS:
            flags = pygit2.GIT_STATUS_CONFLICTED
        else:
            try:
                flags = _INDEX_FLAGS[xy[0]] | _WORKTREE_FLAGS[xy[1]]
            except KeyError as exc:
                raise GitError(f"Unexpected Git status code: {xy!r}") from exc
        result[path] = int(flags)
    return result


def read_status(repo: pygit2.Repository) -> dict[str, int]:
    """Return pygit2-compatible flags without writing/refreshing the Git index.

    Even a QRunnable cannot keep Python UI callbacks responsive while the
    pygit2 status extension holds the GIL. Waiting for a separate Git process
    releases it. Explicit repository paths also support linked worktrees and
    managers opened through a subdirectory or their administrative directory.
    """
    if repo.workdir is None:
        raise GitError("Cannot read working-tree status in a bare repository.")
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
    ):
        env.pop(key, None)
    try:
        completed = subprocess.run(
            [
                "git", "--no-optional-locks", f"--git-dir={repo.path}",
                f"--work-tree={repo.workdir}", "status", "--porcelain=v1", "-z",
                "--untracked-files=all", "--no-renames", "--no-ahead-behind",
            ],
            cwd=repo.workdir, env=env, capture_output=True, check=False, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise GitNotInstalledError("`git` CLI is not in PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("Reading Git status timed out after 30s.") from exc
    except OSError as exc:
        raise GitError(f"Cannot read Git status: {exc}") from exc
    if completed.returncode:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Cannot read Git status: {message}")
    return _parse_status(completed.stdout)
