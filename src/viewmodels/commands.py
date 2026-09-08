"""Command pattern scaffolding for undo/redo.

Per ``docs/DEVELOPMENT_RULES.md``, every mutating Git operation (commit,
merge, rebase, branch create, checkout, stash, push, pull, fetch) MUST be
a subclass of :class:`GitCommand` and routed through
:class:`CommandProcessor`. The toolbar Undo/Redo buttons bind to the
processor, never to operations directly.

The processor is the single owner of the undo/redo stacks and the only
thing that should emit ``stack_changed``. ``GitCommand`` subclasses
capture everything they need in ``__init__`` so the processor itself
holds no Git state.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import deque
from pathlib import Path

import pygit2
from PySide6.QtCore import QObject, Signal

from src.core.diff_parser import ParsedDiffLine
from src.core.exceptions import GitError, MergeConflictError
from src.core.operations import (
    DEFAULT_PUSH_TIMEOUT_SECONDS,
    abort_merge,
    abort_rebase,
    add_remote,
    add_to_gitignore,
    checkout_branch,
    checkout_commit,
    cherry_pick,
    commit_changes,
    create_branch,
    create_tag,
    delete_branch,
    delete_tag,
    discard_changes,
    discard_file,
    drop_commit,
    edit_commit_message,
    ensure_safe_tree_update,
    fetch,
    find_stash_index_by_oid,
    is_merge_in_progress,
    is_rebase_in_progress,
    list_remotes,
    merge_branch,
    pull,
    push,
    rebase_branch,
    remove_remote,
    rename_branch,
    reset,
    restore_index_entry,
    restore_stash,
    restore_stash_apply_state,
    revert,
    snapshot_index_entry,
    snapshot_stash_apply_state,
    squash_commits,
    stage_diff_line,
    stash_apply,
    stash_drop,
    stash_oid_at,
    stash_pop,
    stash_push,
    stash_push_staged,
    unstage_diff_line,
)
from src.core.repository import RepositoryManager


class GitCommand(ABC):
    """Base class for all mutating Git operations.

    Subclasses must capture every input they need for ``execute()`` and
    ``undo()`` in ``__init__``; the processor owns no Git state.

    Attributes
    ----------
    _timestamp : float | None
        Set by :class:`CommandProcessor` on successful ``execute()``.
        Used by the action-history panel for display ordering.
    """

    _timestamp: float | None = None
    # Commands that have no effect (or whose undo has no effect) are not
    # entered into the undo/redo history. Subclasses may set this during
    # ``execute`` when the outcome is known (for example, an up-to-date
    # merge).
    is_noop = False

    @abstractmethod
    def execute(self) -> None:
        """Apply the command to the repository."""

    @abstractmethod
    def undo(self) -> None:
        """Reverse the command's effect on the repository."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name used in the undo history UI."""

    @property
    def timestamp(self) -> float | None:
        """Wall-clock time when :class:`CommandProcessor` executed this command, or ``None``."""
        return self._timestamp


class CommandProcessor(QObject):
    """Centralised executor of :class:`GitCommand` instances.

    The processor is the only thing the toolbar Undo/Redo buttons bind to.
    Each successful :meth:`execute` clears the redo stack; undoing and
    redoing moves commands between the two stacks.
    """

    stack_changed = Signal()
    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None, max_undo: int | None = None) -> None:
        super().__init__(parent)
        if max_undo is None:
            try:
                from src.utils.config import default_config_path, load_config

                configured = load_config(default_config_path()).get(
                    "command_processor_history_size",
                    100,
                )
            except (OSError, TypeError, ValueError):
                configured = 100
            max_undo = (
                configured
                if isinstance(configured, int) and not isinstance(configured, bool)
                else 100
            )
        self._max_undo = max(1, max_undo)
        self._undo_stack: deque[GitCommand] = deque(maxlen=self._max_undo)
        self._redo_stack: deque[GitCommand] = deque(maxlen=self._max_undo)

    def _rebuild_with_maxlen(self) -> None:
        """Rebuild both history deques, retaining their newest entries."""
        self._undo_stack = deque(self._undo_stack, maxlen=self._max_undo)
        self._redo_stack = deque(self._redo_stack, maxlen=self._max_undo)

    def set_max_undo(self, n: int) -> None:
        """Change the history bound and immediately trim both stacks."""
        if isinstance(n, bool) or not isinstance(n, int):
            raise TypeError("max_undo must be an integer")
        self._max_undo = max(1, n)
        self._rebuild_with_maxlen()
        self.stack_changed.emit()

    @property
    def max_undo(self) -> int:
        """Maximum number of entries retained in either history stack."""
        return self._max_undo

    def peek_undo_command(self) -> GitCommand | None:
        """Return the most recent undo command without changing history."""
        return self._undo_stack[-1] if self._undo_stack else None

    def peek_redo_command(self) -> GitCommand | None:
        """Return the next redo command without changing either stack."""
        return self._redo_stack[-1] if self._redo_stack else None

    def execute(self, command: GitCommand) -> None:
        """Run ``command.execute()`` and push it onto the undo stack."""
        try:
            command.execute()
        except MergeConflictError as exc:
            self.record_failure(command, exc)
            raise
        self.record_success(command)

    def record_failure(self, command: GitCommand, exc: Exception) -> None:
        """Record a partially executed merge on the processor's owning thread."""
        if (isinstance(exc, MergeConflictError)
                and getattr(command, "_had_conflict_in_execute", False)):
            command._timestamp = time.time()
            command.is_noop = False
            self._undo_stack.append(command)
            self._redo_stack.clear()
            self.stack_changed.emit()

    def record_success(self, command: GitCommand, action: str = "execute") -> None:
        """Apply history bookkeeping after a worker finishes, on the GUI thread."""
        if action == "undo":
            if self.peek_undo_command() is not command:
                raise GitError("Undo history changed while the command was running.")
            self._undo_stack.pop()
            self._redo_stack.append(command)
            self.stack_changed.emit()
            return
        if action == "redo":
            if self.peek_redo_command() is not command:
                raise GitError("Redo history changed while the command was running.")
            self._redo_stack.pop()
        command._timestamp = time.time()
        if not command.is_noop:
            self._undo_stack.append(command)
        if action == "execute":
            self._redo_stack.clear()
        self.stack_changed.emit()

    def undo(self) -> bool:
        """Pop the most recent command and undo it.

        Returns ``True`` when the command was undone (and moved to the
        redo stack), ``False`` when the stack was empty or the undo
        failed.  On failure the command stays on the undo stack and the
        error is reported through :attr:`error_occurred` — callers must
        check the return value instead of assuming success (review
        finding: a failed undo used to be logged as "Undo succeeded"
        because the failure never left the processor).
        """
        if not self._undo_stack:
            return False
        command = self._undo_stack.pop()
        try:
            command.undo()
        except Exception as exc:
            # Keep a failed command available for a later retry.  In
            # particular, an undo conflict must not silently erase history.
            self._undo_stack.append(command)
            self.error_occurred.emit(str(exc))
            self.stack_changed.emit()
            return False
        self._redo_stack.append(command)
        self.stack_changed.emit()
        return True

    def redo(self) -> bool:
        """Re-apply the most recently undone command.

        Same result contract as :meth:`undo`: ``True`` on success,
        ``False`` on empty stack or failure (the command stays
        redo-able and :attr:`error_occurred` carries the message).
        """
        if not self._redo_stack:
            return False
        command = self._redo_stack.pop()
        try:
            command.execute()
        except Exception as exc:
            # A failed redo remains redo-able; do not lose it from both
            # stacks merely because the repository is currently conflicted.
            self._redo_stack.append(command)
            self.error_occurred.emit(str(exc))
            self.stack_changed.emit()
            return False
        command._timestamp = time.time()
        if not command.is_noop:
            self._undo_stack.append(command)
        self.stack_changed.emit()
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo_stack_snapshot(self) -> list[dict[str, object]]:
        """Return a copy of the undo stack metadata (oldest first).

        Each entry contains ``name`` (str) and ``timestamp`` (float or
        ``None``).  Used by the action-history panel to build its list.
        """
        return [
            {"name": cmd.name, "timestamp": cmd._timestamp}
            for cmd in self._undo_stack
        ]

    def redo_stack_snapshot(self) -> list[dict[str, object]]:
        """Return a copy of the redo stack metadata (oldest first).

        Each entry has the same shape as :meth:`undo_stack_snapshot`.
        """
        return [
            {"name": cmd.name, "timestamp": cmd._timestamp}
            for cmd in self._redo_stack
        ]

    def clear(self) -> None:
        """Drop both stacks (e.g. after opening a different repository)."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.stack_changed.emit()


# ----- undo safety guards ----------------------------------------------------
#
# History-rewriting commands capture the HEAD (and target-ref) OID they
# produced in ``execute``.  Before an undo that destructively rewinds refs
# (hard reset / forced checkout) the guards below verify:
#
# 1. HEAD is still exactly where the command left it — otherwise undo
#    would rewind commits made afterwards (via this app, the CLI, or an
#    IDE) that the command knows nothing about.
# 2. The index/worktree carry no uncommitted changes — a hard reset
#    would otherwise silently delete the user's uncommitted work.
#
# A failed guard raises :class:`GitError`; the processor keeps the command
# in the undo stack, so the user can reconcile manually and retry.


def _current_head_oid(repo: RepositoryManager) -> str | None:
    """Return the resolved HEAD OID hex, or ``None`` when HEAD is unborn."""
    r = repo.repo
    if r.head_is_unborn:
        return None
    return str(r.head.target)


def _ensure_head_at(
    repo: RepositoryManager,
    expected: str | None,
    action: str,
    expected_ref: str | None = None,
) -> None:
    """Refuse ``action`` when HEAD moved since the operation ran.

    ``expected`` is the OID the command captured right after
    ``execute``; ``None`` disables the check (nothing captured).
    """
    if expected is None:
        return
    if expected_ref is not None and repo.repo.head.name != expected_ref:
        raise GitError(
            f"Cannot {action}: HEAD is on a different branch "
            f"(expected {expected_ref}, now {repo.repo.head.name}).",
        )
    current = _current_head_oid(repo)
    if current == expected:
        return
    cur = current[:7] if current else "unborn"
    raise GitError(
        f"Cannot {action}: HEAD has moved since the operation ran "
        f"(expected {expected[:7]}, now {cur}). "
        "Reconcile manually (e.g. via the Git CLI) first.",
    )


# Status flags an undo hard reset / forced checkout would silently destroy.
# Colliding untracked/ignored paths are checked against the destination tree.
_UNSAFE_UNDO_STATUS_FLAGS = (
    pygit2.GIT_STATUS_INDEX_NEW
    | pygit2.GIT_STATUS_INDEX_MODIFIED
    | pygit2.GIT_STATUS_INDEX_DELETED
    | pygit2.GIT_STATUS_INDEX_RENAMED
    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
    | pygit2.GIT_STATUS_WT_MODIFIED
    | pygit2.GIT_STATUS_WT_DELETED
    | pygit2.GIT_STATUS_WT_RENAMED
    | pygit2.GIT_STATUS_WT_TYPECHANGE
)


def _ensure_no_uncommitted_changes(
    repo: RepositoryManager, action: str, target: str | None = None,
) -> None:
    """Refuse a destructive ``action`` while the index/worktree is dirty."""
    if target is not None:
        ensure_safe_tree_update(repo, target, action)
        return
    repo.repo.index.read()
    dirty = [
        path
        for path, flags in repo.repo.status().items()
        if flags & _UNSAFE_UNDO_STATUS_FLAGS
    ]
    if not dirty:
        return
    n = len(dirty)
    preview = ", ".join(sorted(dirty)[:10])
    suffix = f" and {n - 10} more" if n > 10 else ""
    raise GitError(
        f"Cannot {action}: uncommitted changes in {n} file(s) would be "
        f"lost. Commit, stash or discard them first: {preview}{suffix}",
    )


class CommitCommand(GitCommand):
    """Create a commit on ``HEAD``; undo via ``git reset --soft HEAD~1``.

    Captures the pre-commit HEAD SHA on :meth:`execute` so undo can move
    the ref back. An unborn ``HEAD`` is recorded as ``None`` and is valid:
    the core operation creates the first commit without a parent.
    ``stage_all=False`` because :class:`CommitPanelViewModel` manages the
    index explicitly (the user picks which files to include in the commit),
    so the index is already in the right state when this command runs.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        message: str,
        author: pygit2.Signature | None = None,
        committer: pygit2.Signature | None = None,
    ) -> None:
        self._repo = repo
        self._message = message
        self._author = author
        self._committer = committer
        self._previous_head: str | None = None

    def execute(self) -> None:
        if not self._message or not self._message.strip():
            from src.core.exceptions import GitError

            raise GitError("Commit message must not be empty.")
        self.is_noop = self._repo.repo.head_is_unborn
        if not self._repo.repo.head_is_unborn:
            self._previous_head = str(self._repo.repo.head.target)
        else:
            self._previous_head = None
        commit_changes(
            self._repo,
            self._message,
            author=self._author,
            committer=self._committer,
            stage_all=False,
        )

    def undo(self) -> None:
        if self._previous_head is None:
            # First commit (HEAD was unborn before ``execute``). We
            # cannot ``reset --soft`` past the unborn point, so undo
            # is a no-op — the user has to clean up manually.
            return
        reset(self._repo, self._previous_head, mode="soft")

    @property
    def name(self) -> str:
        first_line = self._message.splitlines()[0] if self._message else ""
        if len(first_line) > 50:
            first_line = first_line[:49] + "…"
        suffix = f": {first_line}" if first_line else ""
        return f"commit{suffix}"


class StageDiffLineCommand(GitCommand):
    """Stage one diff row and restore the previous index blob on undo."""

    def __init__(
        self,
        repo: RepositoryManager,
        path: str,
        line: ParsedDiffLine,
    ) -> None:
        self._repo = repo
        self._path = path
        self._line = line
        self._previous_entry: tuple[str, int] | None = None
        self._captured = False

    def execute(self) -> None:
        if not self._captured:
            self._previous_entry = snapshot_index_entry(self._repo, self._path)
            self._captured = True
        stage_diff_line(self._repo, self._path, self._line)

    def undo(self) -> None:
        restore_index_entry(self._repo, self._path, self._previous_entry)

    @property
    def name(self) -> str:
        return f"stage line in {self._path}"


class UnstageDiffLineCommand(GitCommand):
    """Unstage one diff row and restore the previous index blob on undo."""

    def __init__(
        self,
        repo: RepositoryManager,
        path: str,
        line: ParsedDiffLine,
    ) -> None:
        self._repo = repo
        self._path = path
        self._line = line
        self._previous_entry: tuple[str, int] | None = None
        self._captured = False

    def execute(self) -> None:
        if not self._captured:
            self._previous_entry = snapshot_index_entry(self._repo, self._path)
            self._captured = True
        unstage_diff_line(self._repo, self._path, self._line)

    def undo(self) -> None:
        restore_index_entry(self._repo, self._path, self._previous_entry)

    @property
    def name(self) -> str:
        return f"unstage line in {self._path}"


# ----- branches ---------------------------------------------------------


class CheckoutCommand(GitCommand):
    """Switch ``HEAD`` to ``target_branch``; undo by switching back.

    The previous branch shorthand is captured on :meth:`execute` and
    restored on :meth:`undo`. If the previous ``HEAD`` was unborn
    (e.g. this is the first checkout of a fresh repo) the undo is a
    no-op — there is nothing meaningful to return to.
    """

    def __init__(self, repo: RepositoryManager, target_branch: str) -> None:
        self._repo = repo
        self._target_branch = target_branch
        self._previous_branch: str | None = None

    def execute(self) -> None:
        previous = self._previous_branch_for_undo()
        result = checkout_branch(self._repo, self._target_branch)
        if result is not None:
            dirty = result.get("dirty_files", [])
            n = len(dirty)
            preview = ", ".join(dirty[:5])
            suffix = f" and {n - 5} more" if n > 5 else ""
            from src.core.exceptions import DirtyWorkTreeError
            raise DirtyWorkTreeError(
                f"Cannot check out {self._target_branch!r}: "
                f"working tree has {n} uncommitted change(s) "
                f"({preview}{suffix}).",
            )
        self._previous_branch = previous
        self.is_noop = self._previous_branch is None

    def undo(self) -> None:
        if self._previous_branch is None:
            return
        checkout_branch(self._repo, self._previous_branch, strategy=pygit2.GIT_CHECKOUT_FORCE)

    @property
    def name(self) -> str:
        return f"checkout {self._target_branch}"

    def _previous_branch_for_undo(self) -> str | None:
        """Snapshot the current branch *before* switching.

        ``HEAD.shorthand`` is safe to read as long as HEAD is not
        unborn; an unborn HEAD returns ``None`` so :meth:`undo` becomes
        a no-op.
        """
        repo = self._repo.repo
        if repo.head_is_unborn:
            return None
        return repo.head.shorthand


class CheckoutCommitCommand(GitCommand):
    """Switch ``HEAD`` to a specific commit (detached HEAD); undo by switching back.

    On :meth:`execute` the repository enters a detached-HEAD state
    pointing at the given commit SHA. :meth:`undo` restores the
    previous HEAD — either by checking out the original branch (if
    HEAD was on a named branch before) or by checking out the
    previous commit SHA directly (if already detached).

    :class:`DirtyWorkTreeError` propagates out of :meth:`execute` when
    the working tree has uncommitted changes and ``GIT_CHECKOUT_SAFE``
    is used (always true for the initial execute; undo uses FORCE).
    """

    def __init__(self, repo: RepositoryManager, sha: str) -> None:
        self._repo = repo
        self._sha = sha
        self._previous_head: str | None = None
        self._previous_branch: str | None = None

    def execute(self) -> None:
        from src.core.exceptions import DirtyWorkTreeError, GitError

        r = self._repo.repo
        try:
            if not r.head_is_unborn:
                self._previous_head = str(r.head.peel(pygit2.Commit).id)
                self._previous_branch = r.head.shorthand
        except Exception as exc:
            raise GitError(
                f"Failed to save pre-checkout HEAD state: {exc}",
            ) from exc
        try:
            result = checkout_commit(self._repo, self._sha)
        except GitError:
            raise
        except Exception as exc:
            raise GitError(f"Checkout commit failed: {exc}") from exc
        if result is not None:
            dirty = result.get("dirty_files", [])
            n = len(dirty)
            preview = ", ".join(dirty[:5])
            suffix = f" and {n - 5} more" if n > 5 else ""

            raise DirtyWorkTreeError(
                f"Cannot check out {self._sha[:7]!r}: "
                f"working tree has {n} uncommitted change(s) "
                f"({preview}{suffix}).",
            )

    def undo(self) -> None:
        if self._previous_head is None:
            return
        if self._previous_branch is not None:
            try:
                checkout_branch(
                    self._repo, self._previous_branch,
                    strategy=pygit2.GIT_CHECKOUT_FORCE,
                )
                return
            except Exception:
                pass
        checkout_commit(self._repo, self._previous_head, strategy=pygit2.GIT_CHECKOUT_FORCE)

    @property
    def name(self) -> str:
        short = self._sha[:7] if len(self._sha) >= 7 else self._sha
        return f"checkout {short}"


class CreateBranchCommand(GitCommand):
    """Create local branch ``name``; undo by deleting it.

    ``force=True`` is used for the undo because the branch was just
    created by us — there is no way it could have become the current
    branch or be checked out elsewhere in the small window between
    execute and undo.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        name: str,
        target_sha: str | None = None,
    ) -> None:
        self._repo = repo
        self._name = name
        self._target_sha = target_sha
        self._existed_before = False

    def execute(self) -> None:
        existing = {b.name for b in self._repo.branches}
        self._existed_before = self._name in existing
        create_branch(self._repo, self._name, self._target_sha)

    def undo(self) -> None:
        if self._existed_before:
            # We didn't create the branch (it pre-existed) — undo is
            # a no-op, otherwise we'd be destroying user data.
            return
        delete_branch(self._repo, self._name, force=True)

    @property
    def name(self) -> str:
        return f"create branch {self._name}"


class DeleteBranchCommand(GitCommand):
    """Delete local branch ``name``; undo by recreating it on its old target.

    The deleted branch's ``target_sha`` is captured on :meth:`execute`
    so :meth:`undo` can put the ref back at the same commit. If the
    target SHA can no longer be resolved (e.g. the repo was rewritten
    by another command) the undo is a silent no-op — failing loudly
    here would be more confusing than the original deletion.
    """

    def __init__(self, repo: RepositoryManager, name: str, force: bool = False) -> None:
        self._repo = repo
        self._name = name
        self._force = force
        self._target_sha: str | None = None
        self._existed_before = False

    def execute(self) -> None:
        existing = {b.name for b in self._repo.branches}
        self._existed_before = self._name in existing
        if self._existed_before:
            branch = self._repo.repo.lookup_branch(self._name)
            self._target_sha = str(branch.target)
        delete_branch(self._repo, self._name, force=self._force)

    def undo(self) -> None:
        if not self._existed_before or self._target_sha is None:
            return
        create_branch(self._repo, self._name, self._target_sha)

    @property
    def name(self) -> str:
        return f"delete branch {self._name}"


class RenameBranchCommand(GitCommand):
    """Rename ``old_name`` to ``new_name``; undo by swapping the names back.

    Undo uses ``force=True`` so it can clobber any branch the user
    created at the *old* name between execute and undo — that branch
    was created on top of the deleted one, and rolling back means we
    want the original state back regardless.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        old_name: str,
        new_name: str,
        force: bool = False,
    ) -> None:
        self._repo = repo
        self._old_name = old_name
        self._new_name = new_name
        self._force = force

    def execute(self) -> None:
        rename_branch(self._repo, self._old_name, self._new_name, force=self._force)

    def undo(self) -> None:
        rename_branch(self._repo, self._new_name, self._old_name, force=True)

    @property
    def name(self) -> str:
        return f"rename branch {self._old_name} → {self._new_name}"


# ----- merge / rebase / cherry-pick / revert --------------------------------


class MergeCommand(GitCommand):
    """Merge ``source`` into the current HEAD; undo by resetting back.

    Captures the pre-merge HEAD SHA on :meth:`execute` so undo can
    move the ref back. Handles three outcomes:

    * **Up-to-date** — no SHA change; undo is a no-op.
    * **Fast-forward** (``no_ff=False``) — the current branch ref
      is moved to ``source_oid``; undo is
      ``reset(_previous_head_sha, hard)``.
    * **Fast-forward forced through the merge path**
      (``no_ff=True``) — a merge commit with two parents is
      created even on a fast-forwardable history, so the merge is
      visible in the graph.
    * **Three-way merge** — a merge commit with two parents is
      created; undo is ``reset(_previous_head_sha, hard)``.

    A conflict (``MergeConflictError`` from the core layer) propagates
    out of :meth:`execute`; the processor pushes the command onto the
    undo stack (marked via ``_had_conflict_in_execute``) so Undo can
    abort the in-progress merge, and the ViewModel picks up the
    conflict state from the exception.  The core keeps HEAD on the
    *target* branch while the conflict is unresolved — the merge state
    belongs to that branch — and undo additionally returns HEAD to the
    branch the user started from after the abort.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        source: str,
        target: str | None = None,
        message: str | None = None,
        no_ff: bool = False,
    ) -> None:
        self._repo = repo
        self._source = source
        self._target = target
        self._message = message
        # ``no_ff`` mirrors ``git merge --no-ff``: when the source
        # is a fast-forward of HEAD, force a real merge commit
        # instead of silently moving the ref. The UI uses this for
        # drag-and-drop and context-menu merges — the user asked
        # for a merge, so the history should show one.
        self._no_ff = no_ff
        # The target and HEAD may be different branches.  Keep both refs,
        # rather than only the current HEAD SHA, because core.merge_branch
        # checks out an explicitly requested target before merging.
        self._target_ref_name: str | None = None
        self._target_sha_before: str | None = None
        self._head_ref_name_before: str | None = None
        self._head_sha_before: str | None = None
        self._merge_oid: str | None = None
        self._expected_head_after: str | None = None
        self._expected_head_ref_after: str | None = None
        self._head_moved = False
        self._had_conflict_in_execute = False

    def execute(self) -> None:
        repo = self._repo.repo
        target_name = self._target or (None if repo.head_is_detached else repo.head.shorthand)
        if target_name is None:
            from src.core.exceptions import GitError

            raise GitError("merge_branch requires a target branch (HEAD is detached).")

        # Capture all rollback state before core.merge_branch is called.  In
        # particular, this is the target branch's old tip, not necessarily
        # the branch currently checked out by the user.
        self._target_ref_name = f"refs/heads/{target_name}"
        try:
            target_ref = repo.lookup_reference(self._target_ref_name)
        except (KeyError, ValueError, pygit2.GitError) as exc:
            from src.core.exceptions import GitError

            raise GitError(f"Unknown target branch: {target_name!r}.") from exc
        self._target_sha_before = str(target_ref.target)
        self._head_ref_name_before = None
        self._head_sha_before = None
        if not repo.head_is_unborn and not repo.head_is_detached:
            self._head_ref_name_before = repo.head.name
            self._head_sha_before = str(repo.head.target)

        try:
            merge_branch(
                self._repo,
                self._source,
                target=self._target,
                message=self._message,
                no_ff=self._no_ff,
            )
        except MergeConflictError:
            self._had_conflict_in_execute = True
            self._merge_oid = None
            self._expected_head_after = _current_head_oid(self._repo)
            self._expected_head_ref_after = repo.head.name
            self._head_moved = False
            self.is_noop = False
            raise

        ref_after = repo.lookup_reference(self._target_ref_name)
        after_sha = str(ref_after.target)
        self._merge_oid = after_sha if after_sha != self._target_sha_before else None
        self._expected_head_after = _current_head_oid(self._repo)
        self._expected_head_ref_after = repo.head.name
        self._head_moved = (
            self._head_ref_name_before != (None if repo.head_is_detached else repo.head.name)
            or (
                self._head_sha_before is not None
                and not repo.head_is_unborn
                and str(repo.head.target) != self._head_sha_before
            )
        )
        self.is_noop = self._merge_oid is None and not self._head_moved

    def undo(self) -> None:
        # A failed merge leaves MERGE_HEAD and conflict entries behind.
        # Abort that operation instead of treating it like a clean merge.
        if self._had_conflict_in_execute:
            _ensure_head_at(
                self._repo, self._target_sha_before, "undo the conflicted merge",
                self._target_ref_name,
            )
            if is_merge_in_progress(self._repo):
                abort_merge(self._repo)
            # The conflicting merge kept HEAD on the target branch;
            # return the user to the branch they started from.
            self._restore_previous_head()
            self._had_conflict_in_execute = False
            return
        # Up-to-date merges on the current branch do not move anything.  A
        # target merge from another branch can still have checked out the
        # target, though, so restore HEAD whenever that checkout happened.
        if self._target_ref_name is None or self._target_sha_before is None:
            return
        repo = self._repo.repo
        target_ref = repo.lookup_reference(self._target_ref_name)
        if self._merge_oid is None and not self._head_moved:
            return

        # Refuse to clobber external changes: the target ref must still
        # point at the merge result and HEAD must still be where this
        # command left it (review: verify *all* refs an undo touches,
        # not just HEAD).
        if self._merge_oid is not None:
            current_target = str(target_ref.target)
            if current_target != self._merge_oid:
                raise GitError(
                    f"Cannot undo the merge: branch "
                    f"{self._target_ref_name.removeprefix('refs/heads/')!r} "
                    f"has moved since the merge (expected "
                    f"{self._merge_oid[:7]}, now {current_target[:7]}). "
                    "Reconcile manually (e.g. via the Git CLI) first.",
                )
        _ensure_head_at(
            self._repo, self._expected_head_after, "undo the merge",
            self._expected_head_ref_after,
        )
        destination = self._head_sha_before or self._target_sha_before
        if self._head_ref_name_before != self._target_ref_name:
            if self._head_ref_name_before is not None:
                destination = str(repo.lookup_reference(self._head_ref_name_before).target)
        _ensure_no_uncommitted_changes(self._repo, "undo the merge", destination)

        try:
            target_ref.set_target(self._target_sha_before)
            if self._head_ref_name_before is not None:
                repo.set_head(self._head_ref_name_before)
                repo.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)
            elif self._head_sha_before is not None:
                repo.create_reference_direct("HEAD", self._head_sha_before, force=True)
                repo.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)
        except (KeyError, ValueError, pygit2.GitError) as exc:
            raise GitError(f"Failed to undo merge: {exc}") from exc

    def _restore_previous_head(self) -> None:
        """Restore the original branch after abort, protecting local files."""
        repo = self._repo.repo
        if self._head_ref_name_before is None:
            return
        try:
            current = None if repo.head_is_detached else repo.head.name
            if current != self._head_ref_name_before:
                destination = str(repo.lookup_reference(self._head_ref_name_before).target)
                ensure_safe_tree_update(self._repo, destination, "restore the original branch")
                repo.set_head(self._head_ref_name_before)
                repo.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)
        except (KeyError, ValueError, pygit2.GitError) as exc:
            raise GitError(f"Cannot restore the original branch: {exc}") from exc

    @property
    def name(self) -> str:
        suffix = f" into {self._target}" if self._target else ""
        return f"merge {self._source}{suffix}"


class RebaseCommand(GitCommand):
    """Rebase the current branch onto ``upstream``; undo by resetting.

    Pre-rebase HEAD SHA is captured so undo can move the ref back.
    If the rebase is still in flight (conflict) when undo runs, the
    command aborts the in-progress rebase via ``git rebase --abort``
    instead of resetting — aborting leaves the tree at the pre-rebase
    state, which is exactly what the user wants.

    ``RebaseConflictError`` propagates out of :meth:`execute` so the
    processor does not push the command on failure.

    Detached-HEAD handling (R1.3): ``rebase_branch`` in core rejects
    detached HEAD up front with a domain error, so in practice
    ``RebaseCommand`` will not run when HEAD is detached.  The undo
    path is nevertheless written defensively: ``reset(OID, hard)``
    preserves the detached-vs-symbolic state — a detached HEAD stays
    detached, a symbolic HEAD stays on its branch — so the captured
    OID is sufficient to undo regardless of how HEAD looked before
    the command ran.
    """

    def __init__(self, repo: RepositoryManager, upstream: str) -> None:
        self._repo = repo
        self._upstream = upstream
        self._previous_head: str | None = None
        # ``reset(OID)`` preserves the symbolic/detached state of HEAD,
        # but tracking it explicitly makes the intent obvious and gives
        # future refactors a clear hook if the underlying behaviour ever
        # changes (R1.3 / finding C3).
        self._previous_head_was_detached: bool = False
        self._new_head: str | None = None
        self._new_head_ref: str | None = None

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
            self._previous_head_was_detached = pygit2_repo.head_is_detached
        rebase_branch(self._repo, self._upstream)
        self._new_head = _current_head_oid(self._repo)
        self._new_head_ref = self._repo.repo.head.name

    def undo(self) -> None:
        if is_rebase_in_progress(self._repo):
            # Conflict mid-rebase: abort to roll back to the pre-rebase
            # state. ``previous_head`` is irrelevant here — the abort
            # already restores the original branch.
            abort_rebase(self._repo)
            return
        if self._previous_head is None:
            return
        _ensure_head_at(self._repo, self._new_head, "undo the rebase", self._new_head_ref)
        _ensure_no_uncommitted_changes(self._repo, "undo the rebase", self._previous_head)
        # ``reset(OID)`` keeps HEAD detached if it was detached before,
        # and re-attaches to the symbolic ref otherwise, so undoing
        # from either starting state lands on the right HEAD shape.
        reset(self._repo, self._previous_head, mode="hard")

    @property
    def name(self) -> str:
        return f"rebase onto {self._upstream}"


class CherryPickCommand(GitCommand):
    """Cherry-pick ``sha`` onto the current HEAD; undo the touched index paths.

    With ``auto_commit`` the staged result is committed immediately
    (original message + authorship); undo still rewinds to the
    captured HEAD.
    """

    def __init__(self, repo: RepositoryManager, sha: str, *, auto_commit: bool = False) -> None:
        self._repo = repo
        self._sha = sha
        self._auto_commit = auto_commit
        self._previous_head: str | None = None
        self._previous_head_was_detached: bool = False
        self._index_paths: dict[str, int] = {}
        self._index_entries: dict[str, tuple[str, int] | None] = {}
        self._index_captured = False

    def _capture_index(self) -> None:
        if self._index_captured:
            return
        status = self._repo.repo.status()
        self._index_paths = {path: int(flag) for path, flag in status.items()}
        self._index_entries = {
            path: snapshot_index_entry(self._repo, path)
            for path in self._index_paths
        }
        self._index_captured = True

    def _restore_index(self) -> None:
        for path, entry in self._index_entries.items():
            restore_index_entry(self._repo, path, entry)

    def execute(self) -> None:
        self._capture_index()
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
            self._previous_head_was_detached = pygit2_repo.head_is_detached
        cherry_pick(self._repo, self._sha, create_commit=self._auto_commit)

    def undo(self) -> None:
        if self._previous_head is None:
            return
        reset(self._repo, self._previous_head, mode="mixed")
        self._restore_index()

    @property
    def name(self) -> str:
        short = self._sha[:7] if len(self._sha) >= 7 else self._sha
        return f"cherry-pick {short}"


class DropCommitCommand(GitCommand):
    """Drop ``sha`` from the current branch; undo by resetting.

    Mirrors :class:`RebaseCommand`: pre-drop HEAD is captured, undo
    aborts an in-flight rebase (conflict) or hard-resets back.  The
    hard reset is guarded: it refuses while HEAD moved externally or
    uncommitted changes would be destroyed.
    """

    def __init__(self, repo: RepositoryManager, sha: str) -> None:
        self._repo = repo
        self._sha = sha
        self._previous_head: str | None = None
        self._new_head: str | None = None
        self._new_head_ref: str | None = None

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
        drop_commit(self._repo, self._sha)
        self._new_head = _current_head_oid(self._repo)
        self._new_head_ref = self._repo.repo.head.name

    def undo(self) -> None:
        if is_rebase_in_progress(self._repo):
            abort_rebase(self._repo)
            return
        if self._previous_head is None:
            return
        _ensure_head_at(self._repo, self._new_head, "undo the drop", self._new_head_ref)
        _ensure_no_uncommitted_changes(self._repo, "undo the drop", self._previous_head)
        reset(self._repo, self._previous_head, mode="hard")

    @property
    def name(self) -> str:
        short = self._sha[:7] if len(self._sha) >= 7 else self._sha
        return f"drop commit {short}"


class EditCommitMessageCommand(GitCommand):
    """Rewrite a message and undo by restoring only the original branch ref."""

    def __init__(self, repo: RepositoryManager, sha: str, message: str) -> None:
        self._repo = repo
        self._sha = sha
        self._message = message
        self._previous_head: str | None = None
        self._new_head: str | None = None
        self._new_head_ref: str | None = None

    def execute(self) -> None:
        self._previous_head = _current_head_oid(self._repo)
        edit_commit_message(self._repo, self._sha, self._message)
        self._new_head = _current_head_oid(self._repo)
        self._new_head_ref = self._repo.repo.head.name

    def undo(self) -> None:
        if self._previous_head is None:
            return
        _ensure_head_at(self._repo, self._new_head, "undo the message edit", self._new_head_ref)
        self._repo.repo.head.set_target(self._previous_head, "undo: restore pre-reword tip")

    @property
    def name(self) -> str:
        return f"edit message {self._sha[:7]}"


class SquashCommitsCommand(GitCommand):
    """Squash a contiguous chain of commits; undo by resetting.

    Pre-squash HEAD is captured; undo aborts an in-flight rebase
    (conflict), moves the ref back only for a tip-range squash (whose
    execute touched nothing but the ref), or hard-resets back — with
    the same external-change / dirty-tree guards as the other
    history-rewriting commands.
    """

    def __init__(self, repo: RepositoryManager, shas: list[str], message: str) -> None:
        self._repo = repo
        self._shas = list(shas)
        self._message = message
        self._previous_head: str | None = None
        self._new_head: str | None = None
        self._new_head_ref: str | None = None
        self._was_tip = False

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        self._was_tip = False
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
            try:
                top = pygit2_repo.revparse_single(self._shas[0]).peel(pygit2.Commit)
                self._was_tip = top.id == pygit2_repo.head.target
            except (KeyError, ValueError, pygit2.GitError, IndexError):
                self._was_tip = False
        squash_commits(self._repo, self._shas, self._message)
        self._new_head = _current_head_oid(self._repo)
        self._new_head_ref = self._repo.repo.head.name

    def undo(self) -> None:
        if is_rebase_in_progress(self._repo):
            abort_rebase(self._repo)
            return
        if self._previous_head is None:
            return
        _ensure_head_at(self._repo, self._new_head, "undo the squash", self._new_head_ref)
        if self._was_tip:
            # Tip-range squash only moved the branch ref in execute;
            # undo is the exact inverse — the index and worktree keep
            # whatever the user had there (staged or dirty).
            self._repo.repo.head.set_target(
                self._previous_head,
                "undo: restore pre-squash tip",
            )
            return
        _ensure_no_uncommitted_changes(self._repo, "undo the squash", self._previous_head)
        reset(self._repo, self._previous_head, mode="hard")

    @property
    def name(self) -> str:
        return f"squash {len(self._shas)} commits"


class RevertCommand(GitCommand):
    """Revert ``sha``; undo by resetting --mixed (mirror of cherry-pick).

    Detached-HEAD handling (R1.3): revert is also allowed on a
    detached HEAD. ``reset(OID, mixed)`` preserves the detached
    state, so undo restores HEAD to the pre-revert SHA while
    remaining detached when it started detached.
    """

    def __init__(self, repo: RepositoryManager, sha: str) -> None:
        self._repo = repo
        self._sha = sha
        self._previous_head: str | None = None
        self._previous_head_was_detached: bool = False
        self._index_paths: dict[str, int] = {}
        self._index_entries: dict[str, tuple[str, int] | None] = {}
        self._index_captured = False

    def _capture_index(self) -> None:
        if self._index_captured:
            return
        status = self._repo.repo.status()
        self._index_paths = {path: int(flag) for path, flag in status.items()}
        self._index_entries = {
            path: snapshot_index_entry(self._repo, path)
            for path in self._index_paths
        }
        self._index_captured = True

    def _restore_index(self) -> None:
        for path, entry in self._index_entries.items():
            restore_index_entry(self._repo, path, entry)

    def execute(self) -> None:
        self._capture_index()
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
            self._previous_head_was_detached = pygit2_repo.head_is_detached
        revert(self._repo, self._sha)

    def undo(self) -> None:
        if self._previous_head is None:
            return
        reset(self._repo, self._previous_head, mode="mixed")
        self._restore_index()

    @property
    def name(self) -> str:
        short = self._sha[:7] if len(self._sha) >= 7 else self._sha
        return f"revert {short}"


# ----- remotes: push / pull / fetch / add / remove -------------------------


class PushCommand(GitCommand):
    """Push ``refspec`` to ``remote_name``.

    Push is one-way: the command is a no-op for undo, because the
    canonical rewind would require talking to the server again (force-
    push the previous SHA). We accept that the user cannot undo a push
    with the toolbar — if they need to roll back, they do it manually
    on the server.

    The command is still pushed onto the undo stack so the history
    panel shows what happened, and so Redo (which calls ``execute``
    again) replays the same push. Redo is only useful when the first
    push was rejected (e.g. rejected fast-forward → fix → redo).
    """

    # Push is irreversible from the toolbar's POV: there is no canonical
    # client-side undo. Marking the command as ``is_noop`` keeps it out of
    # the undo stack so the Undo button does not silently "succeed" by
    # doing nothing. The action-history panel still records the push.
    is_noop = True

    def __init__(
        self,
        repo: RepositoryManager,
        remote_name: str = "origin",
        refspec: str | None = None,
        callbacks: pygit2.RemoteCallbacks | None = None,
        ssh_key_path: str | None = None,
        timeout: float = DEFAULT_PUSH_TIMEOUT_SECONDS,
    ) -> None:
        self._repo = repo
        self._remote_name = remote_name
        self._refspec = refspec
        self._callbacks = callbacks
        self._ssh_key_path = ssh_key_path
        self._timeout = timeout

    def execute(self) -> None:
        push(
            self._repo, self._remote_name, self._refspec,
            callbacks=self._callbacks, ssh_key_path=self._ssh_key_path,
            timeout=self._timeout,
        )

    def undo(self) -> None:
        return  # no-op: see docstring

    @property
    def name(self) -> str:
        spec = self._refspec or "HEAD"
        return f"push {self._remote_name}/{spec}"


class PullCommand(GitCommand):
    """Fetch + merge ``refspec`` from ``remote_name`` into HEAD.

    Undo captures the pre-pull HEAD SHA on :meth:`execute` and
    rewinds via ``reset --hard``. If the pull was up-to-date (no
    SHA change) the undo is a no-op so we do not accidentally nuke
    the worktree.

    Conflicts surface as :class:`MergeConflictError` from the core
    layer — the processor does not push the command in that case.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        remote_name: str = "origin",
        refspec: str | None = None,
        callbacks: pygit2.RemoteCallbacks | None = None,
        ssh_key_path: str | None = None,
    ) -> None:
        self._repo = repo
        self._remote_name = remote_name
        self._refspec = refspec
        self._callbacks = callbacks
        self._ssh_key_path = ssh_key_path
        self._previous_head: str | None = None
        self._new_head: str | None = None
        self._new_head_ref: str | None = None
        self._head_moved = False
        self._had_conflict_in_execute = False

    def execute(self) -> None:
        pygit2_repo = self._repo.repo
        if not pygit2_repo.head_is_unborn:
            self._previous_head = str(pygit2_repo.head.target)
        else:
            self._previous_head = None
        try:
            pull(
                self._repo, self._remote_name, self._refspec,
                callbacks=self._callbacks, ssh_key_path=self._ssh_key_path,
            )
        except MergeConflictError:
            self._had_conflict_in_execute = True
            self._head_moved = False
            self.is_noop = False
            raise
        self._new_head = _current_head_oid(self._repo)
        self._new_head_ref = self._repo.repo.head.name
        if self._previous_head is None:
            self._head_moved = False
        else:
            try:
                self._head_moved = str(pygit2_repo.head.target) != self._previous_head
            except pygit2.GitError:
                self._head_moved = False

    def undo(self) -> None:
        if getattr(self, "_had_conflict_in_execute", False):
            if is_merge_in_progress(self._repo):
                abort_merge(self._repo)
            self._had_conflict_in_execute = False
            return
        if self._previous_head is None or not self._head_moved:
            return
        _ensure_head_at(self._repo, self._new_head, "undo the pull", self._new_head_ref)
        _ensure_no_uncommitted_changes(self._repo, "undo the pull", self._previous_head)
        reset(self._repo, self._previous_head, mode="hard")

    @property
    def name(self) -> str:
        spec = self._refspec or "HEAD"
        return f"pull {self._remote_name}/{spec}"


class FetchCommand(GitCommand):
    """Fetch ``refspec`` from ``remote_name``; undo is a no-op.

    Fetch only updates remote-tracking branches under
    ``refs/remotes/<name>/*``; it does not touch the working tree, the
    index, or any local ref. There is nothing to roll back, so undo
    is intentionally a no-op. The command is still pushed onto the
    undo stack so the user can see the fetch history.
    """

    # Fetch only mutates ``refs/remotes/<name>/*``; nothing on the
    # toolbar Undo side can usefully roll that back. Excluding the
    # command from the undo stack prevents a misleadingly-enabled Undo
    # button. The action-history panel still records the fetch.
    is_noop = True

    def __init__(
        self,
        repo: RepositoryManager,
        remote_name: str = "origin",
        refspec: str | None = None,
        callbacks: pygit2.RemoteCallbacks | None = None,
        ssh_key_path: str | None = None,
    ) -> None:
        self._repo = repo
        self._remote_name = remote_name
        self._refspec = refspec
        self._callbacks = callbacks
        self._ssh_key_path = ssh_key_path

    def execute(self) -> None:
        fetch(
            self._repo,
            self._remote_name,
            [self._refspec] if self._refspec else None,
            callbacks=self._callbacks,
            ssh_key_path=self._ssh_key_path,
        )

    def undo(self) -> None:
        return  # no-op: see docstring

    @property
    def name(self) -> str:
        spec = self._refspec or "all"
        return f"fetch {self._remote_name}/{spec}"


class AddRemoteCommand(GitCommand):
    """Add a remote; undo by removing it.

    The command records whether the remote already existed *before*
    ``execute`` ran. If it did, undo is a no-op — we would otherwise
    destroy a remote the user did not create through this command.
    """

    def __init__(self, repo: RepositoryManager, name: str, url: str) -> None:
        self._repo = repo
        self._name = name
        self._url = url
        self._existed_before = False

    def execute(self) -> None:
        existing = {r.name for r in list_remotes(self._repo)}
        self._existed_before = self._name in existing
        add_remote(self._repo, self._name, self._url)

    def undo(self) -> None:
        if self._existed_before:
            return
        remove_remote(self._repo, self._name)

    @property
    def name(self) -> str:
        return f"add remote {self._name}"


class RemoveRemoteCommand(GitCommand):
    """Remove a remote; undo by re-adding it with the original URL.

    The URL (and fetch refspec, which we cannot recover from pygit2
    after deletion) is captured on :meth:`execute`. On undo we
    re-create the remote with the same name and URL; the fetch
    refspec reverts to libgit2's default (``+refs/heads/*:refs/remotes/<name>/*``),
    which matches what libgit2 would have produced originally.
    """

    def __init__(self, repo: RepositoryManager, name: str) -> None:
        self._repo = repo
        self._name = name
        self._saved_url: str | None = None
        self._existed_before = False

    def execute(self) -> None:
        existing = {r.name for r in list_remotes(self._repo)}
        self._existed_before = self._name in existing
        if self._existed_before:
            for r in list_remotes(self._repo):
                if r.name == self._name:
                    self._saved_url = r.url
                    break
        remove_remote(self._repo, self._name)

    def undo(self) -> None:
        if not self._existed_before or not self._saved_url:
            return
        add_remote(self._repo, self._name, self._saved_url)

    @property
    def name(self) -> str:
        return f"remove remote {self._name}"


# ----- stash: push / pop / apply / drop -----------------------------------


class StashPushCommand(GitCommand):
    """Push the current worktree changes onto the stash list.

    The pushed OID is retained so :meth:`undo` can remove that exact
    entry even if another client prepends a stash before undo runs.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        message: str = "WIP",
        include_untracked: bool = True,
    ) -> None:
        self._repo = repo
        self._message = message
        self._include_untracked = include_untracked
        self._stash_oid_at_saved: str | None = None

    def execute(self) -> None:
        self._stash_oid_at_saved = stash_push(
            self._repo,
            self._message,
            include_untracked=self._include_untracked,
        )

    def undo(self) -> None:
        if self._stash_oid_at_saved is None:
            return
        pushed_oid = self._stash_oid_at_saved
        current_oid = stash_oid_at(self._repo, 0)
        found_at = 0
        if current_oid != pushed_oid:
            found_at = find_stash_index_by_oid(self._repo, pushed_oid)
            if found_at is None:
                raise GitError(f"Stash entry {pushed_oid} not found — undo aborted.")
        if found_at == 0:
            # Preserve the established Undo UX: if our stash is still the
            # newest entry, pop it so the command's worktree changes return.
            stash_pop(self._repo, 0)
        else:
            # A newer stash belongs to someone else. Applying our now-older
            # entry could conflict with or overwrite their state, so only
            # remove the exact OID captured by execute().
            stash_drop(self._repo, found_at)
        self._stash_oid_at_saved = None

    @property
    def name(self) -> str:
        label = (self._message or "WIP").splitlines()[0]
        if len(label) > 40:
            label = label[:39] + "…"
        return f"stash push: {label}"


class StashPopCommand(GitCommand):
    """Apply the stash at ``index`` and remove it from the list.

    :meth:`undo` restores the pre-pop worktree/index snapshot first,
    then recreates the dropped stash entry via ``git stash store``.
    """

    def __init__(self, repo: RepositoryManager, index: int = 0) -> None:
        self._repo = repo
        self._index = index
        self._popped_oid: str | None = None
        self._captured_message: str | None = None
        self._applied_path_contents: dict[str, bytes] | None = None
        self._applied_missing_paths: set[str] = set()
        self._applied_index_diff: dict[str, str] | None = None
        self._pop_completed = False

    def execute(self) -> None:
        # Snapshot the OID, message, worktree, and index before pop removes
        # the stash entry and applies it.
        self._pop_completed = False
        self._popped_oid = stash_oid_at(self._repo, self._index)
        if self._popped_oid is None:
            # Preserve stash_pop's established domain error for an invalid
            # index rather than manufacturing a snapshot-related error.
            stash_pop(self._repo, self._index)
            return
        (
            self._applied_path_contents,
            self._applied_missing_paths,
            self._applied_index_diff,
        ) = snapshot_stash_apply_state(self._repo, self._index)
        try:
            for idx, entry in enumerate(self._repo.repo.listall_stashes()):
                if idx == self._index:
                    raw = entry.message.strip()
                    # Strip "On <branch>: " prefix added by libgit2.
                    if ": " in raw:
                        self._captured_message = raw.split(": ", 1)[1]
                    else:
                        self._captured_message = raw
                    break
        except (pygit2.GitError, KeyError):
            self._captured_message = None
        stash_pop(self._repo, self._index)
        self._pop_completed = True

    def undo(self) -> None:
        if (
            not self._pop_completed
            or self._popped_oid is None
            or self._applied_path_contents is None
            or self._applied_index_diff is None
        ):
            return
        restore_stash_apply_state(
            self._repo,
            self._applied_path_contents,
            self._applied_missing_paths,
            self._applied_index_diff,
        )
        if find_stash_index_by_oid(self._repo, self._popped_oid) is None:
            restore_stash(
                self._repo,
                self._popped_oid,
                self._captured_message or "WIP (restored)",
            )
        self._applied_path_contents = None
        self._applied_missing_paths = set()
        self._applied_index_diff = None
        self._pop_completed = False

    @property
    def name(self) -> str:
        return f"stash pop @{{{self._index}}}"


class StashApplyCommand(GitCommand):
    """Apply the stash at ``index`` without removing it from the list.

    Only paths represented by the stash are snapshotted. Undo can
    therefore restore their exact pre-apply worktree/index state without
    discarding unrelated dirty or untracked files.
    """

    def __init__(self, repo: RepositoryManager, index: int = 0) -> None:
        self._repo = repo
        self._index = index
        self._applied_path_contents: dict[str, bytes] | None = None
        self._applied_missing_paths: set[str] = set()
        self._applied_index_diff: dict[str, str] | None = None
        self._apply_completed = False

    def execute(self) -> None:
        self._apply_completed = False
        if stash_oid_at(self._repo, self._index) is None:
            stash_apply(self._repo, self._index)
            return
        (
            self._applied_path_contents,
            self._applied_missing_paths,
            self._applied_index_diff,
        ) = snapshot_stash_apply_state(self._repo, self._index)
        stash_apply(self._repo, self._index)
        self._apply_completed = True

    def undo(self) -> None:
        if (
            not self._apply_completed
            or self._applied_path_contents is None
            or self._applied_index_diff is None
        ):
            return
        restore_stash_apply_state(
            self._repo,
            self._applied_path_contents,
            self._applied_missing_paths,
            self._applied_index_diff,
        )
        self._applied_path_contents = None
        self._applied_missing_paths = set()
        self._applied_index_diff = None
        self._apply_completed = False

    @property
    def name(self) -> str:
        return f"stash apply @{{{self._index}}}"


class StashDropCommand(GitCommand):
    """Drop the stash at ``index``; undo by restoring the stash via ``git stash store``.

    The dropped commit object survives in the object database (only
    the ref is removed), so we can use :func:`src.core.operations.restore_stash`
    to put the entry back on undo. The message is also captured
    because libgit2 prefixes the raw commit message with
    ``"On <branch>: "`` and ``git stash store`` requires the bare
    user message.
    """

    def __init__(self, repo: RepositoryManager, index: int = 0) -> None:
        self._repo = repo
        self._index = index
        self._captured_oid: str | None = None
        self._captured_message: str | None = None

    def execute(self) -> None:
        self._captured_oid = stash_oid_at(self._repo, self._index)
        if self._captured_oid is not None:
            try:
                for idx, entry in enumerate(self._repo.repo.listall_stashes()):
                    if idx == self._index:
                        raw = entry.message.strip()
                        if ": " in raw:
                            self._captured_message = raw.split(": ", 1)[1]
                        else:
                            self._captured_message = raw
                        break
            except (pygit2.GitError, KeyError):
                self._captured_message = None
        stash_drop(self._repo, self._index)

    def undo(self) -> None:
        if self._captured_oid is None:
            return
        if not self._captured_message:
            self._captured_message = "WIP (restored)"
        try:
            restore_stash(self._repo, self._captured_oid, self._captured_message)
        except Exception:
            pass  # best-effort

    @property
    def name(self) -> str:
        return f"stash drop @{{{self._index}}}"


class StashPushStagedCommand(GitCommand):
    """Stash only the staged (index) changes via :func:`src.core.operations.stash_push_staged`.

    The undo path pops the stash we just pushed (the staged files
    return to the index, the worktree is left alone). This mirrors
    :class:`StashPushCommand`'s undo strategy.
    """

    def __init__(self, repo: RepositoryManager, message: str = "WIP staged") -> None:
        self._repo = repo
        self._message = message
        self._pushed_oid: str | None = None

    def execute(self) -> None:
        self._pushed_oid = stash_push_staged(self._repo, self._message)

    def undo(self) -> None:
        if self._pushed_oid is None:
            return
        try:
            stash_pop(self._repo, 0)
        except Exception:
            pass  # best-effort

    @property
    def name(self) -> str:
        return "stash push (staged only)"


class DiscardFileCommand(GitCommand):
    """Discard uncommitted changes for a single file, restoring it from HEAD.

    Untracked files are handled specially: the content is read into memory
    and the file is removed from disk; undo writes it back.
    """

    def __init__(self, repo: RepositoryManager, path: str) -> None:
        self._repo = repo
        self._path = path
        self._untracked_backup: bytes | None = None
        self._backup_exceeded = False

    def _is_untracked(self) -> bool:
        flag = self._repo.repo.status().get(self._path)
        return bool(flag and flag & pygit2.GIT_STATUS_WT_NEW)

    def execute(self) -> None:
        from src.core.repository import unwrap

        with unwrap(self._repo) as r:
            if r.is_bare or r.head_is_unborn:
                return
        dirty = bool(self._repo.repo.status().get(self._path))
        if not dirty:
            discard_file(self._repo, self._path)
            return
        if self._is_untracked():
            self._discard_untracked()
            return
        discard_file(self._repo, self._path)

    def _discard_untracked(self) -> None:
        from src.core.repository import unwrap

        with unwrap(self._repo) as r:
            workdir = r.workdir
        if workdir is None:
            return
        full_path = Path(workdir) / self._path
        if not full_path.exists():
            return
        from src.utils.config import default_config_path, get_int, load_config

        cap = get_int(
            load_config(default_config_path()),
            "discard_file_max_backup_bytes",
            1024 * 1024,
        )
        if full_path.stat().st_size > max(0, cap):
            self._backup_exceeded = True
            full_path.unlink()
            return
        self._untracked_backup = full_path.read_bytes()
        full_path.unlink()

    def undo(self) -> None:
        if self._untracked_backup is None:
            return
        from src.core.repository import unwrap

        with unwrap(self._repo) as r:
            workdir = r.workdir
        if workdir is not None:
            full_path = Path(workdir) / self._path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_bytes(self._untracked_backup)
        self._untracked_backup = None

    @property
    def name(self) -> str:
        return f"discard {self._path}"


class StashSingleFileCommand(GitCommand):
    """Stash changes for a single file; undo via :func:`stash_pop`."""

    def __init__(self, repo: RepositoryManager, path: str) -> None:
        self._repo = repo
        self._path = path
        self._pushed_oid: str | None = None

    def execute(self) -> None:
        self._pushed_oid = stash_push(
            self._repo,
            message=f"WIP: {self._path}",
            paths=[self._path],
        )

    def undo(self) -> None:
        if self._pushed_oid is None:
            return
        try:
            stash_pop(self._repo, 0)
        except Exception:
            pass

    @property
    def name(self) -> str:
        return f"stash {self._path}"


class IgnoreCommand(GitCommand):
    """Add a pattern to ``.gitignore``; undo removes the last line.

    This is a best-effort undo — if ``.gitignore`` has been modified
    concurrently the undo may not be correct.
    """

    def __init__(self, repo: RepositoryManager, pattern: str) -> None:
        self._repo = repo
        self._pattern = pattern

    def execute(self) -> None:
        add_to_gitignore(self._repo, self._pattern)

    def undo(self) -> None:
        from src.core.repository import unwrap

        with unwrap(self._repo) as r:
            workdir = r.workdir
            if workdir is None:
                return
        gitignore_path = Path(workdir) / ".gitignore"
        if not gitignore_path.exists():
            return
        try:
            lines = gitignore_path.read_text(encoding="utf-8").splitlines()
            if lines and lines[-1] == self._pattern:
                gitignore_path.write_text(
                    "\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""),
                    encoding="utf-8",
                )
        except OSError:
            pass

    @property
    def name(self) -> str:
        return f"ignore {self._pattern}"


class DiscardChangesCommand(GitCommand):
    """Discard all uncommitted changes (index + workdir) in the working tree.

    The working tree is hard-reset to ``HEAD``.  Undo is a no-op since
    the changes are discarded without a backup.
    """

    # ``DiscardChangesCommand`` is destructive and irreversible: the
    # working tree is hard-reset with no backup of the prior content.
    # Marking it as ``is_noop`` keeps it out of the undo stack so the
    # toolbar Undo button does not silently "succeed" on a no-op. The
    # confirmation gate is enforced at the VM layer
    # (:meth:`MainViewModel.discard_changes`).
    is_noop = True

    def __init__(self, repo: RepositoryManager) -> None:
        self._repo = repo

    def execute(self) -> None:
        from src.core.repository import unwrap

        with unwrap(self._repo) as r:
            if r.is_bare or r.head_is_unborn:
                return
        discard_changes(self._repo)

    def undo(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "discard changes"


class CreateTagCommand(GitCommand):
    """Create a tag (lightweight or annotated); undo by deleting it.

    :meth:`execute` records whether the tag already existed *before*
    the call. If it did, :meth:`undo` is a no-op — we would otherwise
    destroy a tag the user did not create through this command.

    Annotated tags carry a message (and an optional tagger signature);
    lightweight tags have ``message=None``.
    """

    def __init__(
        self,
        repo: RepositoryManager,
        name: str,
        target_sha: str,
        message: str | None = None,
        tagger: pygit2.Signature | None = None,
    ) -> None:
        self._repo = repo
        self._name = name
        self._target_sha = target_sha
        self._message = message
        self._tagger = tagger
        self._existed_before = False

    def execute(self) -> None:
        existing = {t.name for t in self._repo.tags}
        self._existed_before = self._name in existing
        create_tag(self._repo, self._name, self._target_sha, self._message, self._tagger)

    def undo(self) -> None:
        if self._existed_before:
            return
        try:
            delete_tag(self._repo, self._name)
        except Exception:
            pass  # best-effort: tag may have been deleted externally

    @property
    def name(self) -> str:
        suffix = " (annotated)" if self._message else ""
        return f"create tag {self._name}{suffix}"


class FetchAndCheckoutCommand(GitCommand):
    """Fetch and update one tracking branch without touching other local refs."""

    def __init__(
        self, repo: RepositoryManager, remote_branch: str, *,
        force: bool = False, ssh_key_path: str | None = None,
    ):
        self._repo = repo
        self._remote_branch = remote_branch
        self._force = force
        self._ssh_key_path = ssh_key_path
        self._previous_ref: str | None = None
        self._previous_oid: str | None = None
        self._local_oid: str | None = None
        self._local_upstream: str | None = None
        self._new_head: str | None = None
        self.diverged = False

    @property
    def name(self) -> str:
        verb = "reset to" if self._force else "fetch and checkout"
        return f"{verb} {self._remote_branch}"

    def execute(self) -> None:
        from src.core.operations import checkout_fetched_branch

        remote, local_name = self._remote_branch.split("/", 1)
        fetch(
            self._repo, remote,
            [f"+refs/heads/{local_name}:refs/remotes/{remote}/{local_name}"],
            ssh_key_path=self._ssh_key_path,
        )
        r = self._repo.repo
        self._previous_ref = r.head.name if not r.head_is_unborn else None
        self._previous_oid = _current_head_oid(self._repo)
        local = r.lookup_branch(local_name)
        self._local_oid = str(local.target) if local is not None else None
        self._local_upstream = (
            local.upstream.shorthand if local is not None and local.upstream is not None else None
        )
        self.diverged = checkout_fetched_branch(
            self._repo, self._remote_branch, force=self._force,
        )
        self._new_head = _current_head_oid(self._repo)

    def undo(self) -> None:
        if self._previous_oid is None:
            raise GitError("Cannot restore an unborn branch after remote checkout.")
        r = self._repo.repo
        local_name = self._remote_branch.split("/", 1)[1]
        local_ref = f"refs/heads/{local_name}"
        _ensure_head_at(self._repo, self._new_head, "undo remote checkout", local_ref)
        destination = self._previous_oid
        if self._previous_ref not in (local_ref, "HEAD", None):
            destination = str(r.lookup_reference(self._previous_ref).target)
        ensure_safe_tree_update(self._repo, destination, "undo remote checkout")
        try:
            r.checkout_tree(r[pygit2.Oid(hex=destination)], strategy=pygit2.GIT_CHECKOUT_FORCE)
            local = r.lookup_branch(local_name)
            if self._local_oid is not None:
                local.set_target(self._local_oid)
                local.upstream = (
                    r.lookup_branch(self._local_upstream, pygit2.GIT_BRANCH_REMOTE)
                    if self._local_upstream is not None else None
                )
            r.set_head(
                pygit2.Oid(hex=destination) if self._previous_ref == "HEAD" else self._previous_ref,
            )
            if self._local_oid is None:
                local.delete()
        except (KeyError, ValueError, pygit2.GitError) as exc:
            raise GitError(f"Cannot undo remote checkout: {exc}") from exc


class ContinueRebaseCommand(GitCommand):
    """Continue a resolved rebase and make its completed result undoable."""

    def __init__(self, repo: RepositoryManager):
        self._repo = repo
        self._previous_oid: str | None = None
        self._head_ref: str | None = None
        self._new_head: str | None = None
        self.result = None

    @property
    def name(self) -> str:
        return "continue rebase"

    def execute(self) -> None:
        from src.core.operations import RebaseContinueResult, complete_rebase_continue

        if self._new_head is not None:
            _ensure_head_at(self._repo, self._previous_oid, "redo rebase", self._head_ref)
            ensure_safe_tree_update(self._repo, self._new_head, "redo rebase")
            reset(self._repo, self._new_head, mode="hard")
            return
        r = self._repo.repo
        for dirname in ("rebase-merge", "rebase-apply"):
            directory = Path(r.path) / dirname
            if directory.is_dir():
                try:
                    self._previous_oid = (directory / "orig-head").read_text().strip()
                    self._head_ref = (directory / "head-name").read_text().strip()
                except OSError as exc:
                    raise GitError(f"Cannot read rebase state: {exc}") from exc
                break
        self.result = complete_rebase_continue(self._repo)
        self.is_noop = self.result is RebaseContinueResult.CONFLICTS_REMAIN
        if not self.is_noop:
            self._new_head = _current_head_oid(self._repo)
            self._head_ref = r.head.name

    def undo(self) -> None:
        _ensure_head_at(self._repo, self._new_head, "undo rebase", self._head_ref)
        if self._previous_oid is None:
            raise GitError("Original rebase HEAD is missing; cannot undo.")
        ensure_safe_tree_update(self._repo, self._previous_oid, "undo rebase")
        reset(self._repo, self._previous_oid, mode="hard")


class CompleteMergeCommand(GitCommand):
    """Finalize a conflict-resolved merge by creating the merge commit.

    This is the CommandProcessor-routed finish of a conflicted
    :class:`MergeCommand`: after the user resolves the last conflicting
    file, the ViewModel executes this command so the merge completion
    is itself undoable.  Undo rewinds the target branch to
    ``parent_oid`` (the pre-merge tip) via a hard reset — guarded
    against external HEAD moves and uncommitted changes like every
    other destructive undo.
    """

    def __init__(
        self,
        repo_manager,
        source: str,
        parent_oid: str,
        target: str | None = None,
        message: str | None = None,
    ) -> None:
        self._repo = repo_manager
        self._source = source  # ref / branch / SHA being merged in
        self._parent_oid = parent_oid  # pre-merge HEAD SHA — undo target
        self._target = target
        self._message = message
        self._merge_oid: str | None = None
        self._head_ref: str | None = None

    @property
    def name(self) -> str:
        return "CompleteMerge"

    def execute(self) -> None:
        from src.core.operations import complete_merge

        if self._merge_oid is not None:
            _ensure_head_at(
                self._repo, self._parent_oid, "redo the merge completion", self._head_ref,
            )
            ensure_safe_tree_update(self._repo, self._merge_oid, "redo the merge completion")
            reset(self._repo, self._merge_oid, mode="hard")
            return
        self._head_ref = self._repo.repo.head.name
        self._merge_oid = complete_merge(
            self._repo,
            source=self._source,
            target=self._target,
            message=self._message,
        )

    def undo(self) -> None:
        from src.core.operations import reset as core_reset

        _ensure_head_at(
            self._repo, self._merge_oid, "undo the merge completion", self._head_ref,
        )
        _ensure_no_uncommitted_changes(
            self._repo, "undo the merge completion", self._parent_oid,
        )
        # Hard reset to pre-merge HEAD. Worktree is also reset —
        # caller should re-populate from the resolved tree if needed.
        core_reset(self._repo, target=self._parent_oid, mode="hard")


__all__ = [
    "AddRemoteCommand",
    "CheckoutCommand",
    "CheckoutCommitCommand",
    "CherryPickCommand",
    "CommandProcessor",
    "CompleteMergeCommand",
    "CommitCommand",
    "CreateBranchCommand",
    "CreateTagCommand",
    "DeleteBranchCommand",
    "DiscardChangesCommand",
    "DiscardFileCommand",
    "FetchCommand",
    "GitCommand",
    "IgnoreCommand",
    "MergeCommand",
    "PullCommand",
    "PushCommand",
    "RebaseCommand",
    "RemoveRemoteCommand",
    "RenameBranchCommand",
    "RevertCommand",
    "StashApplyCommand",
    "StashDropCommand",
    "StashPopCommand",
    "StashPushCommand",
    "StashPushStagedCommand",
    "StashSingleFileCommand",
    "StageDiffLineCommand",
    "UnstageDiffLineCommand",
]
