"""Read-only staged snapshots for commit-message generation, including unborn HEAD."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pygit2

from src.core.exceptions import GitError
from src.core.repository import RepositoryManager


@dataclass(frozen=True)
class StagedSnapshot:
    identity: str
    branch: str
    diff: str


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
    """Diff HEAD against the index only; never read working-tree file contents."""
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
        chunks: list[str] = []
        total = 0

        def append(text: str) -> None:
            nonlocal total
            total += len(text)
            if total > max_chars:
                raise GitError("Staged diff is too large. Split the changes into smaller commits.")
            chunks.append(text)

        if repo.head_is_unborn:
            for entry in index:
                if entry.mode == pygit2.GIT_FILEMODE_COMMIT:
                    append(
                        f"diff --git a/{entry.path} b/{entry.path}\nnew file mode {entry.mode:o}\n"
                    )
                    append(f"+Subproject commit {entry.id}\n")
                else:
                    patch = pygit2.Patch.create_from(
                        None,
                        repo[entry.id],
                        old_as_path=entry.path,
                        new_as_path=entry.path,
                    )
                    append(
                        patch.text.replace(
                            "new file mode 100644", f"new file mode {entry.mode:o}", 1
                        )
                    )
        else:
            diff = repo.head.peel(pygit2.Commit).tree.diff_to_index(index)
            diff.find_similar()
            for patch in diff:
                append(patch.text)
        if staged_identity(path) != identity:
            raise GitError("Staged changes moved while reading the diff. Please try again.")
        text = "".join(chunks)
        if not text.strip():
            raise GitError("Stage some changes before generating a commit message.")
        return StagedSnapshot(identity, str(branch).removeprefix("refs/heads/"), text)
    except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
        raise GitError(f"Cannot read staged changes: {exc}") from exc
