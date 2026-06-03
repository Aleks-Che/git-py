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
    file list to the user. The working tree is NOT touched in that case
    — the pre-check happens before any files are modified.

    Implementation: HEAD is moved first (atomic ``set_head``), then the
    working tree is updated via ``checkout_head``. If ``checkout_head``
    fails, HEAD is rolled back to the previous branch so the repository
    is never left with HEAD on one branch and files from another.
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
            if previous_head is not None:
                try:
                    r.set_head(previous_head)
                except pygit2.GitError:
                    pass
            raise DirtyWorkTreeError(
                f"Cannot update working tree for {name!r}: {exc}",
            ) from exc
    return None


def _dirty_paths(repo: pygit2.Repository) -> list[str]:
    """Return the list of paths with uncommitted changes (index or worktree)."""
    return [p for p, _ in repo.status().items()]


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
) -> str | None:
    """Stash uncommitted changes; returns the stash OID, or ``None`` if there was nothing to stash.

    ``include_untracked`` defaults to ``True`` (matches the common
    "stash everything I'm working on" expectation); pass ``False`` to
    only stash tracked-file changes, like ``git stash --keep-index``
    vs. plain ``git stash``.
    """
    with unwrap(repo) as r:
        try:
            oid = r.stash(_now_signature(), message, include_untracked=include_untracked)
        except (pygit2.GitError, KeyError) as exc:
            msg = str(exc).lower()
            if "nothing to stash" in msg:
                return None
            raise GitError(f"Stash failed: {exc}") from exc
    return str(oid) if oid else None


def stash_pop(
    repo: RepositoryManager | pygit2.Repository,
    index: int = 0,
) -> None:
    """Apply and drop the stash at ``index`` (0 is the most recent)."""
    with unwrap(repo) as r:
        try:
            r.stash_pop(index)
        except pygit2.GitError as exc:
            conflicts = _collect_conflicts(r)
            if conflicts:
                raise MergeConflictError(
                    "Stash pop produced conflicts.",
                    conflicting_paths=conflicts,
                ) from exc
            raise GitError(f"Stash pop failed: {exc}") from exc


# ----- remotes: push / pull / fetch ----------------------------------------


def _wrap_remote_error(url: str, exc: pygit2.GitError) -> GitError:
    msg = str(exc).lower()
    if "auth" in msg or "credential" in msg:
        return AuthError(f"Authentication failed for {url}: {exc}")
    if "could not resolve" in msg or "network" in msg or "timed out" in msg or "tls" in msg:
        return NetworkError(f"Network error contacting {url}: {exc}")
    return GitError(f"Remote operation against {url} failed: {exc}")


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
            # libgit2 raises ``AlreadyExistsError`` in newer versions
            # and a bare ``ValueError`` (with error code GIT_EEXISTS)
            # in older ones — both signal "remote with this name exists".
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
        # ``list(r.remotes)`` yields ``Remote`` *objects*, not names.
        # Use ``.names()`` to get the string names so the snapshot is
        # independent of the underlying state.
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
                # ``push_refspecs`` is a newer libgit2 addition; treat
                # ``AttributeError`` as "unsupported" and fall back.
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
    """Push ``refspec`` to ``remote_name`` (default: push ``HEAD``)."""
    spec = refspec or "HEAD"
    with unwrap(repo) as r:
        try:
            remote = r.remotes[remote_name]
        except KeyError as exc:
            raise InvalidRefError(f"Unknown remote: {remote_name!r}") from exc
        try:
            remote.push([spec], callbacks=callbacks)
        except pygit2.GitError as exc:
            raise _wrap_remote_error(remote.url or remote_name, exc) from exc


def fetch(
    repo: RepositoryManager | pygit2.Repository,
    remote_name: str = "origin",
    refspec: Sequence[str] | None = None,
    callbacks: pygit2.RemoteCallbacks | None = None,
) -> None:
    """Fetch ``refspec`` from ``remote_name`` (default: fetch all configured refspecs)."""
    with unwrap(repo) as r:
        try:
            remote = r.remotes[remote_name]
        except KeyError as exc:
            raise InvalidRefError(f"Unknown remote: {remote_name!r}") from exc
        try:
            remote.fetch(refspec, callbacks=callbacks)
        except pygit2.GitError as exc:
            raise _wrap_remote_error(remote.url or remote_name, exc) from exc


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


__all__ = [
    "abort_merge",
    "abort_rebase",
    "add_remote",
    "cherry_pick",
    "checkout_branch",
    "commit_changes",
    "complete_merge",
    "complete_rebase_continue",
    "create_branch",
    "delete_branch",
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
    "stash_pop",
    "stash_push",
    "unstage_changes",
]
