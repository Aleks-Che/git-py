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

from src.core.exceptions import GitError
from src.core.models import FileChange
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
        self._staged_files: set[str] = set()
        self._selected_file: str | None = None
        self._selected_file_staged: bool = False
        self._current_diff: str | None = None
        self._commit_summary: str = ""
        self._commit_description: str = ""

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
        """Return the sorted list of paths that are in the working tree
        but **not** currently recorded in the index.

        Used by the right panel's *Stage All Changes* button. Files
        that are already staged are filtered out.
        """
        staged = self._staged_files
        return sorted(c.path for c in self._file_changes if c.path not in staged)

    def unstaged_files(self) -> list[FileChange]:
        """Return the :class:`FileChange` records that are not yet staged."""
        staged = self._staged_files
        return [c for c in self._file_changes if c.path not in staged]

    def staged_files_detailed(self) -> list[FileChange]:
        """Return the :class:`FileChange` records for staged paths.

        The result keeps the original :class:`FileStatus` from
        ``file_changes``; callers that need a per-file ``M`` / ``A``
        / ``D`` badge read this directly.
        """
        staged = self._staged_files
        return [c for c in self._file_changes if c.path in staged]

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
        self._selected_file = None
        self._selected_file_staged = False
        self._current_diff = None
        self._commit_summary = ""
        self._commit_description = ""
        self.selected_file_changed.emit(None)
        self.diff_ready.emit("")
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
            self._staged_files = set()
        else:
            try:
                raw_status = self._repo.repo.status()
                self._file_changes = self._repo.get_status_from_raw(raw_status)
                self._staged_files = self._compute_staged_files_from_raw(raw_status)
            except GitError as exc:
                self.error_occurred.emit(str(exc))
                self._file_changes = []
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
        self.refresh_status()

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
        """
        if self._repo is None or not self._repo.is_open:
            return
        from src.core.operations import unstage_changes

        try:
            unstage_changes(self._repo, path)
        except GitError as exc:
            self.error_occurred.emit(f"Failed to unstage {path!r}: {exc}")
            return
        self.refresh_status()

    def select_file(self, path: str | None, staged: bool = False) -> None:
        """Set the file whose diff is shown in the preview pane.

        ``staged=True`` computes the diff between the index and HEAD
        (i.e. what *is* staged), rather than the working tree vs HEAD.
        """
        self._selected_file = path
        self._selected_file_staged = staged if path is not None else False
        self.selected_file_changed.emit(path)
        self._compute_and_emit_diff(path)

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

    @staticmethod
    def _compute_staged_files_from_raw(raw_status: dict[str, int]) -> set[str]:
        """Rebuild the staged set from a pre-fetched ``pygit2`` status dict."""
        return {path for path, flag in raw_status.items() if flag & _STAGED_FLAGS}

    @staticmethod
    def _compute_status_data(
        repo: RepositoryManager,
    ) -> tuple[list[FileChange], set[str]]:
        """Read working-tree status from *repo* and return
        ``(file_changes, staged_files)``.

        Pure data-in/data-out — no signal emissions, safe to call
        from a background thread.
        """
        raw_status = repo.repo.status()
        file_changes: list[FileChange] = []
        for path, flag in raw_status.items():
            file_changes.append(FileChange(path=path, status=repo._map_status(flag)))
        staged = CommitPanelViewModel._compute_staged_files_from_raw(raw_status)
        return file_changes, staged

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
        return self._compute_staged_files_from_raw(self._repo.repo.status())

    def _compute_and_emit_diff(self, path: str | None) -> None:
        """Compute the diff for ``path`` and emit :attr:`diff_ready`."""
        if self._repo is None or not self._repo.is_open or path is None:
            self._current_diff = ""
            self.diff_ready.emit("")
            return
        try:
            text = self.build_diff_text(path, staged=self._selected_file_staged)
        except GitError as exc:
            self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
            self._current_diff = ""
            self.diff_ready.emit("")
            return
        self._current_diff = text
        self.diff_ready.emit(text)

    def build_diff_text(self, path: str, staged: bool = False) -> str:
        """Return the unified diff for ``path``.

        When ``staged=False`` (default) shows the working-tree diff
        (worktree vs HEAD).  When ``staged=True`` shows the index diff
        (index vs HEAD) — what would be committed if you ran ``git
        commit`` right now.

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
            diff = repo.diff(
                "HEAD",
                cached=staged,
                context_lines=3,
                flags=pygit2.enums.DiffOption.INCLUDE_UNTRACKED
                | pygit2.enums.DiffOption.RECURSE_UNTRACKED_DIRS,
            )
        except (pygit2.GitError, KeyError) as exc:
            raise GitError(str(exc)) from exc
        return self._extract_patch_for(diff, path)

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
