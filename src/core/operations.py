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

import shutil
import subprocess
import time
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
from src.core.models import CommitInfo
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
) -> None:
    """Switch ``HEAD`` to local branch ``name``.

    ``strategy`` defaults to ``GIT_CHECKOUT_SAFE`` which refuses to
    overwrite local changes; pass ``GIT_CHECKOUT_FORCE`` to override.
    """
    with unwrap(repo) as r:
        try:
            r.checkout(f"refs/heads/{name}", strategy=strategy)
        except KeyError as exc:
            raise InvalidRefError(f"Unknown branch: {name!r}") from exc
        except pygit2.GitError as exc:
            raise DirtyWorkTreeError(
                f"Cannot check out {name!r}: worktree has uncommitted changes "
                "(pass force=True to override).",
            ) from exc


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
            r.revert(commit.id)
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
    "cherry_pick",
    "checkout_branch",
    "commit_changes",
    "create_branch",
    "delete_branch",
    "fetch",
    "merge_branch",
    "pull",
    "push",
    "rebase_branch",
    "reset",
    "revert",
    "stash_pop",
    "stash_push",
    "unstage_changes",
]
