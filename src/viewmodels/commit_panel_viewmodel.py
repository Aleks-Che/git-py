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
* ``commit_message`` — the text the user is typing.
* ``selected_file`` / ``current_diff`` — what the diff preview is
  showing.

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

    commit_message_changed = Signal(str)
    """Emitted when ``commit_message`` changes."""

    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo: RepositoryManager | None = None
        self._file_changes: list[FileChange] = []
        self._staged_files: set[str] = set()
        self._selected_file: str | None = None
        self._current_diff: str | None = None
        self._commit_message: str = ""

    # ----- read-only state (properties) --------------------------------

    def file_changes(self) -> list[FileChange]:
        """Return a copy of the current working-tree / index status."""
        return list(self._file_changes)

    def staged_files(self) -> list[str]:
        """Return the sorted list of currently staged paths."""
        return sorted(self._staged_files)

    def selected_file(self) -> str | None:
        return self._selected_file

    def current_diff(self) -> str | None:
        return self._current_diff

    def commit_message(self) -> str:
        return self._commit_message

    # ----- repository binding -----------------------------------------

    def set_repository(self, manager: RepositoryManager | None) -> None:
        """Bind (or unbind) the repository the panel reads from.

        On every bind the message field and the file selection are
        cleared and the status is refreshed. ``manager=None`` is the
        close path: the panel becomes empty.
        """
        self._repo = manager
        self._selected_file = None
        self._current_diff = None
        self._commit_message = ""
        self.selected_file_changed.emit(None)
        self.diff_ready.emit("")
        self.commit_message_changed.emit("")
        self.refresh_status()

    # ----- verb methods ------------------------------------------------

    def refresh_status(self) -> None:
        """Re-read the working-tree status and rebuild the staged set.

        Translates :class:`GitError` into :attr:`error_occurred` —
        never re-raises. Emits :attr:`file_changes_changed` and
        :attr:`staged_files_changed` exactly once each.
        """
        if self._repo is None or not self._repo.is_open:
            self._file_changes = []
            self._staged_files = set()
        else:
            try:
                self._file_changes = self._repo.get_status()
                self._staged_files = self._compute_staged_files()
            except GitError as exc:
                self.error_occurred.emit(str(exc))
                self._file_changes = []
                self._staged_files = set()
        self.file_changes_changed.emit()
        self.staged_files_changed.emit(sorted(self._staged_files))

    def stage_file(self, path: str) -> None:
        """Add ``path`` to the index (``git add <path>``).

        On success :attr:`staged_files_changed` is emitted (via
        :meth:`refresh_status`). Errors are surfaced through
        :attr:`error_occurred`.
        """
        if self._repo is None or not self._repo.is_open:
            return
        try:
            self._repo.repo.index.add(path)
            self._repo.repo.index.write()
        except (pygit2.GitError, OSError, KeyError) as exc:
            self.error_occurred.emit(f"Failed to stage {path!r}: {exc}")
            return
        self.refresh_status()

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

    def select_file(self, path: str | None) -> None:
        """Set the file whose diff is shown in the preview pane."""
        self._selected_file = path
        self.selected_file_changed.emit(path)
        self._compute_and_emit_diff(path)

    def set_commit_message(self, text: str) -> None:
        """Update the commit message; emits :attr:`commit_message_changed`."""
        if text == self._commit_message:
            return
        self._commit_message = text
        self.commit_message_changed.emit(text)

    # ----- internals ---------------------------------------------------

    def _compute_staged_files(self) -> set[str]:
        """Rebuild the staged set from the raw ``pygit2`` status flags.

        :meth:`RepositoryManager.get_status` collapses staged and
        worktree-only variants into a single :class:`FileStatus`
        (``MODIFIED``, ``DELETED``, ...). We re-read the raw flag
        bitfield here to keep the staged-vs-not distinction.
        """
        result: set[str] = set()
        for path, flag in self._repo.repo.status().items():
            if flag & _STAGED_FLAGS:
                result.add(path)
        return result

    def _compute_and_emit_diff(self, path: str | None) -> None:
        """Compute the worktree-vs-HEAD diff for ``path`` and emit :attr:`diff_ready`."""
        if self._repo is None or not self._repo.is_open or path is None:
            self._current_diff = ""
            self.diff_ready.emit("")
            return
        try:
            text = self._build_diff_text(path)
        except GitError as exc:
            self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
            self._current_diff = ""
            self.diff_ready.emit("")
            return
        self._current_diff = text
        self.diff_ready.emit(text)

    def _build_diff_text(self, path: str) -> str:
        """Return the unified diff for ``path`` (HEAD vs worktree).

        pygit2 1.x's :meth:`Repository.diff` doesn't accept a
        ``pathspec`` argument, so we build the full workdir-vs-HEAD
        diff (with ``INCLUDE_UNTRACKED``) and then walk the patches
        to find the entry for ``path``.

        Untracked files are synthesised as a unified-diff header
        followed by ``+`` lines for every line of the file. Binary
        files produce a one-line placeholder.
        """
        repo = self._repo.repo
        if self._is_untracked(path):
            return self._untracked_diff_text(path)
        if self._is_binary(path):
            return f"Binary file {path} differs from HEAD.\n"
        try:
            diff = repo.diff(
                "HEAD",
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
