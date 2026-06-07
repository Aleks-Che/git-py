"""High-level Git operation wrappers.

Each function takes either a :class:`RepositoryManager` or a raw
``pygit2.Repository`` (see :func:`src.core.repository.unwrap`) and
returns either the resulting object (e.g. an OID, a Commit) or a
serialisable dataclass. Every operation translates ``pygit2.GitError``
into the appropriate domain exception from :mod:`src.core.exceptions`.

Per ``docs/DEVELOPMENT_RULES.md`` (section 2), every mutating operation
will eventually be wrapped in a ``GitCommand`` subclass by the ViewModel
layer and routed through ``CommandProcessor`` so the toolbar Undo/Redo
buttons keep working. The functions here are the *implementation*
behind those commands — they know nothing about the undo machinery.

Note on rebase: pygit2 1.x does not expose a high-level ``rebase()``
method. ``rebase_branch`` therefore shells out to the ``git`` CLI
(``git rebase <upstream>``); it raises
:class:`src.core.exceptions.GitNotInstalledError` if ``git`` is not
in ``PATH``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pygit2

from src.core.exceptions import (
    AuthError,
    DirtyWorkTreeError,
    GitError,
    GitNotInstalledError,
    InvalidRefError,
    MergeConflictError,
    NetworkError,
    RebaseConflictError,
)
from src.core.models import CommitInfo, RemoteInfo
from src.core.repository import RepositoryManager, unwrap

if TYPE_CHECKING:
    from collections.abc import Sequence


# ----- helpers --------------------------------------------------------------


def _now_signature(name: str = "git-py", email: str = "git-py@localhost") -> pygit2.Signature:
    return pygit2.Signature(name, email, int(time.time()), 0)


def _to_commit_info(commit: pygit2.Commit) -> CommitInfo:
    author = commit.author
    committer = commit.committer
    return CommitInfo(
        sha=str(commit.id),
        short_sha=str(commit.short_id),
        message=commit.message,
        author_name=author.name,
        author_email=author.email,
        author_time=author.time,
        committer_name=committer.name,
        committer_email=committer.email,
        committer_time=committer.time,
        parents=[str(p) for p in commit.parent_ids],
    )


def _ensure_clean(repo: pygit2.Repository) -> None:
    """Raise :class:`DirtyWorkTreeError` if ``repo`` has any index/worktree changes."""
    if any(repo.status()):
        raise DirtyWorkTreeError("Working tree has uncommitted changes.")


# ----- commit ---------------------------------------------------------------


def commit_changes(
    repo: RepositoryManager | pygit2.Repository,
    message: str,
    author: pygit2.Signature | None = None,
    committer: pygit2.Signature | None = None,
    stage_all: bool = True,
) -> CommitInfo:
    """Stage all tracked changes and create a commit on ``HEAD``.

    If ``stage_all`` is ``True`` (default), every modified/deleted tracked
    file is added to the index first. Untracked files are *not* staged
    — add them explicitly via the ViewModel layer.

    Returns the :class:`CommitInfo` of the new commit.
    """
    if not message or not message.strip():
        raise GitError("Commit message must not be empty.")
    author = author or _now_signature()
    committer = committer or _now_signature()
    with unwrap(repo) as r:
        if r.head_is_unborn:
            raise GitError("Cannot commit: HEAD is unborn (no initial commit yet).")
        if stage_all:
            r.index.add_all()  # adds all modified, removes deleted — see libgit2 docs
            r.index.write()
        try:
            tree_oid = r.index.write_tree()
            head_oid = r.head.target
            commit_oid = r.create_commit(
                "HEAD",
                author,
                committer,
                message,
                tree_oid,
                [head_oid],
            )
        except pygit2.GitError as exc:
            raise GitError(f"Commit failed: {exc}") from exc
    return _to_commit_info(r[commit_oid])


# ----- branches -------------------------------------------------------------


def create_branch(
    repo: RepositoryManager | pygit2.Repository,
    name: str,
    target_sha: str | None = None,
) -> str:
    """Create a local branch at ``target_sha`` (default: ``HEAD``). Returns the new branch name."""
    with unwrap(repo) as r:
        if target_sha is None:
            if r.head_is_unborn:
                raise GitError("Cannot create a branch: HEAD is unborn.")
            target = r.head.target
            target_obj = r[target]
        else:
            try:
                target_obj = r.revparse_single(target_sha).peel(pygit2.Commit)
            except (KeyError, pygit2.GitError, ValueError) as exc:
                raise InvalidRefError(f"Unknown target revision: {target_sha!r}") from exc
        try:
            r.create_branch(name, target_obj)
        except pygit2.GitError as exc:
            raise GitError(f"Failed to create branch {name!r}: {exc}") from exc
    return name


def delete_branch(
    repo: RepositoryManager | pygit2.Repository,
    name: str,
    force: bool = False,
) -> None:
    """Delete local branch ``name``.

    If ``force`` is ``False``, refuse to delete the branch the working
    tree is currently on.
    """
    with unwrap(repo) as r:
        try:
            branch = r.lookup_branch(name)
        except (KeyError, ValueError) as exc:
            raise InvalidRefError(f"Unknown branch: {name!r}") from exc
        if branch is None:
            raise InvalidRefError(f"Unknown branch: {name!r}")
        if branch.is_head() and not force:
            raise GitError(
                f"Cannot delete the current branch {name!r} (pass force=True to override).",
            )
        try:
            r.branches.delete(name)
        except pygit2.GitError as exc:
            raise GitError(f"Failed to delete branch {name!r}: {exc}") from exc


def checkout_branch(
    repo: RepositoryManager | pygit2.Repository,
    name: str,
    strategy: int = pygit2.GIT_CHECKOUT_SAFE,
) -> dict | None:
    """Switch ``HEAD`` to local branch ``name``.

    ``strategy`` defaults to ``GIT_CHECKOUT_SAFE`` which refuses to
    overwrite local changes; pass ``GIT_CHECKOUT_FORCE`` to override.

    Returns ``None`` on success. When ``GIT_CHECKOUT_SAFE`` is used and
    the working tree has uncommitted changes, returns a dict
    ``{"dirty_files": [str, ...]}`` so the caller can surface the exact
    file list to the user.

    Implementation:
    1. Pre-check: if SAFE and dirty, return dirty list without touching
       any files.
    2. ``set_head`` — atomically move HEAD to the target branch.
    3. ``checkout_head`` — update the working tree to match HEAD.
    4. Post-verify: if the working tree is still dirty after step 3,
       HEAD is rolled back. This catches the Windows edge case where
       ``checkout_head(FORCE)`` silently skips locked files.
    """
    with unwrap(repo) as r:
        refname = f"refs/heads/{name}"
        try:
            branch = r.lookup_branch(name)
        except (KeyError, ValueError) as exc:
            raise InvalidRefError(f"Unknown branch: {name!r}") from exc
        if branch is None:
            raise InvalidRefError(f"Unknown branch: {name!r}")

        if strategy == pygit2.GIT_CHECKOUT_SAFE:
            dirty = _dirty_paths(r)
            if dirty:
                return {"dirty_files": dirty}
            strategy = pygit2.GIT_CHECKOUT_FORCE

        previous_head = r.head.name if not r.head_is_unborn else None

        try:
            r.set_head(refname)
        except pygit2.GitError as exc:
            raise GitError(f"Cannot switch HEAD to {name!r}: {exc}") from exc

        try:
            r.checkout_head(strategy=strategy)
        except pygit2.GitError as exc:
            _rollback_head(r, previous_head)
            raise DirtyWorkTreeError(
                f"Cannot update working tree for {name!r}: {exc}",
            ) from exc

        remaining = _dirty_paths(r)
        if remaining:
            _rollback_head(r, previous_head)
            n = len(remaining)
            preview = ", ".join(remaining[:10])
            suffix = f" and {n - 10} more" if n > 10 else ""
            raise DirtyWorkTreeError(
                f"Checkout to {name!r} did not fully update the working tree "
                f"({n} file(s) still differ): {preview}{suffix}",
            )
    return None


def _rollback_head(repo: pygit2.Repository, previous_head: str | None) -> None:
    """Restore HEAD to *previous_head* after a failed checkout."""
    if previous_head is None:
        return
    try:
        repo.set_head(previous_head)
    except pygit2.GitError:
        pass


def _dirty_paths(repo: pygit2.Repository) -> list[str]:
    """Return the list of paths with uncommitted changes (index or worktree)."""
    return [p for p, _ in repo.status().items()]


def checkout_commit(
    repo: RepositoryManager | pygit2.Repository,
    sha: str,
    strategy: int = pygit2.GIT_CHECKOUT_SAFE,
) -> dict | None:
    """Switch ``HEAD`` to a specific commit (detached HEAD mode).

    Resolves ``sha`` to a commit and switches ``HEAD`` to it directly,
    leaving the repository in detached-HEAD state. ``strategy`` defaults
    to ``GIT_CHECKOUT_SAFE`` which refuses to overwrite local changes;
    pass ``GIT_CHECKOUT_FORCE`` to override.

    Returns ``None`` on success. When ``GIT_CHECKOUT_SAFE`` is used and
    the working tree has uncommitted changes, returns a dict
    ``{"dirty_files": [str, ...]}`` so the caller can surface the exact
    file list to the user.
    """
    with unwrap(repo) as r:
        try:
            commit = r.revparse_single(sha).peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(f"Unknown revision: {sha!r}") from exc

        if strategy == pygit2.GIT_CHECKOUT_SAFE:
            dirty = _dirty_paths(r)
            if dirty:
                return {"dirty_files": dirty}
            strategy = pygit2.GIT_CHECKOUT_FORCE

        previous_head = r.head.name if not r.head_is_unborn else None

        try:
            r.create_reference_direct('HEAD', commit.id, force=True)
        except pygit2.GitError as exc:
            raise GitError(f"Cannot switch HEAD to {sha[:7]!r}: {exc}") from exc

        try:
            r.checkout_head(strategy=strategy)
        except pygit2.GitError as exc:
            _rollback_head(r, previous_head)
            raise DirtyWorkTreeError(
                f"Cannot update working tree for {sha[:7]!r}: {exc}",
            ) from exc

        remaining = _dirty_paths(r)
        if remaining:
            _rollback_head(r, previous_head)
            n = len(remaining)
            preview = ", ".join(remaining[:10])
            suffix = f" and {n - 10} more" if n > 10 else ""
            raise DirtyWorkTreeError(
                f"Checkout to {sha[:7]!r} did not fully update the working tree "
                f"({n} file(s) still differ): {preview}{suffix}",
            )
    return None


def rename_branch(
    repo: RepositoryManager | pygit2.Repository,
    old_name: str,
    new_name: str,
    force: bool = False,
) -> str:
    """Rename local branch ``old_name`` to ``new_name``. Returns ``new_name``.

    If ``force`` is ``False`` (default) the rename will fail when the
    target name already exists, matching ``git branch -m``'s default
    safety check. Pass ``force=True`` to overwrite a colliding branch
    (matches ``git branch -M``).
    """
    with unwrap(repo) as r:
        try:
            branch = r.lookup_branch(old_name)
        except (KeyError, ValueError) as exc:
            raise InvalidRefError(f"Unknown branch: {old_name!r}") from exc
        if branch is None:
            raise InvalidRefError(f"Unknown branch: {old_name!r}")
        try:
            branch.rename(new_name, force)
        except pygit2.AlreadyExistsError as exc:
            raise GitError(
                f"Branch {new_name!r} already exists (pass force=True to overwrite).",
            ) from exc
        except pygit2.GitError as exc:
            raise GitError(f"Failed to rename branch {old_name!r}: {exc}") from exc
    return new_name


# ----- merge / rebase / cherry-pick / revert --------------------------------


def merge_branch(
    repo: RepositoryManager | pygit2.Repository,
    source: str,
    target: str | None = None,
    message: str | None = None,
) -> bool:
    """Merge ``source`` (branch name, SHA, or ref) into the current HEAD.

    - If ``source`` is a fast-forward of ``HEAD``, the ref is simply moved
      and the function returns ``False``.
    - If the merge is up-to-date, returns ``False`` and does nothing.
    - Otherwise a three-way merge is performed and (when there are no
      conflicts) a merge commit is created with two parents: HEAD and
      ``source``. The function returns ``True``.

    Raises :class:`MergeConflictError` if conflicts were left in the
    index; in that case no merge commit is created — the caller is
    expected to resolve the conflicts and finish the merge.
    """
    with unwrap(repo) as r:
        if r.head_is_unborn:
            raise GitError("Cannot merge: HEAD is unborn.")
        try:
            source_oid = r.revparse_single(source).peel(pygit2.Commit).id
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(f"Unknown source: {source!r}") from exc
        analysis, _ = r.merge_analysis(source_oid)
        if analysis & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            return False
        if analysis & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD:
            try:
                target_name = target or r.head.shorthand
                ref = r.lookup_reference(f"refs/heads/{target_name}")
                ref.set_target(source_oid)
                r.head.set_target(source_oid)
                r.checkout(f"refs/heads/{target_name}", strategy=pygit2.GIT_CHECKOUT_SAFE)
            except pygit2.GitError as exc:
                raise GitError(f"Fast-forward merge failed: {exc}") from exc
            return False
        # Real three-way merge.
        head_oid = r.head.target
        try:
            r.merge(source_oid)
        except pygit2.GitError as exc:
            raise GitError(f"Merge failed: {exc}") from exc
        conflicts = _collect_conflicts(r)
        if conflicts:
            raise MergeConflictError(
                f"Merge of {source!r} produced conflicts in {len(conflicts)} file(s).",
                conflicting_paths=conflicts,
            )
        # Clean merge: write the index tree and create the merge commit.
        try:
            tree_oid = r.index.write_tree()
            target_name = target or r.head.shorthand
            merge_msg = message or f"Merge {source} into {target_name}"
            merge_oid = r.create_commit(
                "HEAD",
                _now_signature(),
                _now_signature(),
                merge_msg,
                tree_oid,
                [head_oid, source_oid],
            )
            ref = r.lookup_reference(f"refs/heads/{target_name}")
            ref.set_target(merge_oid)
        except pygit2.GitError as exc:
            raise GitError(f"Failed to create merge commit: {exc}") from exc
    return True


def _collect_conflicts(repo: pygit2.Repository) -> list[str]:
    """Return the list of paths currently in conflict in the index.

    Each ``repo.index.conflicts`` entry is a 3-tuple ``(ancestor, ours,
    theirs)`` of ``IndexEntry``; we use the "ours" entry's path because
    it is always present when there is a conflict.
    """
    conflicts: list[str] = []
    conflicts_attr = getattr(repo.index, "conflicts", None)
    if not conflicts_attr:
        return conflicts
    for entry in conflicts_attr:
        # ``entry`` is (ancestor, ours, theirs); pick the first non-None side.
        for side in entry:
            if side is not None:
                conflicts.append(side.path)
                break
    return conflicts


def rebase_branch(
    repo: RepositoryManager | pygit2.Repository,
    upstream: str,
) -> None:
    """Rebase the current branch onto ``upstream``.

    Implemented via the ``git rebase`` CLI because pygit2 1.x does not
    expose a high-level rebase. Requires ``git`` in ``PATH``.
    """
    with unwrap(repo) as r:
        workdir = r.workdir
    if workdir is None:
        raise GitError("Cannot rebase a bare repository.")
    git = shutil.which("git")
    if git is None:
        raise GitNotInstalledError("`git` CLI is not in PATH; rebase requires it.")
    try:
        completed = subprocess.run(
            [git, "rebase", upstream],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"Failed to spawn git: {exc}") from exc
    if completed.returncode != 0:
        if "conflict" in (completed.stderr + completed.stdout).lower():
            raise RebaseConflictError(
                f"Rebase stopped with conflicts. Resolve and run `git rebase --continue`.\n"
                f"{completed.stderr}",
            )
        raise GitError(f"Rebase failed: {completed.stderr.strip() or completed.stdout.strip()}")


# ----- merge / rebase state checks, abort, and finalize --------------------


def _git_dir(repo: pygit2.Repository) -> Path:
    """Return the path to the repository's git directory.

    For a normal repo this is ``<workdir>/.git``; for a bare repo it is
    the repo's own directory. ``pygit2.Repository.path`` is the git dir
    in both cases. Worktrees are not supported yet (Stage 5+).
    """
    return Path(repo.path)


def is_merge_in_progress(repo: RepositoryManager | pygit2.Repository) -> bool:
    """Return ``True`` if a merge is in progress (``.git/MERGE_HEAD`` exists)."""
    with unwrap(repo) as r:
        merge_head = _git_dir(r) / "MERGE_HEAD"
    return merge_head.is_file()


def is_rebase_in_progress(repo: RepositoryManager | pygit2.Repository) -> bool:
    """Return ``True`` if a rebase is in progress.

    Checks both ``.git/rebase-apply/`` (interactive rebase / ``git am``)
    and ``.git/rebase-merge/`` (non-interactive rebase) directories. A
    bare repo is never in a rebase.
    """
    with unwrap(repo) as r:
        if r.is_bare:
            return False
        gd = _git_dir(r)
    return (gd / "rebase-apply").is_dir() or (gd / "rebase-merge").is_dir()


def _run_git_in_workdir(
    repo: pygit2.Repository,
    args: list[str],
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo.workdir``; raise domain errors on failure."""
    workdir = repo.workdir
    if workdir is None:
        raise GitError("Cannot run git in a bare repository.")
    git = shutil.which("git")
    if git is None:
        raise GitNotInstalledError("`git` CLI is not in PATH.")
    try:
        return subprocess.run(
            [git, *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"Failed to spawn git: {exc}") from exc


def abort_merge(repo: RepositoryManager | pygit2.Repository) -> None:
    """Abort the in-progress merge via ``git merge --abort``.

    Raises :class:`GitError` if there is no merge in progress or the
    command fails. The caller is expected to verify
    :func:`is_merge_in_progress` first; calling ``abort_merge`` on a
    clean tree is an error.
    """
    with unwrap(repo) as r:
        if not is_merge_in_progress(r):
            raise GitError("No merge in progress.")
        completed = _run_git_in_workdir(r, ["merge", "--abort"])
    if completed.returncode != 0:
        raise GitError(
            f"git merge --abort failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )


def abort_rebase(repo: RepositoryManager | pygit2.Repository) -> None:
    """Abort the in-progress rebase via ``git rebase --abort``."""
    with unwrap(repo) as r:
        if not is_rebase_in_progress(r):
            raise GitError("No rebase in progress.")
        completed = _run_git_in_workdir(r, ["rebase", "--abort"])
    if completed.returncode != 0:
        raise GitError(
            f"git rebase --abort failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )


def complete_merge(
    repo: RepositoryManager | pygit2.Repository,
    source: str,
    target: str | None = None,
    message: str | None = None,
) -> str:
    """Finalize a resolved merge by creating the merge commit.

    Assumes the index has no more conflicts and contains the resolved
    tree. Returns the new merge commit's SHA.

    - ``source`` is the ref / branch / SHA that was being merged in
      (kept as the second parent of the merge commit).
    - ``target`` defaults to the current branch; the target ref is
      moved to the new commit (matches ``git merge`` semantics for the
      in-progress case).
    - ``message`` defaults to ``"Merge {source} into {target}"``.

    Raises :class:`GitError` if no merge is in progress. The MERGE_HEAD
    / MERGE_MSG state files are cleared on success so the repo leaves
    the in-progress state.
    """
    with unwrap(repo) as r:
        if r.head_is_unborn:
            raise GitError("Cannot complete a merge: HEAD is unborn.")
        if not is_merge_in_progress(r):
            raise GitError("No merge in progress.")
        try:
            source_oid = r.revparse_single(source).peel(pygit2.Commit).id
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(f"Unknown source: {source!r}") from exc
        conflicts = _collect_conflicts(r)
        if conflicts:
            raise MergeConflictError(
                f"Cannot complete merge: conflicts remain in {len(conflicts)} file(s).",
                conflicting_paths=conflicts,
            )
        head_oid = r.head.target
        try:
            tree_oid = r.index.write_tree()
            target_name = target or r.head.shorthand
            merge_msg = message or f"Merge {source} into {target_name}"
            merge_oid = r.create_commit(
                "HEAD",
                _now_signature(),
                _now_signature(),
                merge_msg,
                tree_oid,
                [head_oid, source_oid],
            )
            ref = r.lookup_reference(f"refs/heads/{target_name}")
            ref.set_target(merge_oid)
        except pygit2.GitError as exc:
            raise GitError(f"Failed to create merge commit: {exc}") from exc
        # Clear in-progress state so is_merge_in_progress() returns False
        # and the worktree / status refreshes to "clean".
        for state_file in ("MERGE_HEAD", "MERGE_MSG"):
            path = _git_dir(r) / state_file
            if path.is_file():
                try:
                    path.unlink()
                except OSError as exc:
                    raise GitError(
                        f"Failed to clear {state_file}: {exc}",
                    ) from exc
    return str(merge_oid)


def complete_rebase_continue(repo: RepositoryManager | pygit2.Repository) -> bool:
    """Continue an in-progress rebase after the user resolved conflicts.

    Runs ``git rebase --continue`` with ``GIT_EDITOR=true`` so the
    command does not block waiting for input — the original commit
    message is reused (``--continue`` does not change it).

    Returns ``True`` if the rebase is fully done, ``False`` if more
    commits still have to be applied (and the next step produced new
    conflicts).
    """
    with unwrap(repo) as r:
        if not is_rebase_in_progress(r):
            raise GitError("No rebase in progress.")
        workdir = r.workdir
    if workdir is None:
        raise GitError("Cannot continue a rebase in a bare repository.")
    git = shutil.which("git")
    if git is None:
        raise GitNotInstalledError("`git` CLI is not in PATH.")
    env = {**os.environ, "GIT_EDITOR": "true"}
    try:
        completed = subprocess.run(
            [git, "rebase", "--continue"],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    except OSError as exc:
        raise GitError(f"Failed to spawn git: {exc}") from exc
    if completed.returncode != 0:
        if "conflict" in (completed.stderr + completed.stdout).lower():
            # Not an error per se: there are more commits to apply and
            # the next one conflicted. Return False so the caller can
            # prompt for resolution again.
            return False
        raise GitError(
            f"git rebase --continue failed: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )
    with unwrap(repo) as r:
        return not is_rebase_in_progress(r)


def cherry_pick(
    repo: RepositoryManager | pygit2.Repository,
    sha: str,
) -> CommitInfo:
    """Cherry-pick ``sha`` onto the current HEAD."""
    with unwrap(repo) as r:
        if r.head_is_unborn:
            raise GitError("Cannot cherry-pick: HEAD is unborn.")
        try:
            commit = r.revparse_single(sha).peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(f"Unknown revision: {sha!r}") from exc
        try:
            r.cherrypick(commit.id)
        except pygit2.GitError as exc:
            raise GitError(f"Cherry-pick failed: {exc}") from exc
        conflicts = _collect_conflicts(r)
        if conflicts:
            raise MergeConflictError(
                f"Cherry-pick of {sha!r} produced conflicts.",
                conflicting_paths=conflicts,
            )
        head = r[r.head.target]
    return _to_commit_info(head)


def revert(
    repo: RepositoryManager | pygit2.Repository,
    sha: str,
) -> CommitInfo:
    """Revert the commit at ``sha`` (creates a new commit that undoes it)."""
    with unwrap(repo) as r:
        if r.head_is_unborn:
            raise GitError("Cannot revert: HEAD is unborn.")
        try:
            commit = r.revparse_single(sha).peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(f"Unknown revision: {sha!r}") from exc
        try:
            r.revert(commit)
        except pygit2.GitError as exc:
            raise GitError(f"Revert failed: {exc}") from exc
        conflicts = _collect_conflicts(r)
        if conflicts:
            raise MergeConflictError(
                f"Revert of {sha!r} produced conflicts.",
                conflicting_paths=conflicts,
            )
        head = r[r.head.target]
    return _to_commit_info(head)


def unstage_changes(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
) -> None:
    """Reset the index entry for ``path`` to match ``HEAD`` (``git reset HEAD -- <path>``).

    libgit2's :meth:`pygit2.Index.remove` drops a path from the index
    without restoring the ``HEAD`` entry, so a previously-modified
    file becomes "intent-to-delete" (``INDEX_DELETED``) — the opposite
    of what the UI wants. We shell out to ``git reset`` because
    pygit2 1.x has no high-level per-path "reset to HEAD" primitive.

    On an unborn HEAD (or when the path is not in the index at all),
    the call is a no-op so callers can blindly "unstage" anything.
    """
    with unwrap(repo) as r:
        if path not in r.index:
            # Path is not in the index — nothing to unstage.
            return
        if r.head_is_unborn:
            # No HEAD to reset to; just drop the staged entry.
            r.index.remove(path)
            r.index.write()
            return
        workdir = r.workdir
    if workdir is None:
        raise GitError("Cannot unstage in a bare repository.")
    git = shutil.which("git")
    if git is None:
        raise GitNotInstalledError("`git` CLI is not in PATH; unstage requires it.")
    try:
        completed = subprocess.run(
            [git, "reset", "HEAD", "--", path],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"Failed to spawn git: {exc}") from exc
    if completed.returncode != 0:
        raise GitError(
            f"Failed to unstage {path!r}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )


def reset(
    repo: RepositoryManager | pygit2.Repository,
    target: str,
    mode: str = "mixed",
) -> None:
    """Reset ``HEAD`` to ``target``.

    ``mode`` is one of ``"soft"`` (move HEAD only), ``"mixed"`` (HEAD +
    index, default), ``"hard"`` (HEAD + index + worktree).
    """
    modes = {
        "soft": pygit2.GIT_RESET_SOFT,
        "mixed": pygit2.GIT_RESET_MIXED,
        "hard": pygit2.GIT_RESET_HARD,
    }
    if mode not in modes:
        raise GitError(f"Invalid reset mode: {mode!r}. Use one of {sorted(modes)}.")
    with unwrap(repo) as r:
        try:
            commit = r.revparse_single(target).peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(f"Unknown target: {target!r}") from exc
        try:
            r.reset(commit.id, modes[mode])
        except pygit2.GitError as exc:
            raise GitError(f"Reset failed: {exc}") from exc


# ----- stash ----------------------------------------------------------------


def stash_push(
    repo: RepositoryManager | pygit2.Repository,
    message: str = "WIP",
    include_untracked: bool = True,
    paths: list[str] | None = None,
) -> str | None:
    """Stash uncommitted changes; returns the stash OID, or ``None`` if there was nothing to stash.

    ``include_untracked`` defaults to ``True`` (matches the common
    "stash everything I'm working on" expectation); pass ``False`` to
    only stash tracked-file changes, like ``git stash --keep-index``
    vs. plain ``git stash``.

    ``paths`` is an optional whitelist of working-tree paths to stash.
    When non-empty only the listed paths participate in the stash
    (matching ``git stash -- <path>`` semantics). The implementation
    passes the list straight to :meth:`pygit2.Repository.stash` which
    accepts a ``paths=`` keyword.
    """
    with unwrap(repo) as r:
        try:
            oid = r.stash(
                _now_signature(),
                message,
                include_untracked=include_untracked,
                paths=paths,
            )
        except (pygit2.GitError, KeyError) as exc:
            msg = str(exc).lower()
            if "nothing to stash" in msg:
                return None
            raise GitError(f"Stash failed: {exc}") from exc
    return str(oid) if oid else None


def stash_push_staged(
    repo: RepositoryManager | pygit2.Repository,
    message: str = "WIP staged",
) -> str | None:
    """Stash only the *staged* (index) changes, leaving the worktree alone.

    Implemented via the ``git stash push -- <path>`` CLI because
    :meth:`pygit2.Repository.stash` with ``paths=`` reverts *all*
    worktree changes (not just the listed paths) — it does not match
    the modern ``git stash push`` semantics, which only touches the
    listed paths and leaves the rest of the worktree intact.

    Returns ``None`` when there are no staged changes to stash (the
    CLI prints a "No local changes to save" message, which we
    detect). The CLI's stderr is surfaced as a :class:`GitError` on
    any other failure.
    """
    with unwrap(repo) as r:
        staged_paths: list[str] = []
        for path, flag in r.status().items():
            if flag & _STAGED_FLAGS:
                staged_paths.append(path)
        if not staged_paths:
            return None
        workdir = r.workdir
        if workdir is None:
            raise GitError("Cannot stash in a bare repository.")
    git = shutil.which("git")
    if git is None:
        raise GitNotInstalledError("`git` CLI is not in PATH; partial stash requires it.")
    args = ["stash", "push", "-m", message, "--"] + staged_paths
    try:
        completed = subprocess.run(
            [git, *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"Failed to spawn git: {exc}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        if "no local changes to save" in stderr.lower():
            return None
        raise GitError(f"git stash push failed: {stderr}")
    return stash_oid_at(repo, 0)


# Bitmask of pygit2 status flags that mean "the change is already
# recorded in the index" (i.e. would be picked up by the next commit).
# Kept module-private — callers that need a similar set should use
# :class:`src.viewmodels.commit_panel_viewmodel.CommitPanelViewModel`'s
# staged-files view instead of duplicating the bitmask.
_STAGED_FLAGS = (
    pygit2.GIT_STATUS_INDEX_NEW
    | pygit2.GIT_STATUS_INDEX_MODIFIED
    | pygit2.GIT_STATUS_INDEX_DELETED
    | pygit2.GIT_STATUS_INDEX_RENAMED
    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
)


def stash_pop(
    repo: RepositoryManager | pygit2.Repository,
    index: int = 0,
) -> None:
    """Apply and drop the stash at ``index`` (0 is the most recent)."""
    with unwrap(repo) as r:
        try:
            r.stash_pop(index)
        except (pygit2.GitError, KeyError) as exc:
            conflicts = _collect_conflicts(r)
            if conflicts:
                raise MergeConflictError(
                    "Stash pop produced conflicts.",
                    conflicting_paths=conflicts,
                ) from exc
            raise GitError(f"Stash pop failed: {exc}") from exc


def stash_apply(
    repo: RepositoryManager | pygit2.Repository,
    index: int = 0,
) -> None:
    """Apply the stash at ``index`` without removing it from the stash list.

    Mirrors :func:`stash_pop` for the conflict path — :class:`MergeConflictError`
    is raised when the application left conflicts in the index.
    """
    with unwrap(repo) as r:
        try:
            r.stash_apply(index)
        except (pygit2.GitError, KeyError) as exc:
            conflicts = _collect_conflicts(r)
            if conflicts:
                raise MergeConflictError(
                    "Stash apply produced conflicts.",
                    conflicting_paths=conflicts,
                ) from exc
            raise GitError(f"Stash apply failed: {exc}") from exc


def stash_drop(
    repo: RepositoryManager | pygit2.Repository,
    index: int = 0,
) -> None:
    """Remove the stash at ``index`` from the stash list (commit object is kept)."""
    with unwrap(repo) as r:
        try:
            r.stash_drop(index)
        except (pygit2.GitError, KeyError) as exc:
            raise GitError(f"Stash drop failed: {exc}") from exc


def stash_oid_at(
    repo: RepositoryManager | pygit2.Repository,
    index: int,
) -> str | None:
    """Return the OID of the stash commit at ``index`` (0 is most recent).

    Returns ``None`` if the index is out of range. The OID is needed by
    :func:`restore_stash` to put a dropped stash back: ``git stash store``
    requires the original commit SHA, not just the message.
    """
    with unwrap(repo) as r:
        try:
            for idx, entry in enumerate(r.listall_stashes()):
                if idx == index:
                    sha = entry.commit_id if hasattr(entry, "commit_id") else entry
                    return str(sha)
        except (pygit2.GitError, KeyError) as exc:
            raise GitError(f"Stash lookup failed: {exc}") from exc
    return None


def restore_stash(
    repo: RepositoryManager | pygit2.Repository,
    sha: str,
    message: str,
) -> None:
    """Restore a previously-dropped stash via ``git stash store``.

    pygit2 has no public API to recreate a stash entry from an existing
    commit — ``git stash`` only ever creates a *new* entry from the
    current worktree. The portable escape hatch is the low-level
    ``git stash store`` plumbing command, which writes a stash ref
    pointing at an existing commit object. We shell out to the
    ``git`` CLI for this, matching the pattern in
    :func:`_fetch_via_cli` / :func:`_push_via_cli`.

    The command raises :class:`GitNotInstalledError` when ``git`` is
    missing and :class:`GitError` on any other failure.
    """
    with unwrap(repo) as r:
        workdir = r.workdir
    if workdir is None:
        raise GitError("Cannot restore stash in a bare repository.")
    git = shutil.which("git")
    if git is None:
        raise GitNotInstalledError("`git` CLI is not in PATH; stash restore requires it.")
    try:
        completed = subprocess.run(
            [git, "stash", "store", "-m", message, sha],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(f"Failed to spawn git: {exc}") from exc
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        raise GitError(f"git stash store failed: {stderr}")


# ----- remotes: push / pull / fetch ----------------------------------------


def _wrap_remote_error(url: str, exc: pygit2.GitError) -> GitError:
    msg = str(exc).lower()
    if "auth" in msg or "credential" in msg:
        return AuthError(f"Authentication failed for {url}: {exc}")
    if "could not resolve" in msg or "network" in msg or "timed out" in msg or "tls" in msg:
        return NetworkError(f"Network error contacting {url}: {exc}")
    return GitError(f"Remote operation against {url} failed: {exc}")


# SCP-style ``user@host:path`` URLs (the common GitHub / GitLab SSH
# form, e.g. ``git@github.com:foo/bar.git``). We use this regex rather
# than ``urlsplit`` because the colon is not a port separator here.
_SCP_URL_RE = re.compile(r"^[\w.-]+@[\w.-]+:")
# ``ssh://...`` and ``git+ssh://...`` are the URL-style forms.
_SSH_SCHEME_RE = re.compile(r"^(ssh|git\+ssh)://", re.IGNORECASE)


def _url_needs_cli_fallback(url: str) -> bool:
    """Return ``True`` if ``url`` is an SSH URL pygit2 may not handle.

    pygit2 is built on libgit2; the prebuilt Windows wheel ships
    **without libssh2 support**, so any SSH URL surfaces as
    ``unsupported URL protocol``. The system ``git`` CLI uses the
    user's own SSH client (OpenSSH on PATH, ``~/.ssh/config``,
    ``SSH_AUTH_SOCK``) which works out of the box. We detect the
    common SSH forms and route those through the CLI; HTTPS URLs
    still go through pygit2 and benefit from its in-process transport.
    """
    if not url:
        return False
    return bool(_SCP_URL_RE.match(url) or _SSH_SCHEME_RE.match(url))


def _fetch_via_cli(
    repo: pygit2.Repository,
    remote_name: str,
    refspec: Sequence[str] | None,
) -> None:
    """Run ``git fetch <remote> [refspec...]`` in ``repo``'s workdir.

    Used as a fallback for SSH remotes when pygit2 cannot handle the
    transport. Translates non-zero exit codes into the same domain
    errors :func:`_wrap_remote_error` would have produced for a
    pygit2 failure so callers handle one error surface.
    """
    args: list[str] = ["fetch", remote_name]
    if refspec:
        if isinstance(refspec, str):
            args.append(refspec)
        else:
            args.extend(refspec)
    try:
        completed = _run_git_in_workdir(repo, args)
    except GitNotInstalledError:
        raise
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        # We don't have a pygit2.GitError to feed into _wrap_remote_error,
        # so the error-class detection is done on the CLI's stderr text.
        # ``git fetch`` reports auth failures as "Permission denied
        # (publickey)" and network problems as "Could not resolve
        # hostname" / "Connection refused" / "Connection timed out".
        low = stderr.lower()
        url = remote_name
        if "permission denied" in low or "publickey" in low or "authentication" in low:
            raise AuthError(
                f"Authentication failed for {url}: {stderr}",
            ) from None
        if (
            "could not resolve" in low
            or "connection refused" in low
            or "connection timed out" in low
            or "network" in low
            or "no route" in low
        ):
            raise NetworkError(
                f"Network error contacting {url}: {stderr}",
            ) from None
        raise GitError(f"git fetch {url} failed: {stderr}") from None


def add_remote(
    repo: RepositoryManager | pygit2.Repository,
    name: str,
    url: str,
) -> str:
    """Create a new remote called ``name`` pointing at ``url``.

    Returns the remote name. Translates :class:`pygit2.AlreadyExistsError`
    to :class:`GitError` and any other :class:`pygit2.GitError` to
    :class:`GitError` as well — the ViewModel does not need to
    distinguish "name taken" from "bad URL" because both are surfaced
    the same way to the user.
    """
    if not name or not name.strip():
        raise GitError("Remote name must not be empty.")
    if not url or not url.strip():
        raise GitError("Remote URL must not be empty.")
    with unwrap(repo) as r:
        try:
            remote = r.remotes.create(name, url)
        except (pygit2.AlreadyExistsError, ValueError) as exc:
            raise GitError(f"Remote {name!r} already exists.") from exc
        except pygit2.GitError as exc:
            raise GitError(f"Failed to add remote {name!r}: {exc}") from exc
    return remote.name


def remove_remote(
    repo: RepositoryManager | pygit2.Repository,
    name: str,
) -> None:
    """Delete the remote called ``name``.

    ``pygit2`` raises :class:`KeyError` when the remote does not exist;
    we re-raise as :class:`InvalidRefError` (the closest domain
    exception — a missing remote is conceptually a missing ref-like
    entry in the config). Other libgit2 errors become :class:`GitError`.
    """
    with unwrap(repo) as r:
        try:
            r.remotes.delete(name)
        except KeyError as exc:
            raise InvalidRefError(f"Unknown remote: {name!r}") from exc
        except pygit2.GitError as exc:
            raise GitError(f"Failed to remove remote {name!r}: {exc}") from exc


def list_remotes(repo: RepositoryManager | pygit2.Repository) -> list[RemoteInfo]:
    """Return a snapshot of every remote configured in ``repo``."""
    with unwrap(repo) as r:
        names = list(r.remotes.names())
        result: list[RemoteInfo] = []
        for remote_name in names:
            remote = r.remotes[remote_name]
            fetch_spec = ""
            push_spec = ""
            try:
                specs = list(remote.fetch_refspecs or ())
                if specs:
                    fetch_spec = "\n".join(specs)
            except (AttributeError, pygit2.GitError):
                fetch_spec = ""
            try:
                push_specs = list(getattr(remote, "push_refspecs", None) or ())
                if push_specs:
                    push_spec = "\n".join(push_specs)
            except (AttributeError, pygit2.GitError):
                push_spec = ""
            result.append(
                RemoteInfo(
                    name=remote.name,
                    url=remote.url or "",
                    fetch_refspec=fetch_spec,
                    push_refspec=push_spec,
                ),
            )
    return result


def push(
    repo: RepositoryManager | pygit2.Repository,
    remote_name: str = "origin",
    refspec: str | None = None,
    callbacks: pygit2.RemoteCallbacks | None = None,
) -> None:
    """Push ``refspec`` to ``remote_name`` (default: push ``HEAD``).

    SSH remotes (``git@host:path`` / ``ssh://...``) are routed through
    the system ``git`` CLI because prebuilt pygit2 wheels on Windows
    are built without libssh2 support. HTTPS / ``file://`` / ``git://``
    URLs go through :meth:`pygit2.Remote.push` as before.
    """
    spec = refspec or "HEAD"
    with unwrap(repo) as r:
        try:
            remote = r.remotes[remote_name]
        except KeyError as exc:
            raise InvalidRefError(f"Unknown remote: {remote_name!r}") from exc
        url = remote.url or ""
        if _url_needs_cli_fallback(url):
            _push_via_cli(r, remote_name, refspec)
            return
        try:
            remote.push([spec], callbacks=callbacks)
        except pygit2.GitError as exc:
            raise _wrap_remote_error(url, exc) from exc


def fetch(
    repo: RepositoryManager | pygit2.Repository,
    remote_name: str = "origin",
    refspec: Sequence[str] | None = None,
    callbacks: pygit2.RemoteCallbacks | None = None,
) -> None:
    """Fetch ``refspec`` from ``remote_name`` (default: fetch all configured refspecs).

    SSH remotes (``git@host:path`` / ``ssh://...``) are routed through
    the system ``git`` CLI because prebuilt pygit2 wheels on Windows
    are built without libssh2 support. HTTPS / ``file://`` / ``git://``
    URLs go through :meth:`pygit2.Remote.fetch` as before.
    """
    with unwrap(repo) as r:
        try:
            remote = r.remotes[remote_name]
        except KeyError as exc:
            raise InvalidRefError(f"Unknown remote: {remote_name!r}") from exc
        url = remote.url or ""
        if _url_needs_cli_fallback(url):
            _fetch_via_cli(r, remote_name, refspec)
            return
        try:
            remote.fetch(refspec, callbacks=callbacks)
        except pygit2.GitError as exc:
            raise _wrap_remote_error(url, exc) from exc


def _push_via_cli(
    repo: pygit2.Repository,
    remote_name: str,
    refspec: str | None,
) -> None:
    """Run ``git push <remote> [refspec]`` in ``repo``'s workdir.

    Used as a fallback for SSH remotes when pygit2 cannot handle the
    transport, following the same pattern as :func:`_fetch_via_cli`.
    """
    spec = refspec or "HEAD"
    args: list[str] = ["push", remote_name, spec]
    try:
        completed = _run_git_in_workdir(repo, args)
    except GitNotInstalledError:
        raise
    if completed.returncode != 0:
        stderr = (completed.stderr or completed.stdout or "").strip()
        low = stderr.lower()
        url = remote_name
        if "permission denied" in low or "publickey" in low or "authentication" in low:
            raise AuthError(
                f"Authentication failed for {url}: {stderr}",
            ) from None
        if "rejected" in low or "non-fast-forward" in low:
            raise GitError(f"Push to {url} rejected: {stderr}") from None
        if (
            "could not resolve" in low
            or "connection refused" in low
            or "connection timed out" in low
            or "network" in low
            or "no route" in low
        ):
            raise NetworkError(
                f"Network error contacting {url}: {stderr}",
            ) from None
        raise GitError(f"git push {url} failed: {stderr}") from None


def pull(
    repo: RepositoryManager | pygit2.Repository,
    remote_name: str = "origin",
    refspec: str | None = None,
    callbacks: pygit2.RemoteCallbacks | None = None,
) -> bool:
    """Fetch + merge ``remote_name``/``refspec`` into the current branch.

    Returns ``True`` for a real merge, ``False`` for up-to-date/fast-forward.
    """
    fetch(repo, remote_name, [refspec] if refspec else None, callbacks=callbacks)
    with unwrap(repo) as r:
        if r.head_is_unborn:
            return False
        upstream_name = r.head.shorthand
        try:
            upstream_ref = r.lookup_reference(f"refs/remotes/{remote_name}/{upstream_name}")
        except KeyError:
            return False
    return merge_branch(repo, str(upstream_ref.target))


def discard_changes(
    repo: RepositoryManager | pygit2.Repository,
) -> None:
    """Discard all uncommitted changes (index + workdir) by hard-resetting to HEAD.

    Raises :class:`GitError` if the repository is bare or HEAD is unborn.
    """
    with unwrap(repo) as r:
        if r.is_bare:
            raise GitError("Cannot discard changes in a bare repository.")
        if r.head_is_unborn:
            raise GitError("Cannot discard changes: HEAD has no commits yet.")
    reset(repo, "HEAD", "hard")


__all__ = [
    "abort_merge",
    "abort_rebase",
    "add_remote",
    "cherry_pick",
    "checkout_branch",
    "checkout_commit",
    "commit_changes",
    "complete_merge",
    "complete_rebase_continue",
    "create_branch",
    "delete_branch",
    "discard_changes",
    "fetch",
    "is_merge_in_progress",
    "is_rebase_in_progress",
    "list_remotes",
    "merge_branch",
    "pull",
    "push",
    "rebase_branch",
    "remove_remote",
    "rename_branch",
    "reset",
    "revert",
    "restore_stash",
    "stash_apply",
    "stash_drop",
    "stash_oid_at",
    "stash_pop",
    "stash_push",
    "stash_push_staged",
    "unstage_changes",
]
