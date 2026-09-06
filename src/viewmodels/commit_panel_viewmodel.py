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

from functools import partial
from pathlib import Path

import pygit2
from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from src.core.exceptions import GitError
from src.core.file_diff import read_workdir_file_diff, workdir_file_diff
from src.core.models import FileChange, FileStatus
from src.core.repository import RepositoryManager
from src.core.staged_diff import read_staged_snapshot, staged_identity
from src.utils.ai_client import AIClient, AIError, CommitMessage
from src.utils.ai_config import AISettings
from src.utils.async_worker import AsyncWorker
from src.utils.config import default_config_path, load_config
from src.utils.image_preview import is_image_path
from src.utils.latest_worker import LatestWorker


def _generate_message(path: str, identity: str, settings: AISettings) -> CommitMessage:
    snapshot = read_staged_snapshot(path, settings.max_diff_chars)
    if snapshot.identity != identity:
        raise GitError("Staged changes moved before generation started. Please try again.")
    return AIClient(settings).generate_commit_message(snapshot.diff, snapshot.branch)

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

    diff_loading_changed = Signal(bool)

    diff_ready = Signal(str)
    """Emitted with the unified-diff text for the selected file."""

    image_requested = Signal(str, bool)
    """Ask the main VM to load an image (path, staged), including after refresh."""

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
    generation_busy_changed = Signal(bool)
    generation_status_changed = Signal(str)

    def __init__(
        self, parent: QObject | None = None, *, config_path: Path | str | None = None,
        async_enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._config_path = config_path
        self._async_enabled = async_enabled
        self._diff_loader = LatestWorker(self)
        self._diff_loader.busy_changed.connect(self.diff_loading_changed)
        self._diff_loader.finished.connect(self._on_diff_loaded)
        self._diff_loader.failed.connect(self._on_diff_failed)
        self._full_document_requested = False
        self._current_diff_path: str | None = None
        self._current_diff_staged = False
        self._generation_worker: AsyncWorker | None = None
        self._input_revision = 0
        self._generation_revision = 0
        self._generation_path = ""
        self._generation_identity = ""
        self.commit_message_changed.connect(self.invalidate_generation)
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
        if path is None or is_image_path(path) or self._repo is None or not self._repo.is_open:
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

    def _is_binary(self, path: str) -> bool:
        """Probe a small prefix, without reading an entire large file on a click."""
        workdir = self._repo.repo.workdir
        if workdir is None:
            return False
        try:
            with (Path(workdir) / path).open("rb") as stream:
                return b"\0" in stream.read(8192)
        except OSError:
            return False

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
        self.cancel_diff_loading()
        self._repo = manager
        self.generation_status_changed.emit("")
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

    @property
    def is_generating(self) -> bool:
        return self._generation_worker is not None

    def invalidate_generation(self, *_args) -> None:
        """Prevent a pending result from overwriting newer input or repository state."""
        self._input_revision += 1

    def generate_commit_message(self) -> None:
        if self.is_generating:
            return
        if self._repo is None or not self._repo.is_open or not self._repo.path:
            self.error_occurred.emit("Open a repository before generating a commit message.")
            return
        try:
            config = load_config(self._config_path or default_config_path())
            settings = AISettings.from_config(config)
            if not settings.base_url.strip() or not settings.model.strip():
                raise AIError("Configure the API URL and model in Settings → AI first.")
            AIClient(settings)  # Validate before starting a worker.
            self._generation_path = self._repo.path
            self._generation_identity = staged_identity(self._generation_path)
        except (AIError, GitError) as exc:
            self.error_occurred.emit(str(exc))
            return
        self._generation_revision = self._input_revision
        worker = AsyncWorker(
            _generate_message, self._generation_path, self._generation_identity, settings,
        )
        self._generation_worker = worker
        worker.signals.finished.connect(self._on_generation_result)
        worker.signals.failed.connect(self._on_generation_error)
        worker.signals.lifespan_finished.connect(self._on_generation_finished)
        self.generation_status_changed.emit("Generating summary and description…")
        self.generation_busy_changed.emit(True)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _on_generation_result(self, message: CommitMessage) -> None:
        if self._generation_revision != self._input_revision:
            self.generation_status_changed.emit("Input or repository changed; generate again.")
            return
        try:
            if staged_identity(self._generation_path) != self._generation_identity:
                self.generation_status_changed.emit("Staged changes changed; generate again.")
                return
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        # Publish one coherent message, with both fields already updated for listeners.
        self._commit_summary = message.summary
        self._commit_description = message.description
        self.commit_summary_changed.emit(message.summary)
        self.commit_description_changed.emit(message.description)
        self.commit_message_changed.emit(self.combined_commit_message())
        self.generation_status_changed.emit(
            "Generated from staged changes. Review before committing.",
        )

    @Slot(object)
    def _on_generation_error(self, error: Exception) -> None:
        if self._generation_revision != self._input_revision:
            self.generation_status_changed.emit("Input or repository changed; generate again.")
            return
        message = str(error) if isinstance(error, AIError | GitError) else "LLM generation failed."
        self.generation_status_changed.emit("Generation failed; your message was preserved.")
        self.error_occurred.emit(message)

    @Slot()
    def _on_generation_finished(self) -> None:
        self._generation_worker = None
        self.generation_busy_changed.emit(False)

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

    def cancel_diff_loading(self) -> None:
        self._diff_loader.invalidate()
        self._current_diff_path = None
        self._full_document_requested = False

    def _compute_and_emit_diff(self, path: str | None) -> None:
        self.cancel_diff_loading()
        self._current_diff = ""
        if self._repo is None or not self._repo.is_open or path is None:
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")
            return
        if is_image_path(path):
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")
            self.image_requested.emit(path, self._selected_file_staged)
            return
        self._current_diff_path = path
        self._current_diff_staged = self._selected_file_staged
        self._request_diff(path, 3)

    def request_full_document(self) -> None:
        path = self._current_diff_path
        if path is None or not self._current_diff or self._full_document_requested:
            return
        self._full_document_requested = True
        self._request_diff(path, _FULL_DOCUMENT_CONTEXT_LINES)

    def _request_diff(self, path: str, context_lines: int) -> None:
        key = (path, context_lines)
        if self._async_enabled:
            self._diff_loader.submit(key, partial(
                read_workdir_file_diff, self._repo.path, path,
                self._current_diff_staged, context_lines,
            ))
            return
        try:
            text = self.build_diff_text(path, self._current_diff_staged, context_lines)
        except GitError as exc:
            self._on_diff_failed(key, exc)
        else:
            self._on_diff_loaded(key, text)

    @Slot(object, object)
    def _on_diff_loaded(self, key: tuple, text: str) -> None:
        _, context_lines = key
        if context_lines == _FULL_DOCUMENT_CONTEXT_LINES:
            self._full_document_requested = False
            self.diff_pair_ready.emit(self._current_diff or "", text)
        else:
            self._current_diff = text
            self.diff_ready.emit(text)
            self.diff_pair_ready.emit(text, "")

    @Slot(object, object)
    def _on_diff_failed(self, key: tuple, error: object) -> None:
        path, context_lines = key
        self.error_occurred.emit(f"Failed to diff {path!r}: {error}")
        if context_lines == _FULL_DOCUMENT_CONTEXT_LINES:
            self._full_document_requested = False
            # Keep the compact preview on failure; do not start an automatic retry loop.
            self.diff_pair_ready.emit(self._current_diff or "", "")
        else:
            self._current_diff = ""
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")

    def build_diff_text(
        self, path: str, staged: bool = False, context_lines: int = 3,
    ) -> str:
        """Read a patch synchronously for copy actions and non-async callers."""
        return workdir_file_diff(self._repo, path, staged, context_lines)


__all__ = ["CommitPanelViewModel"]
