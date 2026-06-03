"""ViewModel for the commit graph.

Bridges the pure-Python :mod:`src.core.graph` layout engine and the
Qt-flavoured UI: holds a reference to a :class:`RepositoryManager`,
recomputes the lane layout whenever the repository changes, and
exposes the result plus user-driven ``commit_selected`` events as
Qt signals so any number of widgets can listen.

Stage 2 keeps the ViewModel standalone — there's no
:class:`src.viewmodels.main_viewmodel.MainViewModel` yet to own the
repository. :class:`src.ui.main_window.MainWindow` is expected to
construct a ``GraphViewModel`` itself and call :meth:`set_repository`
when the user opens a repo. Stage 3 will switch to wiring it through
the central ``MainViewModel``.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.core.exceptions import GitError
from src.core.graph import compute_layout, nodes_to_rows
from src.core.models import CommitInfo
from src.core.repository import RepositoryManager


class GraphViewModel(QObject):
    """Drives the commit graph view.

    Signals
    -------
    graph_updated(list[dict])
        Emitted with the new layout after :meth:`refresh_graph` (or
        :meth:`set_repository`) finishes. The payload is a list of
        plain dicts — one per commit, oldest entry is the oldest
        commit — safe to consume on any thread.
    commit_selected(str)
        Emitted when the user picks a commit in the view. Carries
        the commit's full SHA.
    error_occurred(str)
        Emitted when a Core call raises a :class:`GitError`. The
        UI surfaces this as a status-bar message or a dialog.
    """

    graph_updated = Signal(list)
    commit_selected = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, repo_manager: RepositoryManager | None = None, parent=None) -> None:
        super().__init__(parent)
        self._repo: RepositoryManager | None = repo_manager

    # ----- repository binding -------------------------------------------

    def set_repository(self, manager: RepositoryManager | None) -> None:
        """Bind (or unbind) the repository the ViewModel reads from.

        Setting a new repository automatically refreshes the graph.
        Passing ``None`` clears the graph (emits an empty list).
        """
        self._repo = manager
        self.refresh_graph()

    def repository(self) -> RepositoryManager | None:
        """Return the currently bound repository, or ``None``."""
        return self._repo

    # ----- commands (verb methods) --------------------------------------

    def refresh_graph(self) -> None:
        """Recompute the graph layout and emit :attr:`graph_updated`.

        Silently emits an empty list when no repository is bound
        or the repository is empty. Any :class:`GitError` from Core
        is translated to :attr:`error_occurred` — never re-raised.
        """
        if self._repo is None or not self._repo.is_open:
            self.graph_updated.emit([])
            return
        try:
            history = self._repo.get_all_history()
            branches = self._repo.branches
            tags = self._repo.tags
            head_target, head_shorthand = self._head_info()
            nodes = compute_layout(history, branches, tags, head_target, head_shorthand)
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        self.graph_updated.emit(nodes_to_rows(nodes))

    def select_commit(self, sha: str) -> None:
        """Forward a user click on a commit to :attr:`commit_selected`."""
        self.commit_selected.emit(sha)

    def get_commit_details(self, sha: str) -> CommitInfo | None:
        """Resolve ``sha`` to a :class:`CommitInfo` for the detail panel.

        Returns ``None`` if no repository is bound or the SHA cannot
        be resolved; the caller is expected to clear its display in
        that case. We deliberately don't emit :attr:`error_occurred`
        for an unknown SHA — the click is a normal user action and
        shouldn't pop a dialog.
        """
        if self._repo is None or not self._repo.is_open:
            return None
        try:
            return self._repo.get_commit(sha)
        except GitError:
            return None

    # ----- internals ----------------------------------------------------

    def _head_info(self) -> tuple[str | None, str | None]:
        """Return ``(head_target_sha, head_shorthand)`` for the open repo.

        ``head_target_sha`` is ``None`` when HEAD is unborn. The
        shorthand is the symbolic ref name (``"main"`` on a branch,
        ``"(detached)"`` when detached, ``None`` if unborn).
        """
        if self._repo is None or self._repo.repo.head_is_unborn:
            return None, None
        head = self._repo.repo.head
        return str(head.target), head.shorthand
