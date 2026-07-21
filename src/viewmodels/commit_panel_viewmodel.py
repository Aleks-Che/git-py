"""ViewModel for the WIP / staging panel.

Holds:

* ``file_changes`` — the current working-tree + index status (read-only,
  refreshed from :meth:`RepositoryManager.get_status`).
* ``staged_files`` — the subset of paths that are currently recorded in
  the index (the bits ``git commit`` would actually pick up). The set
  is rebuilt from the raw ``pygit2`` status flags, not from
  :class:`FileStatus`, because that enum conflates staged and
  worktree-only variants for ``MODIFIED`` / ``DELETED`` / ``RENAMED`` /
  ``TYPE_CHANGED``.
* ``commit_summary`` / ``commit_description`` — the two text fields
  the user fills in for the next commit. The commit message sent to
  Git is built by :meth:`combined_commit_message` as
  ``"<summary>\\n\\n<description>"`` (or just ``<summary>`` when the
  description is empty).
* ``selected_file`` / ``current_diff`` — kept for the Stage-3 diff
  preview contract; the new right-panel UI no longer reads them but
  they remain so old tests and any future diff widget still work.

The ViewModel never commits on its own: it only stages / unstages
files and prepares data. The actual commit is created by
:meth:`MainViewModel.commit_changes`, which builds a
:class:`CommitCommand` and runs it through the
:class:`CommandProcessor`.
"""
from __future__ import annotations

import pygit2
from PySide6.QtCore import QObject, Signal

from src.core.diff_parser import filter_staged_diff_lines
from src.core.exceptions import GitError
from src.core.models import FileChange, FileStatus
from src.core.repository import RepositoryManager

# Bitmask of pygit2 status flags that mean "the change is already
# recorded in the index" (i.e. would be picked up by the next commit).
_STAGED_FLAGS = (
    pygit2.GIT_STATUS_INDEX_NEW
    | pygit2.GIT_STATUS_INDEX_MODIFIED
    | pygit2.GIT_STATUS_INDEX_DELETED
    | pygit2.GIT_STATUS_INDEX_RENAMED
    | pygit2.GIT_STATUS_INDEX_TYPECHANGE
)

_UNSTAGED_FLAGS = (
    pygit2.GIT_STATUS_WT_NEW
    | pygit2.GIT_STATUS_WT_MODIFIED
    | pygit2.GIT_STATUS_WT_DELETED
    | pygit2.GIT_STATUS_WT_RENAMED
    | pygit2.GIT_STATUS_WT_TYPECHANGE
)

# When generating the "full document" variant of a diff we want
# enough context on either side of every change to span the entire
# file. ``2**31 - 1`` is the maximum value libgit2 will accept for
# ``context_lines`` and is large enough for any realistic file size.
_FULL_DOCUMENT_CONTEXT_LINES = 2**31 - 1


class CommitPanelViewModel(QObject):
    """State + verb methods for the WIP / commit-message panel."""

    file_changes_changed = Signal()
    """Emitted when ``file_changes`` is replaced (after :meth:`refresh_status`)."""

    staged_files_changed = Signal(list)
    """Emitted with the sorted list of currently staged paths."""

    selected_file_changed = Signal(object)
    """Emitted with the new selected path (or ``None``)."""

    diff_ready = Signal(str)
    """Emitted with the unified-diff text for the selected file."""

    diff_pair_ready = Signal(str, str)
    """Emitted with the (changes-only, full-document) diff pair.

    The first string is the changes-only diff (the same one
    :attr:`diff_ready` emits); the second is the same file's
    full-document variant with effectively unlimited context so the
    entire file is rendered in the viewer. The :class:`DiffViewWidget`
    uses both to switch between its two view modes without re-running
    Git on toggle."""

    commit_summary_changed = Signal(str)
    """Emitted when ``commit_summary`` changes."""

    commit_description_changed = Signal(str)
    """Emitted when ``commit_description`` changes."""

    commit_message_changed = Signal(str)
    """Emitted with the *combined* commit message
    (``summary + \\n\\n + description``) so legacy listeners that only
    care about the final string still work."""

    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo: RepositoryManager | None = None
        self._file_changes: list[FileChange] = []
        self._raw_status: dict[str, int] = {}
        self._staged_files: set[str] = set()
        self._selected_file: str | None = None
        self._selected_file_staged: bool = False
        self._current_diff: str | None = None
        self._commit_summary: str = ""
        self._commit_description: str = ""
        # R3.2 (P5): batch-mode flag for the stage_all / unstage_all
        # verbs.  When ``True``, ``stage_file`` / ``unstage_file``
        # skip their per-file ``refresh_status`` so the batch runs in
        # O(n) pygit2 ops instead of O(n²).  Toggled by
        # :meth:`set_batch_refresh`.
        self._batch_refresh: bool = False

    # ----- read-only state (properties) --------------------------------

    def file_changes(self) -> list[FileChange]:
        """Return a copy of the current working-tree / index status."""
        return list(self._file_changes)

    def staged_files(self) -> list[str]:
        """Return the sorted list of currently staged paths."""
        return sorted(self._staged_files)

    def staged_paths_set(self) -> set[str]:
        """Return the staged paths as a set (fast ``in`` lookups)."""
        return set(self._staged_files)

    def unstaged_paths(self) -> list[str]:
        """Return paths with working-tree changes not recorded in the index."""
        return sorted(
            path
            for path, flag in self._raw_status.items()
            if flag & _UNSTAGED_FLAGS
        )

    def unstaged_files(self) -> list[FileChange]:
        """Return one side-specific record for every working-tree change."""
        result: list[FileChange] = []
        for path, flag in self._raw_status.items():
            status = self._worktree_status(flag)
            if status is not None:
                result.append(FileChange(path=path, status=status))
        return result

    def staged_files_detailed(self) -> list[FileChange]:
        """Return one side-specific record for every index change."""
        result: list[FileChange] = []
        for path, flag in self._raw_status.items():
            status = self._index_status(flag)
            if status is not None:
                result.append(FileChange(path=path, status=status))
        return result

    def selected_file_is_staged(self) -> bool:
        """Return whether the selected diff is the staged side of the file."""
        return self._selected_file_staged

    def selected_file_supports_line_actions(self) -> bool:
        """Return whether the selected side is a tracked text modification."""
        path = self._selected_file
        if path is None or self._repo is None or not self._repo.is_open:
            return False
        flag = self._raw_status.get(path, pygit2.GIT_STATUS_CURRENT)
        required = (
            pygit2.GIT_STATUS_INDEX_MODIFIED
            if self._selected_file_staged
            else pygit2.GIT_STATUS_WT_MODIFIED
        )
        if not flag & required:
            return False
        try:
            self._repo.repo.revparse_single(f"HEAD:{path}")
        except (GitError, pygit2.GitError, OSError) as exc:
            self.error_occurred.emit(str(exc))
            return False
        except (KeyError, ValueError):
            return False
        return not self._is_binary(path)

    def selected_file(self) -> str | None:
        return self._selected_file

    def current_diff(self) -> str | None:
        return self._current_diff

    def commit_summary(self) -> str:
        return self._commit_summary

    def commit_description(self) -> str:
        return self._commit_description

    def commit_message(self) -> str:
        """Return the combined message (``summary + \\n\\n + description``)."""
        return self.combined_commit_message()

    def combined_commit_message(self) -> str:
        """Return the message to send to ``git commit``.

        Concatenates ``summary`` and ``description`` with a single
        blank line between them (the conventional git layout). When
        the description is empty only the summary is returned.
        """
        if self._commit_description.strip():
            return f"{self._commit_summary}\n\n{self._commit_description}"
        return self._commit_summary

    def has_commit_input(self) -> bool:
        """Return ``True`` if either field has user input."""
        return bool(self._commit_summary.strip() or self._commit_description.strip())

    # ----- repository binding -----------------------------------------

    def set_repository(
        self,
        manager: RepositoryManager | None,
        *,
        refresh: bool = True,
    ) -> None:
        """Bind (or unbind) the repository the panel reads from.

        On every bind the message fields and the file selection are
        cleared and the status is refreshed. ``manager=None`` is the
        close path: the panel becomes empty.

        Pass ``refresh=False`` to defer the status re-read so the
        caller can batch it inside a background worker.
        """
        self._repo = manager
        self._raw_status = {}
        self._selected_file = None
        self._selected_file_staged = False
        self._current_diff = None
        self._commit_summary = ""
        self._commit_description = ""
        self.selected_file_changed.emit(None)
        self.diff_ready.emit("")
        self.diff_pair_ready.emit("", "")
        self.commit_summary_changed.emit("")
        self.commit_description_changed.emit("")
        self.commit_message_changed.emit("")
        if refresh:
            self.refresh_status()

    # ----- verb methods ------------------------------------------------

    def refresh_status(self) -> None:
        """Re-read the working-tree status and rebuild the staged set.

        Translates :class:`GitError` into :attr:`error_occurred` —
        never re-raises. Emits :attr:`file_changes_changed` and
        :attr:`staged_files_changed` exactly once each.

        The raw ``pygit2`` status dict is fetched once and reused for
        both the :class:`FileChange` list and the staged-files set,
        avoiding a second ``repo.status()`` call that walks the
        working tree a second time.

        If the currently-selected file is no longer present in the
        refreshed status (for example after ``Stash Changes`` or
        ``Discard All Changes`` empties the working tree, or after
        ``Discard File`` removes a single tracked file), the file
        selection is cleared so the diff view also closes. Without
        this, the diff would stay open in the centre column while
        the file list that drives it became empty — leaving the user
        with no UI affordance to dismiss it.
        """
        if self._repo is None or not self._repo.is_open:
            self._file_changes = []
            self._raw_status = {}
            self._staged_files = set()
        else:
            try:
                # A committed repository should always have an index.  A
                # missing one is indistinguishable from a broken index to
                # the panel, so surface it through the normal error path
                # instead of presenting a misleading empty status list.
                from pathlib import Path as _Path

                index_path = _Path(self._repo.repo.path) / "index"
                if not self._repo.repo.head_is_unborn and not index_path.exists():
                    raise OSError(f"Git index does not exist: {index_path}")
                raw_status = self._repo.repo.status()
                self._raw_status = dict(raw_status)
                self._file_changes = self._repo.get_status_from_raw(raw_status)
                self._staged_files = self._compute_staged_files_from_raw(raw_status)
            except (GitError, pygit2.GitError, OSError) as exc:
                self.error_occurred.emit(str(exc))
                self._file_changes = []
                self._raw_status = {}
                self._staged_files = set()
        # Force-close the diff when the selected file disappeared from
        # the working-tree / index status. ``select_file(None)`` is
        # idempotent (no-op when nothing is selected) and emits the
        # ``selected_file_changed`` / ``diff_ready`` signals that the
        # main window uses to swap the graph back in.
        if self._selected_file is not None:
            paths = {c.path for c in self._file_changes}
            if self._selected_file not in paths:
                self.select_file(None)
        self.file_changes_changed.emit()
        self.staged_files_changed.emit(sorted(self._staged_files))

    def stage_file(self, path: str) -> None:
        """Add ``path`` to the index (``git add <path>``).

        For files deleted from the working tree the method uses
        ``index.remove()`` instead of ``index.add()`` because libgit2's
        ``git_index_add_bypath`` cannot stat a non-existent file.

        On success :attr:`staged_files_changed` is emitted (via
        :meth:`refresh_status`). Errors are surfaced through
        :attr:`error_occurred`.

        R3.2 (P5): when ``_batch_refresh`` is enabled (set by
        :meth:`set_batch_refresh` from
        :meth:`MainViewModel.stage_all_unstaged`) the per-file
        :meth:`refresh_status` and side-diff refresh are skipped
        so the batch runs in O(n) pygit2 ops instead of O(n²).  The
        batch owner is responsible for the trailing refresh.
        """
        if self._repo is None or not self._repo.is_open:
            return
        try:
            if self._is_deleted_from_disk(self._repo, path):
                self._repo.repo.index.remove(path)
            else:
                self._repo.repo.index.add(path)
            self._repo.repo.index.write()
        except (pygit2.GitError, OSError, KeyError) as exc:
            self.error_occurred.emit(f"Failed to stage {path!r}: {exc}")
            return
        if self._batch_refresh:
            return
        self.refresh_status()
        self._refresh_selected_file_side(path, prefer_staged=True)

    def set_batch_refresh(self, enabled: bool) -> None:
        """Suppress per-file refreshes inside ``stage_file`` / ``unstage_file``.

        R3.2 (P5): the batch verbs in :class:`MainViewModel` flip
        this on while iterating, then call :meth:`refresh_status`
        exactly once at the end.  This keeps a 1000-file batch
        O(n) pygit2 operations instead of O(n²) (each per-file
        call used to refresh the full status independently).
        """
        self._batch_refresh = bool(enabled)

    def recompute_selected_diff(self) -> None:
        """Refresh the selected file's diff and keep the side coherent.

        R3.2 (P5): when the batch verbs (``stage_all_unstaged`` /
        ``unstage_all_staged``) finish, the per-file
        :meth:`stage_file` / :meth:`unstage_file` no longer refresh
        the selected side inline.  This method is the batch's
        trailing re-emit: it re-runs :meth:`_compute_and_emit_diff`
        and, if the previously-selected side is now empty (e.g.
        unstaging switched the file from staged → unstaged and the
        user was on the staged side), flips to the side that still
        has the change.  ``selected_file_changed`` is also emitted so
        :class:`MainWindow` re-evaluates the line-action mode (the
        per-file refresh used to do this implicitly via the side
        change).
        """
        path = self._selected_file
        if path is None:
            return
        # If the side the user was on no longer has changes (the file
        # moved staged ↔ unstaged), flip to the side that does.
        if self._selected_file_staged and path not in self._staged_files:
            self._selected_file_staged = False
            self.selected_file_changed.emit(path)
        elif (
            not self._selected_file_staged
            and path in self._staged_files
            and path not in self._raw_status_with_unstaged()
        ):
            self._selected_file_staged = True
            self.selected_file_changed.emit(path)
        self._compute_and_emit_diff(path)

    def _raw_status_with_unstaged(self) -> set[str]:
        """Return the set of paths that have working-tree changes.

        R3.2 (P5): helper for :meth:`recompute_selected_diff` —
        ``_raw_status`` carries the pygit2 flag bitfield, so we
        inspect the WT_* bits to decide whether the file has
        worktree-only changes.
        """
        wt = (
            pygit2.GIT_STATUS_WT_MODIFIED
            | pygit2.GIT_STATUS_WT_DELETED
            | pygit2.GIT_STATUS_WT_NEW
            | pygit2.GIT_STATUS_WT_RENAMED
            | pygit2.GIT_STATUS_WT_TYPECHANGE
        )
        return {p for p, flag in self._raw_status.items() if flag & wt}

    @staticmethod
    def _is_deleted_from_disk(repo: RepositoryManager, path: str) -> bool:
        """Return ``True`` if *path* was tracked in HEAD but is gone from the worktree."""
        workdir = repo.repo.workdir
        if workdir is None:
            return False
        from pathlib import Path as _Path
        if (_Path(workdir) / path).exists():
            return False
        try:
            repo.repo.revparse_single(f"HEAD:{path}")
        except (KeyError, pygit2.GitError, ValueError):
            return False
        return True

    def unstage_file(self, path: str) -> None:
        """Reset the index entry for ``path`` back to ``HEAD``.

        Wraps :func:`src.core.operations.unstage_changes` (which uses
        ``git reset HEAD -- <path>`` under the hood) so the index entry
        matches the HEAD tree for tracked files. For files that are in
        the index but not in HEAD (e.g. freshly-added untracked),
        they're dropped from the index instead.

        Errors are surfaced through :attr:`error_occurred`.

        R3.2 (P5): when ``_batch_refresh`` is enabled, the per-file
        trailing refresh is skipped — see :meth:`stage_file` and
        :meth:`set_batch_refresh` for the rationale.
        """
        if self._repo is None or not self._repo.is_open:
            return
        from src.core.operations import unstage_changes

        try:
            unstage_changes(self._repo, path)
        except GitError as exc:
            self.error_occurred.emit(f"Failed to unstage {path!r}: {exc}")
            return
        if self._batch_refresh:
            return
        self.refresh_status()
        self._refresh_selected_file_side(path, prefer_staged=False)

    def select_file(self, path: str | None, staged: bool = False) -> None:
        """Set the file whose diff is shown in the preview pane.

        ``staged=True`` computes the diff between the index and HEAD
        (i.e. what *is* staged), rather than the working tree vs HEAD.
        """
        self._selected_file = path
        self._selected_file_staged = staged if path is not None else False
        self.selected_file_changed.emit(path)
        self._compute_and_emit_diff(path)

    def refresh_selected_diff(self) -> None:
        """Recompute the currently selected file diff without changing selection."""
        self._compute_and_emit_diff(self._selected_file)

    def set_commit_summary(self, text: str) -> None:
        """Update the commit summary; emits :attr:`commit_summary_changed`
        and the combined :attr:`commit_message_changed`."""
        if text == self._commit_summary:
            return
        self._commit_summary = text
        self.commit_summary_changed.emit(text)
        self.commit_message_changed.emit(self.combined_commit_message())

    def set_commit_description(self, text: str) -> None:
        """Update the commit description; emits
        :attr:`commit_description_changed` and the combined
        :attr:`commit_message_changed`."""
        if text == self._commit_description:
            return
        self._commit_description = text
        self.commit_description_changed.emit(text)
        self.commit_message_changed.emit(self.combined_commit_message())

    def set_commit_message(self, text: str) -> None:
        """Backwards-compat alias — set the *summary* from a full message.

        Older callers (and the test suite) feed a single string; we
        keep that working by treating the input as the summary and
        clearing the description. New code should prefer
        :meth:`set_commit_summary` / :meth:`set_commit_description`.
        """
        if text == self._commit_summary and self._commit_description == "":
            return
        self._commit_summary = text
        self._commit_description = ""
        self.commit_summary_changed.emit(text)
        self.commit_description_changed.emit("")
        self.commit_message_changed.emit(self.combined_commit_message())

    def clear_commit_input(self) -> None:
        """Reset both fields to empty in a single, signal-coherent step.

        Used by :meth:`MainViewModel.commit_changes` after a successful
        commit so the next commit starts from a clean slate.
        """
        self._commit_summary = ""
        self._commit_description = ""
        self.commit_summary_changed.emit("")
        self.commit_description_changed.emit("")
        self.commit_message_changed.emit("")

    # ----- internals ---------------------------------------------------

    def _refresh_selected_file_side(self, path: str, *, prefer_staged: bool) -> None:
        if self._selected_file != path:
            return
        flag = self._raw_status.get(path, pygit2.GIT_STATUS_CURRENT)
        has_staged = bool(flag & _STAGED_FLAGS)
        has_unstaged = bool(flag & _UNSTAGED_FLAGS)
        if prefer_staged and has_staged:
            self.select_file(path, staged=True)
        elif not prefer_staged and has_unstaged:
            self.select_file(path, staged=False)
        elif has_staged:
            self.select_file(path, staged=True)
        elif has_unstaged:
            self.select_file(path, staged=False)
        else:
            self.select_file(None)

    @staticmethod
    def _compute_staged_files_from_raw(raw_status: dict[str, int]) -> set[str]:
        """Rebuild the staged set from a pre-fetched ``pygit2`` status dict."""
        return {path for path, flag in raw_status.items() if flag & _STAGED_FLAGS}

    @staticmethod
    def _worktree_status(flag: int) -> FileStatus | None:
        if flag & pygit2.GIT_STATUS_CONFLICTED:
            return FileStatus.CONFLICTED
        if flag & pygit2.GIT_STATUS_WT_NEW:
            return FileStatus.UNTRACKED
        if flag & pygit2.GIT_STATUS_WT_RENAMED:
            return FileStatus.RENAMED
        if flag & pygit2.GIT_STATUS_WT_DELETED:
            return FileStatus.DELETED
        if flag & pygit2.GIT_STATUS_WT_TYPECHANGE:
            return FileStatus.TYPE_CHANGED
        if flag & pygit2.GIT_STATUS_WT_MODIFIED:
            return FileStatus.MODIFIED
        return None

    @staticmethod
    def _index_status(flag: int) -> FileStatus | None:
        if flag & pygit2.GIT_STATUS_CONFLICTED:
            return FileStatus.CONFLICTED
        if flag & pygit2.GIT_STATUS_INDEX_NEW:
            return FileStatus.NEW
        if flag & pygit2.GIT_STATUS_INDEX_RENAMED:
            return FileStatus.RENAMED
        if flag & pygit2.GIT_STATUS_INDEX_DELETED:
            return FileStatus.DELETED
        if flag & pygit2.GIT_STATUS_INDEX_TYPECHANGE:
            return FileStatus.TYPE_CHANGED
        if flag & pygit2.GIT_STATUS_INDEX_MODIFIED:
            return FileStatus.MODIFIED
        return None

    @staticmethod
    def _compute_status_data(
        repo: RepositoryManager,
    ) -> tuple[list[FileChange], set[str], dict[str, int]]:
        """Read and return file changes, staged paths, and raw status flags."""
        raw_status = repo.repo.status()
        file_changes = repo.get_status_from_raw(raw_status)
        staged = CommitPanelViewModel._compute_staged_files_from_raw(raw_status)
        return file_changes, staged, dict(raw_status)

    def _compute_staged_files(self) -> set[str]:
        """Rebuild the staged set from the raw ``pygit2`` status flags.

        :meth:`RepositoryManager.get_status` collapses staged and
        worktree-only variants into a single :class:`FileStatus`
        (``MODIFIED``, ``DELETED``, ...). We re-read the raw flag
        bitfield here to keep the staged-vs-not distinction.

        Kept for backwards compatibility; new code should prefer
        :meth:`_compute_staged_files_from_raw` to avoid a duplicate
        ``repo.status()`` call.
        """
        try:
            return self._compute_staged_files_from_raw(self._repo.repo.status())
        except (GitError, pygit2.GitError, OSError) as exc:
            self.error_occurred.emit(str(exc))
            return set()

    def _compute_and_emit_diff(self, path: str | None) -> None:
        """Compute the diff for ``path`` and emit the diff signals.

        Emits :attr:`diff_ready` (changes-only text) eagerly so the
        default diff view is responsive; the *full document* variant
        is computed lazily on :meth:`request_full_document` because
        rendering 2^31 context lines is expensive on large files
        (R3.2 P4). For backwards compatibility we still emit
        :attr:`diff_pair_ready` with an empty ``full_document`` here
        and rely on the widget to call :meth:`request_full_document`
        when the user toggles into full-document mode.

        For untracked files the "full document" view is just the file
        itself, so both variants are identical to avoid showing an
        empty editor when the user toggles modes on such a file.
        """
        if self._repo is None or not self._repo.is_open or path is None:
            self._current_diff = ""
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")
            return
        try:
            changes_only = self.build_diff_text(
                path,
                staged=self._selected_file_staged,
                context_lines=3,
            )
        except GitError as exc:
            self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
            self._current_diff = ""
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")
            return
        self._current_diff = changes_only
        # Track the selected path so a subsequent ``request_full_document``
        # can recompute the full variant lazily (R3.2 P4).
        self._current_diff_path = path
        self._current_diff_staged = self._selected_file_staged
        self.diff_ready.emit(changes_only)
        # Full document is intentionally empty here — the widget will
        # trigger ``request_full_document()`` on mode switch.  We send
        # an empty second slot so listeners that compare both strings
        # still receive *some* signal.
        self.diff_pair_ready.emit(changes_only, "")

    def request_full_document(self) -> None:
        """Recompute and emit the full-document diff for the current selection.

        R3.2 (P4): ``_compute_and_emit_diff`` used to build both
        changes-only and full-document eagerly.  ``full_document`` is
        expensive (2^31 context lines) and only matters when the user
        has toggled the right-panel viewer into "full document" mode,
        so we now defer it to this explicit request.  Emits
        :attr:`diff_pair_ready` with the recomputed text.
        """
        path = getattr(self, "_current_diff_path", None)
        if (
            self._repo is None
            or not self._repo.is_open
            or path is None
            or self._current_diff == ""
        ):
            return
        try:
            full_document = self.build_diff_text(
                path,
                staged=self._current_diff_staged,
                context_lines=_FULL_DOCUMENT_CONTEXT_LINES,
            )
        except GitError as exc:
            self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
            return
        self.diff_pair_ready.emit(self._current_diff, full_document)

    def build_diff_text(
        self,
        path: str,
        staged: bool = False,
        context_lines: int = 3,
    ) -> str:
        """Return the unified diff for ``path``.

        When ``staged=False`` (default), shows the tracked worktree
        changes that are not represented in the index. When
        ``staged=True``, shows the index diff against ``HEAD`` — what
        would be committed if you ran ``git commit`` right now.

        ``context_lines`` controls how many unchanged lines surround
        each change: ``3`` (the default) produces a compact, change-
        focused diff suitable for review; a very large value
        (e.g. ``2**31 - 1``, used by the *Full document* viewer mode)
        makes the surrounding hunks grow until they span the whole
        file, so the entire document is rendered with diff colouring.

        Public — the *Copy Diff* context-menu action in the right
        panel's commit-input view calls this to grab the text that
        gets pushed onto the system clipboard.
        """
        repo = self._repo.repo
        if not staged and self._is_untracked(path):
            return self._untracked_diff_text(path)
        if self._is_binary(path):
            label = "staged" if staged else "HEAD"
            return f"Binary file {path} differs from {label}.\n"
        try:
            flags = (
                pygit2.enums.DiffOption.INCLUDE_UNTRACKED
                | pygit2.enums.DiffOption.RECURSE_UNTRACKED_DIRS
            )
            diff = repo.diff(
                "HEAD",
                cached=staged,
                context_lines=context_lines,
                flags=flags,
            )
        except (pygit2.GitError, KeyError) as exc:
            raise GitError(str(exc)) from exc
        text = self._extract_patch_for(diff, path)
        if staged or not text:
            return text
        return self._without_staged_diff_lines(path, text)

    def _is_untracked(self, path: str) -> bool:
        """Return ``True`` if ``path`` is not present in the index/HEAD tree."""
        try:
            self._repo.repo.revparse_single(f"HEAD:{path}")
        except (KeyError, pygit2.GitError, ValueError):
            return True
        return False

    @staticmethod
    def _extract_patch_for(diff, path: str) -> str:  # noqa: ANN001 - pygit2.Diff
        """Return the patch text for ``path`` from a multi-file ``pygit2.Diff``.

        pygit2 1.x's ``Diff`` is iterable over :class:`Patch` objects;
        each has a ``.delta.new_file.path`` / ``.delta.old_file.path``
        we can match against. Concatenates the per-file patch strings
        when both sides of a rename point at ``path``.
        """
        pieces: list[str] = []
        for patch in diff:
            delta = patch.delta
            if (delta.new_file.path == path) or (delta.old_file.path == path):
                pieces.append(patch.text or "")
        return "".join(pieces)

    def _without_staged_diff_lines(self, path: str, text: str) -> str:
        repo = self._repo.repo
        try:
            staged_diff = repo.diff("HEAD", cached=True, context_lines=3)
        except (pygit2.GitError, KeyError) as exc:
            raise GitError(str(exc)) from exc
        staged_text = self._extract_patch_for(staged_diff, path)
        return filter_staged_diff_lines(text, staged_text)[0]

    def _is_binary(self, path: str) -> bool:
        """Best-effort binary detection: read up to 8 KiB and look for NUL bytes."""
        from pathlib import Path as _Path

        workdir = self._repo.repo.workdir
        if workdir is None:
            return False
        try:
            blob = (_Path(workdir) / path).read_bytes()[:8192]
        except OSError:
            return False
        return b"\x00" in blob

    def _untracked_diff_text(self, path: str) -> str:
        """Produce a unified-diff-shaped string for an untracked file."""
        from pathlib import Path as _Path

        workdir = self._repo.repo.workdir
        full = _Path(workdir) / path if workdir is not None else None
        if full is None or not full.exists():
            return f"New file: {path} (not found on disk)\n"
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise GitError(f"Cannot read {path}: {exc}") from exc
        new_lines = content.splitlines() or [""]
        added = "\n".join(f"+{line}" for line in new_lines)
        header = (
            f"diff --git a/{path} b/{path}\n"
            f"new file mode 100644\n"
            f"--- /dev/null\n"
            f"+++ b/{path}\n"
            f"@@ -0,0 +1,{len(new_lines)} @@\n"
        )
        return header + added + "\n"


__all__ = ["CommitPanelViewModel"]
