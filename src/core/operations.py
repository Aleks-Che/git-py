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
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import pygit2

from src.core.diff_parser import (
    DiffLineType,
    ParsedDiffLine,
    diff_line_action_key,
    filter_staged_diff_lines,
    parse_diff_lines,
)
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


_FULL_DIFF_CONTEXT_LINES = 2**31 - 1


# Status flags that block a ``GIT_CHECKOUT_SAFE`` operation.  These
# reflect the *conflicting* subset of libgit2's status flags — changes
# that real ``git checkout`` would refuse to overwrite.  In particular
# ``GIT_STATUS_WT_NEW`` (untracked files in the worktree) and
# ``GIT_STATUS_IGNORED`` (gitignored files) are **not** in this mask:
# untracked files do NOT block ``git checkout`` (the user can safely
# ``git checkout`` between branches while leaving brand-new files in
# place — the file would only block a later ``git clean`` /
# ``git status`` would just report it).  Mirrors git's actual semantics
# rather than the stricter "everything that isn't CURRENT" reading.
_CONFLICTING_STATUS_FLAGS = (
    pygit2.GIT_STATUS_INDEX_NEW
    | pygit2.GIT_STATUS_INDEX_MODIFIED
    | pygit2.GIT_STATUS_INDEX_DELETED
    | pygit2.GIT_STATUS_INDEX_RENAMED
    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
    | pygit2.GIT_STATUS_WT_MODIFIED
    | pygit2.GIT_STATUS_WT_DELETED
    | pygit2.GIT_STATUS_WT_RENAMED
    | pygit2.GIT_STATUS_WT_TYPECHANGE
    # INTENTIONALLY EXCLUDED:
    # - GIT_STATUS_WT_NEW    (untracked — git checkout doesn't block)
    # - GIT_STATUS_IGNORED   (gitignored  — git checkout doesn't block)
    # - GIT_STATUS_CONFLICTED (merge in progress — caller already handles)
    # - GIT_STATUS_WT_UNREADABLE (worktree entry is unreadable; not a
    #   "tracked file modified" conflict per se; also excluded for
    #   parity with `git status` UX.)
)


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
        try:
            if stage_all:
                # ``Index.add_all()`` without a pathspec also stages WT_NEW
                # entries. Build an explicit pathspec so "stage all" means
                # all changes to paths Git already tracks, not every file in
                # the worktree. INDEX_NEW does not need adding again: it was
                # already staged explicitly by the caller.
                tracked_change_flags = _CONFLICTING_STATUS_FLAGS & ~pygit2.GIT_STATUS_INDEX_NEW
                excluded_flags = pygit2.GIT_STATUS_WT_NEW | pygit2.GIT_STATUS_IGNORED
                tracked_paths = [
                    path
                    for path, flags in r.status().items()
                    if flags & tracked_change_flags and not flags & excluded_flags
                ]
                if tracked_paths:
                    r.index.add_all(tracked_paths)
                r.index.write()
            tree_oid = r.index.write_tree()
            parents = [] if r.head_is_unborn else [r.head.target]
            commit_oid = r.create_commit(
                "HEAD",
                author,
                committer,
                message,
                tree_oid,
                parents,
            )
            return _to_commit_info(r[commit_oid])
        except (KeyError, TypeError, ValueError, pygit2.GitError) as exc:
            raise GitError(f"Commit failed: {exc}") from exc


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

        # Snapshot HEAD before any movement so we can roll back both
        # the symbolic/detached state AND the worktree on failure.
        was_unborn, previous_symbolic, previous_oid = _capture_head_state(r)

        try:
            r.set_head(refname)
        except pygit2.GitError as exc:
            raise GitError(f"Cannot switch HEAD to {name!r}: {exc}") from exc

        try:
            r.checkout_head(strategy=strategy)
        except pygit2.GitError as exc:
            _rollback_head_state(r, was_unborn, previous_symbolic, previous_oid)
            raise DirtyWorkTreeError(
                f"Cannot update working tree for {name!r}: {exc}",
            ) from exc

        remaining = _dirty_paths(r)
        if remaining:
            _rollback_head_state(r, was_unborn, previous_symbolic, previous_oid)
            n = len(remaining)
            preview = ", ".join(remaining[:10])
            suffix = f" and {n - 10} more" if n > 10 else ""
            raise DirtyWorkTreeError(
                f"Checkout to {name!r} did not fully update the working tree "
                f"({n} file(s) still differ): {preview}{suffix}",
            )
    return None


def _rollback_head(
    repo: pygit2.Repository,
    previous_head: str | None,
) -> None:
    """Restore HEAD to *previous_head* after a failed checkout.

    *previous_head* is the symbolic refname captured *before* the
    checkout was attempted (``r.head.name``); ``None`` means HEAD was
    unborn and there is nothing to roll back to.  The caller is
    responsible for also restoring the worktree (see
    :func:`_rollback_head_state`).
    """
    if previous_head is None:
        return
    try:
        repo.set_head(previous_head)
    except pygit2.GitError:
        pass


def _capture_head_state(repo: pygit2.Repository) -> tuple[bool, str | None, str | None]:
    """Snapshot the parts of HEAD we need to roll a checkout back.

    Returns ``(was_unborn, symbolic_name, oid_hex)``:

    - ``was_unborn``: ``True`` when HEAD points to no commit yet.
    - ``symbolic_name``: ``r.head.name`` (e.g. ``"refs/heads/main"``) when
      HEAD is symbolic; ``None`` when detached or unborn.
    - ``oid_hex``: ``str(r.head.target)`` when HEAD points at a commit;
      ``None`` when unborn.
    """
    if repo.head_is_unborn:
        return True, None, None
    symbolic = None if repo.head_is_detached else repo.head.name
    oid_hex = str(repo.head.target)
    return False, symbolic, oid_hex


def _rollback_head_state(
    repo: pygit2.Repository,
    was_unborn: bool,
    symbolic: str | None,
    oid_hex: str | None,
) -> None:
    """Best-effort restore of HEAD **and** the worktree.

    Used as the rollback for any checkout path that may have moved
    HEAD (branch checkout, detached checkout).  Performs three
    independent, exception-swallowing steps so a partial failure does
    not leave the caller worse off:

    1. Restore the symbolic HEAD reference (or set HEAD directly to the
       captured OID when HEAD was detached before the move).
    2. Re-read the index (``force=True``) so any stale in-memory state
       is dropped.
    3. ``checkout_head(FORCE)`` so the worktree matches the restored
       HEAD — this is the part the original code was missing and is the
       reason R1.2 calls for a worktree-level rollback.
    """
    if was_unborn:
        return  # nothing to restore to
    try:
        if symbolic is not None:
            repo.set_head(symbolic)
        elif oid_hex is not None:
            # HEAD was detached (or about to become so); restore by OID
            # rather than by refname — ``set_head`` expects a refname.
            repo.set_head(repo[oid_hex].id)
    except (KeyError, pygit2.GitError):
        pass
    try:
        repo.index.read(force=True)
    except (KeyError, pygit2.GitError, OSError):
        pass
    try:
        repo.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)
    except (KeyError, pygit2.GitError, OSError):
        pass  # best-effort — at worst the worktree lags HEAD


def _dirty_paths(repo: pygit2.Repository) -> list[str]:
    """Return the list of paths with CONFLICTING changes (blocks SAFE checkout).

    Excludes ``GIT_STATUS_WT_NEW`` (untracked files) and
    ``GIT_STATUS_IGNORED`` (gitignored files): real ``git checkout``
    does not refuse to switch branches when such files are present
    in the worktree.  Index/staged changes (``GIT_STATUS_INDEX_*``)
    and modifications/deletions of *tracked* worktree files
    (``GIT_STATUS_WT_MODIFIED/DELETED/RENAMED/TYPECHANGE``) are
    considered conflicts and remain in the returned list.
    """
    out: list[str] = []
    for path, flags in repo.status().items():
        if flags & _CONFLICTING_STATUS_FLAGS:
            out.append(path)
    return out


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

        # Snapshot HEAD before any movement.  ``_capture_head_state``
        # records *both* the symbolic refname and the underlying OID so
        # the rollback can restore a detached HEAD too (the previous
        # code only restored by refname, which silently did nothing
        # when HEAD was detached or unborn — see R1.2 finding C2).
        was_unborn, previous_symbolic, previous_oid = _capture_head_state(r)

        try:
            r.create_reference_direct('HEAD', commit.id, force=True)
        except pygit2.GitError as exc:
            raise GitError(f"Cannot switch HEAD to {sha[:7]!r}: {exc}") from exc

        try:
            r.checkout_head(strategy=strategy)
        except pygit2.GitError as exc:
            _rollback_head_state(r, was_unborn, previous_symbolic, previous_oid)
            raise DirtyWorkTreeError(
                f"Cannot update working tree for {sha[:7]!r}: {exc}",
            ) from exc

        remaining = _dirty_paths(r)
        if remaining:
            _rollback_head_state(r, was_unborn, previous_symbolic, previous_oid)
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
    no_ff: bool = False,
) -> bool:
    """Merge ``source`` (branch name, SHA, or ref) into the current HEAD.

    - If ``source`` is a fast-forward of ``HEAD`` **and** ``no_ff`` is
      ``False`` (the default), the ref is simply moved and the
      function returns ``False``.
    - If the merge is up-to-date, returns ``False`` and does nothing.
    - If ``source`` is a fast-forward **and** ``no_ff`` is ``True``, the
      merge is forced through the three-way path so a real merge
      commit with two parents is created (matches
      ``git merge --no-ff``).
    - Otherwise a three-way merge is performed and (when there are no
      conflicts) a merge commit is created with two parents: HEAD and
      ``source``. The function returns ``True``.

    The ``no_ff`` flag is what the user invokes through the UI: when
    a branch is dragged onto another, or when the left-panel
    "Merge X into current…" / drag-and-drop menu is used, the user
    expects to see a merge commit in the history even if the
    branches haven't diverged — that's the only way the merge is
    visible in the graph. Without ``no_ff``, fast-forwards silently
    move the ref and the user sees "no commit" on the target
    branch.

    Raises :class:`MergeConflictError` if conflicts were left in the
    index; in that case no merge commit is created — the caller is
    expected to resolve the conflicts and finish the merge.
    """
    with unwrap(repo) as r:
        if r.head_is_unborn:
            raise GitError("Cannot merge: HEAD is unborn.")
        current_branch = None if r.head_is_detached else r.head.shorthand
        target_branch = target or current_branch
        if target_branch is None:
            raise GitError("merge_branch requires a target branch (HEAD is detached).")
        if current_branch is None:
            raise GitError("Cannot merge with a detached HEAD; check out a local branch first.")

        try:
            source_oid = r.revparse_single(source).peel(pygit2.Commit).id
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise InvalidRefError(
                f"Unknown source: {source!r}. "
                f"If this is a remote branch, fetch it first "
                f"(Push/Pull toolbar → Fetch, or right-click the remote branch in the left panel).",
            ) from exc

        target_name = target_branch
        target_ref_name = f"refs/heads/{target_name}"
        try:
            r.lookup_reference(target_ref_name)
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise GitError(f"Unknown target branch: {target_name!r}.") from exc

        # ``merge_analysis`` always analyses against HEAD.  A caller may
        # explicitly name another local branch (for example, merging X into
        # Y while currently on Z), so move HEAD to that branch before doing
        # any analysis or writing the merge commit.
        previous_head_name = None if r.head_is_detached else r.head.name
        previous_head_oid = None if r.head_is_unborn else str(r.head.target)
        if current_branch != target_name:
            try:
                # ``set_head`` alone changes the symbolic ref but does not
                # reliably refresh the worktree when the target ref has a
                # different tree.  Use checkout(refname) so both HEAD and
                # index/worktree are moved atomically.
                r.checkout(target_ref_name, strategy=pygit2.GIT_CHECKOUT_SAFE)
                r.index.read(force=True)
            except pygit2.GitError as exc:
                _rollback_head(r, previous_head_name)
                raise GitError(
                    f"Cannot check out merge target {target_name!r}: {exc}",
                ) from exc

        analysis, _ = r.merge_analysis(source_oid)
        if analysis & pygit2.GIT_MERGE_ANALYSIS_UP_TO_DATE:
            return False
        is_fastforward = bool(analysis & pygit2.GIT_MERGE_ANALYSIS_FASTFORWARD)
        if is_fastforward and not no_ff:
            ref = r.lookup_reference(target_ref_name)
            previous_oid = str(ref.target)
            try:
                # Preflight the worktree before moving refs.  In particular,
                # this catches SAFE refusal for a modified tracked file while
                # the old target tip is still intact.
                r.checkout_tree(r[source_oid].tree, strategy=pygit2.GIT_CHECKOUT_SAFE)
                ref.set_target(source_oid)
                r.head.set_target(source_oid)
                r.checkout(target_ref_name, strategy=pygit2.GIT_CHECKOUT_SAFE)
            except pygit2.GitError as exc:
                # The ref is moved before checkout so that checkout sees the
                # new tree.  Restore both the ref and the original HEAD when
                # checkout refuses the worktree; otherwise a failed merge
                # would silently lose the old target tip.
                try:
                    ref.set_target(previous_oid)
                except Exception:
                    pass
                try:
                    if previous_head_name is not None:
                        r.set_head(previous_head_name)
                    elif previous_head_oid is not None:
                        r.create_reference_direct("HEAD", previous_head_oid, force=True)
                except Exception:
                    pass
                raise GitError(f"Fast-forward merge failed: {exc}") from exc
            return False
        # Real three-way merge — either because the branches diverged
        # or because the caller asked for ``no_ff`` and forced a
        # merge commit even on a fast-forwardable history.
        head_oid = r.head.target
        if is_fastforward:
            # ``r.merge`` is a no-op on a fast-forward: the working
            # tree already matches ``source_oid``. The merge commit
            # carries the *source*'s tree (which is what a fast-
            # forward would have done), with two parents so it
            # shows up in the graph as a real merge. Fast-forward
            # trees are clean by definition (no conflicts to
            # resolve), so we can skip the conflict check.
            try:
                tree_oid = r[source_oid].tree.id
            except pygit2.GitError as exc:
                raise GitError(f"Fast-forward no-ff merge failed: {exc}") from exc
        else:
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
            try:
                tree_oid = r.index.write_tree()
            except pygit2.GitError as exc:
                raise GitError(f"Failed to write merge tree: {exc}") from exc
        # Clean merge: create the merge commit and point the
        # target ref at it. The two-parent shape is what makes
        # the commit a real merge in the history — a single-parent
        # commit would just look like a fast-forward.
        try:
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

    Detached HEAD is rejected up front (R1.3 / finding C3): the CLI's
    ``git rebase`` would either fail with a confusing message or, worse,
    silently re-attach HEAD to whatever ref happens to be checked out
    (``HEAD`` is not a branch refname). Switch to a branch first.
    """
    with unwrap(repo) as r:
        if r.head_is_unborn:
            raise GitError("Cannot rebase: HEAD is unborn.")
        if r.head_is_detached:
            raise GitError(
                "Cannot rebase in detached HEAD state. "
                "Switch to a branch first.",
            )
        if r.workdir is None:
            raise GitError("Cannot rebase a bare repository.")
        try:
            completed = _run_git_in_workdir(
                r,
                ["rebase", upstream],
                timeout=300.0,
            )
        except GitError as exc:
            text = str(exc)
            if "conflict" in text.lower():
                raise RebaseConflictError(
                    "Rebase stopped with conflicts. Resolve and run `git rebase --continue`.\n"
                    f"{text}",
                ) from exc
            raise
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


def _refresh_index(repo: pygit2.Repository) -> None:
    """Reload the in-memory index after an external Git mutation."""
    repo.index.read(force=True)


def _run_git_in_workdir(
    repo: pygit2.Repository,
    args: list[str],
    *,
    timeout: float = 60.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo.workdir``; raise domain errors on failure.

    *timeout* (seconds) guards against a hung ``git`` process — the
    :class:`subprocess.TimeoutExpired` exception is surfaced as a
    :class:`GitError` so the UI always gets a clean domain error
    instead of a raw stack trace.
    """
    workdir = repo.workdir
    if workdir is None:
        raise GitError("Cannot run git in a bare repository.")
    git = shutil.which("git")
    if git is None:
        raise GitNotInstalledError("`git` CLI is not in PATH.")
    try:
        completed = subprocess.run(
            [git, *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
        _refresh_index(repo)
        return completed
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"git {' '.join(args)} timed out after {timeout:.0f}s",
        ) from exc
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
            raise InvalidRefError(
                f"Unknown source: {source!r}. "
                f"If this is a remote branch, fetch it first "
                f"(Push/Pull toolbar → Fetch, or right-click the remote branch in the left panel).",
            ) from exc
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
        if r.workdir is None:
            raise GitError("Cannot continue a rebase in a bare repository.")
        env = {**os.environ, "GIT_EDITOR": "true"}
        completed = _run_git_in_workdir(
            r,
            ["rebase", "--continue"],
            timeout=300.0,
            env=env,
        )
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


def snapshot_index_entry(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
) -> tuple[str, int] | None:
    """Return the index OID and mode for ``path``, or ``None`` when absent."""
    with unwrap(repo) as r:
        try:
            entry = r.index[path]
        except KeyError:
            return None
        return str(entry.id), int(entry.mode)


def restore_index_entry(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
    snapshot: tuple[str, int] | None,
) -> None:
    """Restore one index entry from :func:`snapshot_index_entry`."""
    with unwrap(repo) as r:
        try:
            if snapshot is None:
                if path in r.index:
                    r.index.remove(path)
            else:
                oid, mode = snapshot
                entry = pygit2.IndexEntry(
                    path,
                    pygit2.Oid(hex=oid),
                    pygit2.enums.FileMode(mode),
                )
                r.index.add(entry)
            r.index.write()
        except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
            raise GitError(f"Failed to restore index entry for {path!r}: {exc}") from exc


def stage_diff_line(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
    line: ParsedDiffLine,
) -> None:
    """Apply one HEAD-to-worktree diff row to the index."""
    _apply_diff_line(repo, path, line, unstage=False)


def unstage_diff_line(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
    line: ParsedDiffLine,
) -> None:
    """Reverse one HEAD-to-index diff row in the index."""
    _apply_diff_line(repo, path, line, unstage=True)


def _apply_diff_line(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
    line: ParsedDiffLine,
    *,
    unstage: bool,
) -> None:
    if line.line_type not in (DiffLineType.ADDITION, DiffLineType.DELETION):
        raise GitError("Only added or deleted diff lines can be staged individually.")
    with unwrap(repo) as r:
        if r.is_bare:
            raise GitError("Cannot stage individual lines in a bare repository.")
        if r.head_is_unborn:
            raise GitError("Cannot stage individual lines before the first commit.")
        status = r.status().get(path, pygit2.GIT_STATUS_CURRENT)
        required = (
            pygit2.GIT_STATUS_INDEX_MODIFIED
            if unstage
            else pygit2.GIT_STATUS_WT_MODIFIED
        )
        if not status & required:
            side = "staged" if unstage else "unstaged"
            raise GitError(f"{path!r} has no {side} text modification for this line.")
        try:
            index_entry = r.index[path]
            staged_patch = _patch_text_for_path(
                r.diff("HEAD", cached=True, context_lines=3),
                path,
            )
            staged_lines = parse_diff_lines(staged_patch)
            source_patch = (
                staged_patch
                if unstage
                else _patch_text_for_path(r.diff("HEAD", context_lines=3), path)
            )
            source_lines = parse_diff_lines(source_patch)
            if unstage:
                if line not in source_lines:
                    raise GitError(f"The diff for {path!r} changed; select the line again.")
            else:
                resolved_line = (
                    line
                    if line in source_lines
                    else _resolve_stage_source_line(
                        r,
                        path,
                        line,
                        staged_patch,
                        source_patch,
                    )
                )
                if resolved_line is None:
                    raise GitError(f"The diff for {path!r} changed; select the line again.")
                line = resolved_line
            if not unstage:
                staged_counts = Counter(
                    diff_line_action_key(item)
                    for item in staged_lines
                    if item.line_type in (DiffLineType.ADDITION, DiffLineType.DELETION)
                )
                source_counts = Counter(
                    diff_line_action_key(item)
                    for item in source_lines
                    if item.line_type in (DiffLineType.ADDITION, DiffLineType.DELETION)
                )
                key = diff_line_action_key(line)
                if staged_counts[key] >= source_counts[key]:
                    raise GitError("The selected diff line is already staged.")
            index_data = r[index_entry.id].data
            head_data = r.revparse_single(f"HEAD:{path}").data
            worktree_oid = r.create_blob_fromworkdir(path)
            worktree_data = r[worktree_oid].data
            index_lines = index_data.splitlines(keepends=True)
            head_lines = head_data.splitlines(keepends=True)
            worktree_lines = worktree_data.splitlines(keepends=True)
            if unstage:
                _unstage_line_content(index_lines, head_lines, line)
            else:
                _stage_line_content(
                    index_lines,
                    head_lines,
                    worktree_lines,
                    staged_lines,
                    line,
                )
            blob_oid = r.create_blob(b"".join(index_lines))
            r.index.add(pygit2.IndexEntry(path, blob_oid, index_entry.mode))
            r.index.write()
        except GitError:
            raise
        except (pygit2.GitError, KeyError, ValueError, IndexError, OSError) as exc:
            verb = "unstage" if unstage else "stage"
            raise GitError(f"Failed to {verb} line in {path!r}: {exc}") from exc


def _resolve_stage_source_line(
    repo: pygit2.Repository,
    path: str,
    line: ParsedDiffLine,
    staged_patch: str,
    source_patch: str,
) -> ParsedDiffLine | None:
    resolved = _resolve_filtered_diff_line(line, source_patch, staged_patch)
    if resolved is not None:
        return resolved
    full_source_patch = _patch_text_for_path(
        repo.diff("HEAD", context_lines=_FULL_DIFF_CONTEXT_LINES),
        path,
    )
    return _resolve_filtered_diff_line(line, full_source_patch, staged_patch)


def _resolve_filtered_diff_line(
    line: ParsedDiffLine,
    source_patch: str,
    staged_patch: str,
) -> ParsedDiffLine | None:
    filtered_text, source_line_info = filter_staged_diff_lines(
        source_patch,
        staged_patch,
    )
    visible_line_info = parse_diff_lines(filtered_text)
    for visible, source in zip(visible_line_info, source_line_info, strict=True):
        if visible == line:
            return source
    return None


def _stage_line_content(
    index_lines: list[bytes],
    head_lines: list[bytes],
    worktree_lines: list[bytes],
    staged_lines: list[ParsedDiffLine],
    line: ParsedDiffLine,
) -> None:
    additions = [
        item
        for item in staged_lines
        if item.line_type == DiffLineType.ADDITION
        and item.old_line_number is not None
    ]
    deletions = [
        item
        for item in staged_lines
        if item.line_type == DiffLineType.DELETION
        and item.old_line_number is not None
    ]
    if line.line_type == DiffLineType.ADDITION:
        if line.old_line_number is None:
            raise GitError("The selected diff line has no insertion position.")
        source = _existing_line(worktree_lines, line.new_line_number)
        offset = line.old_line_number - 1
        offset += sum(
            1 for item in additions
            if item.old_line_number <= line.old_line_number
        )
        offset -= sum(
            1 for item in deletions
            if item.old_line_number < line.old_line_number
        )
        index_lines.insert(_validated_insertion_offset(index_lines, offset), source)
        return
    if line.old_line_number is None:
        raise GitError("The selected diff line has no source position.")
    source = _existing_line(head_lines, line.old_line_number)
    offset = line.old_line_number - 1
    offset += sum(
        1 for item in additions
        if item.old_line_number <= line.old_line_number
    )
    offset -= sum(
        1 for item in deletions
        if item.old_line_number < line.old_line_number
    )
    offset = _validated_existing_offset(index_lines, offset)
    if index_lines[offset] != source:
        raise GitError("The selected diff line no longer maps to the index content.")
    index_lines.pop(offset)


def _unstage_line_content(
    index_lines: list[bytes],
    head_lines: list[bytes],
    line: ParsedDiffLine,
) -> None:
    if line.line_type == DiffLineType.ADDITION:
        index_lines.pop(_existing_offset(index_lines, line.new_line_number))
        return
    source = _existing_line(head_lines, line.old_line_number)
    index_lines.insert(_insertion_offset(index_lines, line.new_line_number), source)


def _validated_existing_offset(lines: list[bytes], offset: int) -> int:
    if offset < 0 or offset >= len(lines):
        raise GitError("The selected diff line no longer maps to the file content.")
    return offset


def _validated_insertion_offset(lines: list[bytes], offset: int) -> int:
    if offset < 0 or offset > len(lines):
        raise GitError("The selected diff line no longer maps to the file content.")
    return offset


def _existing_line(lines: list[bytes], line_number: int | None) -> bytes:
    return lines[_existing_offset(lines, line_number)]


def _existing_offset(lines: list[bytes], line_number: int | None) -> int:
    if line_number is None or line_number < 1 or line_number > len(lines):
        raise GitError("The selected diff line no longer maps to the file content.")
    return line_number - 1


def _insertion_offset(lines: list[bytes], line_number: int | None) -> int:
    if line_number is None:
        raise GitError("The selected diff line has no insertion position.")
    offset = max(0, line_number - 1)
    if offset > len(lines):
        raise GitError("The selected diff line no longer maps to the file content.")
    return offset


def _patch_text_for_path(diff: pygit2.Diff, path: str) -> str:
    pieces: list[str] = []
    for patch in diff:
        delta = patch.delta
        if delta.new_file.path == path or delta.old_file.path == path:
            pieces.append(patch.text or "")
    return "".join(pieces)


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

    For files staged for deletion (``INDEX_DELETED`` — in HEAD but not
    in index) the function restores the HEAD entry back into the index,
    moving the file back to the unstaged list.

    On an unborn HEAD (or when the path is not in HEAD at all), the
    call is a no-op so callers can blindly "unstage" anything.
    """
    with unwrap(repo) as r:
        if path in r.index:
            if r.head_is_unborn:
                # No HEAD to reset to; just drop the staged entry.
                r.index.remove(path)
                r.index.write()
                return
        else:
            # Path is not in the index — could be INDEX_DELETED
            # (staged for deletion, in HEAD but not in index).
            if r.head_is_unborn:
                return  # No HEAD to restore from.
            try:
                r.revparse_single(f"HEAD:{path}")
            except (KeyError, pygit2.GitError, ValueError):
                return  # Not in HEAD either — nothing to unstage.
        workdir = r.workdir
    if workdir is None:
        raise GitError("Cannot unstage in a bare repository.")
    completed = _run_git_in_workdir(r, ["reset", "HEAD", "--", path], timeout=30.0)
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
    args = ["stash", "push", "-m", message, "--"] + staged_paths
    completed = _run_git_in_workdir(r, args, timeout=30.0)
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


def find_stash_index_by_oid(
    repo: RepositoryManager | pygit2.Repository,
    oid: str,
) -> int | None:
    """Return the current stash-list index for ``oid``, or ``None`` if absent."""
    wanted = oid.lower()
    with unwrap(repo) as r:
        try:
            for index, entry in enumerate(r.listall_stashes()):
                entry_oid = entry.commit_id if hasattr(entry, "commit_id") else entry
                if str(entry_oid).lower() == wanted:
                    return index
        except (pygit2.GitError, KeyError) as exc:
            raise GitError(f"Stash lookup failed: {exc}") from exc
    return None


def snapshot_stash_apply_state(
    repo: RepositoryManager | pygit2.Repository,
    index: int,
) -> tuple[dict[str, bytes], set[str], dict[str, str]]:
    """Capture worktree and index paths that applying a stash may change.

    The returned tuple contains existing worktree file contents, paths that
    are currently absent, and serialised index entries. Limiting the
    snapshot to paths represented by the stash is important: undoing an
    apply must not overwrite unrelated dirty files that existed beforehand.
    An empty index-entry string records that a path was absent from the
    pre-apply index.
    """
    with unwrap(repo) as r:
        workdir = r.workdir
        if workdir is None:
            raise GitError("Cannot apply a stash in a bare repository.")
        try:
            oid = stash_oid_at(r, index)
            if oid is None:
                raise GitError(f"Stash apply failed: stash index {index} was not found.")
            stash_commit = r[pygit2.Oid(hex=oid)].peel(pygit2.Commit)
            if not stash_commit.parents:
                raise GitError(f"Stash apply failed: {oid} is not a valid stash commit.")

            paths: set[str] = set()
            for patch in r.diff(stash_commit.parents[0].tree, stash_commit.tree):
                old_path = patch.delta.old_file.path
                new_path = patch.delta.new_file.path
                if old_path:
                    paths.add(old_path)
                if new_path:
                    paths.add(new_path)

            # The stash's second parent is the pre-stash index. Include
            # those paths as well: staged-only changes can be identical in
            # the base-to-worktree diff and would otherwise be missed.
            if len(stash_commit.parents) >= 2:
                for patch in r.diff(
                    stash_commit.parents[0].tree,
                    stash_commit.parents[1].tree,
                ):
                    old_path = patch.delta.old_file.path
                    new_path = patch.delta.new_file.path
                    if old_path:
                        paths.add(old_path)
                    if new_path:
                        paths.add(new_path)

            # A stash made with include_untracked=True stores untracked files
            # in the third parent rather than in the stash commit's own tree.
            if len(stash_commit.parents) >= 3:
                _collect_tree_paths(r, stash_commit.parents[2].tree, paths)

            root = Path(workdir)
            path_contents: dict[str, bytes] = {}
            missing_paths: set[str] = set()
            index_entries: dict[str, str] = {}
            r.index.read(force=True)
            for path in paths:
                full_path = root / path
                if full_path.exists() or full_path.is_symlink():
                    path_contents[path] = full_path.read_bytes()
                else:
                    missing_paths.add(path)
                try:
                    entry = r.index[path]
                except KeyError:
                    index_entries[path] = ""
                else:
                    index_entries[path] = f"{entry.id}:{int(entry.mode)}"
        except GitError:
            raise
        except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
            raise GitError(f"Failed to snapshot stash apply state: {exc}") from exc
    return path_contents, missing_paths, index_entries


def _collect_tree_paths(
    repo: pygit2.Repository,
    tree: pygit2.Tree,
    paths: set[str],
    prefix: str = "",
) -> None:
    """Add every non-tree path below ``tree`` to ``paths``."""
    for entry in tree:
        path = f"{prefix}/{entry.name}" if prefix else entry.name
        obj = repo[entry.id]
        if isinstance(obj, pygit2.Tree):
            _collect_tree_paths(repo, obj, paths, path)
        else:
            paths.add(path)


def restore_stash_apply_state(
    repo: RepositoryManager | pygit2.Repository,
    path_contents: dict[str, bytes],
    missing_paths: set[str],
    index_entries: dict[str, str],
) -> None:
    """Restore a snapshot returned by :func:`snapshot_stash_apply_state`."""
    with unwrap(repo) as r:
        workdir = r.workdir
        if workdir is None:
            raise GitError("Cannot restore stash state in a bare repository.")
        root = Path(workdir)
        try:
            for path, content in path_contents.items():
                full_path = root / path
                if full_path.is_dir() and not full_path.is_symlink():
                    raise GitError(
                        f"Cannot restore {path!r}: a directory now exists at that path."
                    )
                if full_path.is_symlink():
                    full_path.unlink()
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_bytes(content)

            for path in missing_paths:
                full_path = root / path
                if full_path.is_dir() and not full_path.is_symlink():
                    raise GitError(
                        f"Cannot remove applied path {path!r}: it is now a directory."
                    )
                if full_path.exists() or full_path.is_symlink():
                    full_path.unlink()

            r.index.read(force=True)
            for path, serialised_entry in index_entries.items():
                if not serialised_entry:
                    if path in r.index:
                        r.index.remove(path)
                    continue
                oid, mode = serialised_entry.rsplit(":", 1)
                r.index.add(
                    pygit2.IndexEntry(
                        path,
                        pygit2.Oid(hex=oid),
                        pygit2.enums.FileMode(int(mode)),
                    )
                )
            r.index.write()
        except GitError:
            raise
        except (pygit2.GitError, KeyError, ValueError, OSError) as exc:
            raise GitError(f"Failed to restore pre-stash worktree state: {exc}") from exc


def restore_stash(
    repo: RepositoryManager | pygit2.Repository,
    sha: str,
    message: str = "WIP (restored)",
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
            timeout=30.0,
        )
        _refresh_index(r)
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            "git stash store timed out after 30s",
        ) from exc
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


# ----- tags ----------------------------------------------------------------


def create_tag(
    repo: RepositoryManager | pygit2.Repository,
    name: str,
    target_sha: str,
    message: str | None = None,
    tagger: pygit2.Signature | None = None,
) -> None:
    """Create a tag — lightweight (``message is None``) or annotated."""
    with unwrap(repo) as r:
        try:
            target = r.get(pygit2.Oid(hex=target_sha))
        except (KeyError, ValueError) as exc:
            raise InvalidRefError(f"Cannot resolve {target_sha[:7]!r}") from exc
        if target is None:
            raise InvalidRefError(f"Cannot resolve {target_sha[:7]!r}")
        try:
            if message:
                tagger = tagger or _now_signature()
                r.create_tag(name, target.oid, pygit2.GIT_OBJECT_COMMIT, tagger, message)
            else:
                r.create_tag(name, target.oid, pygit2.GIT_OBJECT_COMMIT)
        except pygit2.GitError as exc:
            kind = "annotated tag" if message else "lightweight tag"
            raise GitError(f"Failed to create {kind} {name!r}: {exc}") from exc


def delete_tag(
    repo: RepositoryManager | pygit2.Repository,
    name: str,
) -> None:
    """Delete a tag by its *name* (without ``refs/tags/`` prefix)."""
    with unwrap(repo) as r:
        ref_name = f"refs/tags/{name}"
        try:
            ref = r.lookup_reference(ref_name)
        except KeyError as exc:
            raise InvalidRefError(f"Unknown tag: {name!r}") from exc
        try:
            ref.delete()
        except pygit2.GitError as exc:
            raise GitError(f"Failed to delete tag {name!r}: {exc}") from exc


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


def discard_file(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
) -> None:
    """Discard the uncommitted changes of a single file, restoring it from HEAD.

    Works for both tracked (``git checkout HEAD -- <path>``) and untracked
    (delete from disk + index) files. Raises :class:`GitError` on failure.
    """
    with unwrap(repo) as r:
        if r.is_bare or r.head_is_unborn:
            if r.is_bare:
                raise GitError("Cannot discard a file in a bare repository.")
            raise GitError("Cannot discard a file: HEAD has no commits yet.")
        workdir = r.workdir
        if workdir is None:
            raise GitError("Cannot discard a file: no working directory.")
        in_index = path in r.index
    full_path = Path(workdir) / path
    if not full_path.exists() and not in_index:
        return  # nothing to discard
    completed = _run_git_in_workdir(r, ["checkout", "HEAD", "--", path], timeout=30.0)
    if completed.returncode != 0:
        raise GitError(
            f"Failed to discard {path!r}: "
            f"{completed.stderr.strip() or completed.stdout.strip()}",
        )


def add_to_gitignore(
    repo: RepositoryManager | pygit2.Repository,
    pattern: str,
) -> None:
    """Append a pattern to the repository's ``.gitignore`` file.

    Creates the ``.gitignore`` file if it does not exist. The pattern is
    added on a new line. Raises :class:`GitError` on I/O errors.
    """
    with unwrap(repo) as r:
        workdir = r.workdir
        if workdir is None:
            raise GitError("Cannot write .gitignore in a bare repository.")
    gitignore_path = Path(workdir) / ".gitignore"
    try:
        gitignore_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if gitignore_path.exists():
            existing = gitignore_path.read_text(encoding="utf-8").splitlines()
        if pattern in existing:
            return  # already ignored
        with gitignore_path.open("a", encoding="utf-8") as f:
            f.write(pattern + "\n")
    except OSError as exc:
        raise GitError(f"Failed to write .gitignore: {exc}") from exc


def delete_file_from_disk(
    repo: RepositoryManager | pygit2.Repository,
    path: str,
) -> None:
    """Delete a file from disk. Does not stage the deletion.

    Raises :class:`GitError` on I/O errors or if the file does not exist.
    """
    with unwrap(repo) as r:
        workdir = r.workdir
        if workdir is None:
            raise GitError("Cannot delete a file in a bare repository.")
    full_path = Path(workdir) / path
    if not full_path.exists():
        raise GitError(f"File not found: {path!r}")
    try:
        if full_path.is_dir():
            shutil.rmtree(full_path)
        else:
            full_path.unlink()
    except OSError as exc:
        raise GitError(f"Failed to delete {path!r}: {exc}") from exc


def apply_file_from_stash(
    repo: RepositoryManager | pygit2.Repository,
    stash_sha: str,
    path: str,
) -> None:
    """Apply a single file from a stash commit to the working tree and index.

    Reads the blob for ``path`` from the stash commit's tree, writes it
    to disk, and stages it. Raises :class:`GitError` on failure.
    """
    with unwrap(repo) as r:
        if r.is_bare:
            raise GitError("Cannot apply stash file in a bare repository.")
        workdir = r.workdir
        if workdir is None:
            raise GitError("Cannot apply stash file: no working directory.")
        try:
            oid = pygit2.Oid(hex=stash_sha)
            commit = r.get(oid)
            if commit is None:
                raise GitError(f"Unknown stash commit: {stash_sha[:8]!r}")
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise GitError(f"Unknown stash commit: {stash_sha[:8]!r}") from exc
        tree = commit.tree
        try:
            entry = tree[path]
        except KeyError as exc:
            raise GitError(f"Path {path!r} not found in stash {stash_sha[:8]!r}") from exc
        blob = r.get(entry.id)
        if blob is None:
            raise GitError(f"Cannot read blob for {path!r} in stash")
    full_path = Path(workdir) / path
    try:
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(blob.data)
    except OSError as exc:
        raise GitError(f"Failed to write {path!r}: {exc}") from exc
    with unwrap(repo) as r:
        try:
            r.index.add(path)
            r.index.write()
        except (pygit2.GitError, KeyError) as exc:
            raise GitError(f"Failed to stage {path!r} after apply: {exc}") from exc


__all__ = [
    "abort_merge",
    "abort_rebase",
    "add_remote",
    "add_to_gitignore",
    "apply_file_from_stash",
    "cherry_pick",
    "checkout_branch",
    "checkout_commit",
    "commit_changes",
    "complete_merge",
    "complete_rebase_continue",
    "create_branch",
    "delete_branch",
    "delete_file_from_disk",
    "discard_changes",
    "discard_file",
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
