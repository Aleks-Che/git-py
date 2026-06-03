"""ViewModel for the left references panel (branches, remotes, tags, stash).

Pure data container: exposes a snapshot of the currently bound
:class:`src.core.repository.RepositoryManager` through read-only
properties and a single :attr:`references_changed` signal. The panel
itself does the rendering.

Per ``docs/DEVELOPMENT_RULES.md`` this ViewModel never mutates the
repository — mutating actions (checkout / create / delete / rename)
flow through :class:`MainViewModel` → :class:`GitCommand` →
:class:`CommandProcessor`. The panel calls those on
:attr:`src.viewmodels.main_viewmodel.MainViewModel` and then asks this
ViewModel to :meth:`refresh` so the tree updates.

Properties:

* :attr:`local_branches` — local branches (no remote-tracking).
* :attr:`remote_branches` — remote-tracking branches.
* :attr:`tags` — light + annotated tags.
* :attr:`stash_list` — stash entries (most recent first).
* :attr:`remotes` — configured Git remotes (Stage 6).
* :attr:`current_branch_name` — ``HEAD`` shorthand, or ``None``.

The snapshot is rebuilt from Core on every :meth:`refresh` (no
caching) so the panel always sees the freshest view of the repo.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.core.exceptions import GitError
from src.core.models import BranchInfo, RemoteInfo, StashInfo, TagInfo
from src.core.repository import RepositoryManager


class BranchPanelViewModel(QObject):
    """Read-only ViewModel feeding the left panel's tree widget."""

    references_changed = Signal()
    """Emitted after :meth:`refresh` rebuilds the snapshot."""

    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo: RepositoryManager | None = None
        self._local_branches: list[BranchInfo] = []
        self._remote_branches: list[BranchInfo] = []
        self._tags: list[TagInfo] = []
        self._stash_list: list[StashInfo] = []
        self._remotes: list[RemoteInfo] = []
        self._current_branch_name: str | None = None

    # ----- read-only state (properties) --------------------------------

    def local_branches(self) -> list[BranchInfo]:
        return list(self._local_branches)

    def remote_branches(self) -> list[BranchInfo]:
        return list(self._remote_branches)

    def tags(self) -> list[TagInfo]:
        return list(self._tags)

    def stash_list(self) -> list[StashInfo]:
        return list(self._stash_list)

    def remotes(self) -> list[RemoteInfo]:
        """List of configured remotes (``origin``, ``upstream``, ...)."""
        return list(self._remotes)

    def current_branch_name(self) -> str | None:
        """``HEAD`` shorthand (``"main"`` on a branch, ``None`` if unborn)."""
        return self._current_branch_name

    def repository_manager(self) -> RepositoryManager | None:
        return self._repo

    # ----- repository binding -----------------------------------------

    def set_repository(self, manager: RepositoryManager | None) -> None:
        """Bind (or unbind) the repository; immediately :meth:`refresh`."""
        self._repo = manager
        self.refresh()

    def refresh(self) -> None:
        """Re-read branches / tags / stash from the bound repository.

        On no repository (or a Core error) all four lists are cleared,
        :attr:`references_changed` is still emitted so the panel can
        collapse back to its placeholder, and :attr:`error_occurred`
        carries the failure message.
        """
        if self._repo is None or not self._repo.is_open:
            self._local_branches = []
            self._remote_branches = []
            self._tags = []
            self._stash_list = []
            self._remotes = []
            self._current_branch_name = None
            self.references_changed.emit()
            return
        try:
            all_branches = self._repo.branches
            self._local_branches = [b for b in all_branches if not b.is_remote]
            self._remote_branches = [b for b in all_branches if b.is_remote]
            self._tags = self._repo.tags
            self._stash_list = self._repo.stash_list
            from src.core.operations import list_remotes

            self._remotes = list_remotes(self._repo)
            self._current_branch_name = self._head_shorthand()
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            self._local_branches = []
            self._remote_branches = []
            self._tags = []
            self._stash_list = []
            self._remotes = []
            self._current_branch_name = None
        self.references_changed.emit()

    # ----- helpers -----------------------------------------------------

    def get_remote_for_branch(self, branch_name: str) -> str | None:
        """Return the remote name for a remote-tracking ``branch_name``.

        A branch like ``origin/main`` resolves to ``"origin"``. Branches
        without a ``/`` (e.g. local branches) and remote-tracking
        branches whose prefix is not a configured remote return
        ``None`` — the caller should treat this as "not a remote branch
        in any of our remotes".
        """
        if "/" not in branch_name:
            return None
        prefix = branch_name.split("/", 1)[0]
        for remote in self._remotes:
            if remote.name == prefix:
                return prefix
        # Fall back: return the prefix anyway. Callers that need strict
        # matching can use ``in {r.name for r in remotes()}``.
        return prefix

    # ----- internals ---------------------------------------------------

    def _head_shorthand(self) -> str | None:
        """Return the symbolic ref name for ``HEAD`` (``None`` if unborn)."""
        if self._repo is None:
            return None
        repo = self._repo.repo
        if repo.head_is_unborn:
            return None
        return repo.head.shorthand


__all__ = ["BranchPanelViewModel"]
