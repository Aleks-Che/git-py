"""Read per-file patches without materializing unrelated file contents."""
from __future__ import annotations

import os
from pathlib import Path

import pygit2

from src.core.diff_parser import filter_staged_diff_lines
from src.core.exceptions import GitError
from src.core.repository import RepositoryManager


def extract_file_patch(diff: pygit2.Diff, path: str) -> str:
    """Inspect cheap deltas first: iterating Diff itself builds every patch."""
    normalized = path.casefold() if os.name == "nt" else path
    pieces = []
    try:
        for index, delta in enumerate(diff.deltas):
            paths = (delta.old_file.path or "", delta.new_file.path or "")
            if os.name == "nt":
                paths = tuple(item.casefold() for item in paths)
            if normalized in paths:
                pieces.append(diff[index].text or "")
    except (pygit2.GitError, KeyError, ValueError) as exc:
        raise GitError(f"Failed to diff {path!r}: {exc}") from exc
    return "".join(pieces)


def workdir_file_diff(
    manager: RepositoryManager, path: str, staged: bool = False, context_lines: int = 3,
) -> str:
    """Preserve the WIP viewer's HEAD/index filtering and partial staging semantics."""
    repo = manager.repo
    full = Path(repo.workdir) / path if repo.workdir else None
    try:
        if full is not None:
            try:
                with full.open("rb") as stream:
                    binary = b"\0" in stream.read(8192)
            except OSError:
                binary = False
            if binary and not staged:
                return f"Binary file {path} differs from HEAD.\n"

        if not staged:
            try:
                repo.revparse_single(f"HEAD:{path}")
            except (KeyError, pygit2.GitError, ValueError):
                if full is None or not full.exists():
                    return f"New file: {path} (not found on disk)\n"
                content = full.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines() or [""]
                added = "\n".join(f"+{line}" for line in lines)
                return (
                    f"diff --git a/{path} b/{path}\nnew file mode 100644\n"
                    f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n"
                    f"{added}\n"
                )

        base = repo[repo.TreeBuilder().write()] if repo.head_is_unborn else "HEAD"
        flags = (
            pygit2.enums.DiffOption.INCLUDE_UNTRACKED
            | pygit2.enums.DiffOption.RECURSE_UNTRACKED_DIRS
        )
        text = extract_file_patch(
            repo.diff(base, cached=staged, context_lines=context_lines, flags=flags), path,
        )
        if staged or not text:
            return text
        staged_text = extract_file_patch(repo.diff(base, cached=True, context_lines=3), path)
        return filter_staged_diff_lines(text, staged_text)[0]
    except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
        raise GitError(f"Failed to diff {path!r}: {exc}") from exc


def read_workdir_file_diff(
    repo_path: str, path: str, staged: bool, context_lines: int,
) -> str:
    """Open a worker-owned handle; never share the GUI's repository with a thread."""
    manager = RepositoryManager(repo_path)
    try:
        return workdir_file_diff(manager, path, staged, context_lines)
    finally:
        manager.close()
