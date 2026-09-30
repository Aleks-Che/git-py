"""Repair stale index metadata without staging or discarding file content."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import pygit2

from src.core.exceptions import GitError, GitNotInstalledError
from src.core.repository import RepositoryManager, unwrap


def _git(repo: pygit2.Repository, index: Path, args: list[str]) -> bytes:
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        "GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS",
        "GIT_ICASE_PATHSPECS",
    ):
        env.pop(key, None)
    env["GIT_INDEX_FILE"] = str(index)
    try:
        result = subprocess.run(
            [
                "git", "--no-optional-locks", "--literal-pathspecs",
                "-c", "core.splitIndex=false", f"--git-dir={repo.path}",
                f"--work-tree={repo.workdir}", *args,
            ],
            cwd=repo.workdir, env=env, capture_output=True, check=False, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except FileNotFoundError as exc:
        raise GitNotInstalledError("`git` CLI is not in PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError("Repair File Status timed out after 30s.") from exc
    except OSError as exc:
        raise GitError(f"Cannot repair file status: {exc}") from exc
    if result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitError(f"Cannot repair file status: {message}")
    return result.stdout


def _staged_content(repo: pygit2.Repository, index: Path) -> tuple[bytes, bytes]:
    # The second snapshot distinguishes intent-to-add from a staged empty file.
    return (
        _git(repo, index, ["ls-files", "--stage", "-z"]),
        _git(repo, index, [
            "diff", "--cached", "--raw", "--no-abbrev", "-z", "--no-renames",
            "--no-ext-diff", "--no-textconv", "--ignore-submodules=none",
        ]),
    )


def repair_file_status(repo: RepositoryManager, paths: list[str]) -> None:
    """Re-add selected files in an isolated index, publishing only metadata changes.

    A stale cached size can survive both status and update-index --refresh even
    when the worktree blob equals the index. Git add rebuilds that cache. Holding
    index.lock and comparing staged entries before publishing protects partial
    staging, conflicts, mode changes and other Git writers. No worktree writes,
    commits or ref updates occur. A mixed selection is rejected as a whole.
    """
    paths = list(dict.fromkeys(paths))
    if not paths:
        raise GitError("Select files to repair.")
    with unwrap(repo) as r:
        lock_owned = False
        lock_path = Path(r.path) / "index.lock"
        try:
            if r.workdir is None:
                raise GitError("Cannot repair file status in a bare repository.")
            index_path = Path(r.path) / "index"
            with lock_path.open("xb") as lock:
                lock_owned = True
                original = index_path.read_bytes()
                index = r.index
                index.read(force=True)
                if index.conflicts is not None:
                    raise GitError("Resolve index conflicts before repairing file status.")
                for path in paths:
                    if path not in index or index[path].mode not in (
                        pygit2.GIT_FILEMODE_BLOB, pygit2.GIT_FILEMODE_BLOB_EXECUTABLE,
                    ):
                        raise GitError(f"Cannot repair {path!r}: select an existing tracked file.")
                with TemporaryDirectory(prefix="git-py-repair-", dir=r.path) as temporary:
                    candidate = Path(temporary) / "index"
                    candidate.write_bytes(original)
                    before = _staged_content(r, candidate)
                    # One path per call avoids Windows command-line length limits.
                    for path in paths:
                        _git(r, candidate, ["add", "--", path])
                    if _staged_content(r, candidate) != before:
                        raise GitError(
                            "Selected files have real unstaged changes. Use Stage or Discard "
                            "Changes to handle them. Repair left files and staging unchanged.",
                        )
                    if index_path.read_bytes() != original:
                        raise GitError("The index changed during repair. Refresh and try again.")
                    lock.write(candidate.read_bytes())
                    lock.flush()
                    os.fsync(lock.fileno())
            os.replace(lock_path, index_path)
            lock_owned = False
            index.read(force=True)
        except FileExistsError as exc:
            raise GitError(
                "The Git index is locked by another operation. Try again later.",
            ) from exc
        except (OSError, ValueError, KeyError, pygit2.GitError) as exc:
            raise GitError(f"Cannot repair file status: {exc}") from exc
        finally:
            if lock_owned:
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError as exc:
                    raise GitError(f"Cannot remove the repair index lock: {exc}") from exc
