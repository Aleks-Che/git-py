"""Read-only working-tree snapshots for background monitoring."""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pygit2

from src.core.exceptions import GitError
from src.core.repository import RepositoryManager


@dataclass(frozen=True)
class WorktreeStatus:
    raw_status: dict[str, int]
    selected_version: tuple | None


@dataclass(frozen=True)
class WorktreeChanges:
    """A dirty sibling worktree, detached from its repository handle."""

    path: str
    branch: str | None
    head_sha: str | None
    count: int

    @property
    def node_id(self) -> str:
        return f"WIP:{os.path.normcase(self.path)}"


def _other_worktree_paths(
    manager: RepositoryManager,
    error_callback: Callable[[str], None] | None = None,
) -> list[Path]:
    """List existing sibling checkouts, including the primary from a linked one."""
    primary = RepositoryManager()
    try:
        current = Path(manager.repo.workdir).resolve() if manager.repo.workdir else None
        git_dir = Path(manager.repo.path)
        common_file = git_dir / "commondir"
        common_dir = (
            (git_dir / common_file.read_text(encoding="utf-8").strip()).resolve()
            if common_file.exists() else git_dir
        )
        primary.open(str(common_dir))
        candidates = [primary.repo.workdir] if primary.repo.workdir else []
        for name in primary.repo.list_worktrees():
            try:
                candidates.append(primary.repo.lookup_worktree(name).path)
            except (pygit2.GitError, KeyError, OSError) as exc:
                if error_callback:
                    error_callback(f"Cannot read worktree {name}: {exc}")
    except (GitError, pygit2.GitError, OSError, ValueError) as exc:
        raise GitError(f"Cannot list worktrees: {exc}") from exc
    finally:
        primary.close()

    seen = {current}
    paths = []
    for candidate in candidates:
        path = Path(candidate)
        try:
            path = path.resolve()
            if path in seen or not (path / ".git").exists():
                continue
            seen.add(path)
            paths.append(path)
        except (OSError, ValueError) as exc:
            if error_callback:
                error_callback(f"Cannot read worktree {path}: {exc}")
    return paths


def find_branch_worktree(manager: RepositoryManager, branch_name: str) -> str | None:
    """Return another checkout using this local branch, whether clean or dirty.

    Only registration and HEAD are read; this does not scan or refresh indexes.
    The active checkout and detached HEADs never match a local branch.
    """
    def fail(message: str) -> None:
        raise GitError(message)

    for path in _other_worktree_paths(manager, fail):
        sibling = RepositoryManager()
        try:
            sibling.open(str(path))
            head = sibling.repo.lookup_reference("HEAD")
            if head.target == f"refs/heads/{branch_name}":
                return path.as_posix()
        except (GitError, pygit2.GitError, OSError, ValueError, KeyError) as exc:
            raise GitError(f"Cannot read worktree {path}: {exc}") from exc
        finally:
            sibling.close()
    return None


def read_other_worktree_changes(
    manager: RepositoryManager,
    error_callback: Callable[[str], None] | None = None,
) -> list[WorktreeChanges]:
    """Read each sibling's HEAD and index, skipping clean or unavailable checkouts."""
    result = []
    for path in _other_worktree_paths(manager, error_callback):
        sibling = RepositoryManager()
        try:
            sibling.open(str(path))
            snapshot = read_worktree_status(sibling, None, False)
            count = len(sibling.get_status_from_raw(snapshot.raw_status))
            if count:
                repo = sibling.repo
                head = None if repo.head_is_unborn else str(repo.head.target)
                branch = None if repo.head_is_detached else repo.lookup_reference("HEAD").target
                if branch and branch.startswith("refs/heads/"):
                    branch = branch[len("refs/heads/"):]
                result.append(WorktreeChanges(path.as_posix(), branch, head, count))
        except (GitError, pygit2.GitError, OSError, ValueError, KeyError) as exc:
            if error_callback:
                error_callback(f"Cannot read worktree {path}: {exc}")
        finally:
            sibling.close()
    return sorted(result, key=lambda entry: entry.path)


def read_worktree_status(
    manager: RepositoryManager, selected_path: str | None, staged: bool,
) -> WorktreeStatus:
    """Read status and the selected file's version without changing staged content."""
    try:
        repo = manager.repo
        index = repo.index
        if not repo.head_is_unborn and not (Path(repo.path) / "index").exists():
            raise GitError("Git index does not exist.")
        index.read(force=True)
        raw_status = manager.get_raw_status()
        version = None
        if selected_path is not None:
            try:
                entry = index[selected_path]
                indexed = (str(entry.id), entry.mode)
            except KeyError:
                indexed = None
            head = None if repo.head_is_unborn else str(repo.head.target)
            disk = None
            if not staged and repo.workdir:
                try:
                    stat = (Path(repo.workdir) / selected_path).stat()
                    disk = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
                except FileNotFoundError:
                    pass
            version = (selected_path, staged, head, indexed, disk)
        return WorktreeStatus(raw_status, version)
    except (pygit2.GitError, OSError, KeyError, ValueError) as exc:
        raise GitError(f"Cannot read working-tree status: {exc}") from exc
