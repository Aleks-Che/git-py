"""ViewModel for the commit graph.

Bridges the pure-Python :mod:`src.core.graph` layout engine and the
Qt-flavoured UI: holds a reference to a :class:`RepositoryManager`,
recomputes the lane layout whenever the repository changes, and
exposes the result plus user-driven ``commit_selected`` events as
Qt signals so any number of widgets can listen.

Stage 3 also synthesises a "WIP" commit node whenever the working
tree has any uncommitted changes. The WIP node is a real
:class:`CommitInfo` with ``sha="WIP"`` and ``message="WIP:
Uncommitted changes"``; prepending it to the history lets
:func:`src.core.graph.compute_layout` lay it out above ``HEAD``
in the same lane. ``core/`` stays free of any knowledge about
working-tree status — the synthesis happens here, in the ViewModel.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from src.core.exceptions import GitError
from src.core.graph import compute_layout, nodes_to_rows
from src.core.models import CommitInfo, StashInfo
from src.core.repository import RepositoryManager

WIP_SHA = "WIP"
WIP_MESSAGE = "WIP: Uncommitted changes"


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
    search_results_changed = Signal(list)  # list[str] of matching SHA values

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

        If the working tree has any status entries (modified, new,
        deleted, ...), a virtual WIP commit is synthesised and
        prepended to the history so the user sees an uncommitted
        node above HEAD.
        """
        if self._repo is None or not self._repo.is_open:
            self.graph_updated.emit([])
            return
        try:
            history = self._repo.get_all_history()
            branches = self._repo.branches
            tags = self._repo.tags
            head_target, head_shorthand = self._head_info()
            status = self._repo.get_status()
        except GitError as exc:
            self.error_occurred.emit(str(exc))
            return
        # Insert stash entries chronologically among real commits.
        # We iterate newest-first so each stash is placed at the
        # correct time position. If the timestamp matches an existing
        # commit, the stash goes right after the last same-time commit.
        stash_entries = self._repo.stash_list
        for entry in stash_entries:
            stash_ci = self._stash_commit(entry, head_target)
            t = stash_ci.author_time
            idx = 0
            while idx < len(history) and history[idx].author_time > t:
                idx += 1
            while idx < len(history) and history[idx].author_time == t:
                idx += 1
            history.insert(idx, stash_ci)
        if status:
            history = [self._wip_commit(head_target)] + history
        try:
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

    def search_commits(self, query: str) -> list[str]:
        """Search the history for commits matching ``query``.

        Matches are case-insensitive against SHA prefix, commit
        message substring, and author name. Emits
        :attr:`search_results_changed` with the matching SHA list
        and returns the same list for synchronous consumers.

        An empty ``query`` clears the filter (emits an empty list).
        """
        if not query:
            self.search_results_changed.emit([])
            return []
        if self._repo is None or not self._repo.is_open:
            self.search_results_changed.emit([])
            return []
        try:
            history = self._repo.get_all_history(max_count=500)
        except GitError:
            self.search_results_changed.emit([])
            return []
        query_lower = query.lower()
        results: list[str] = []
        for commit in history:
            if (query_lower in commit.sha.lower()
                    or query_lower in commit.message.lower()
                    or query_lower in commit.author_name.lower()):
                results.append(commit.sha)
        self.search_results_changed.emit(results)
        return results

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

    @staticmethod
    def _wip_commit(head_target: str | None) -> CommitInfo:
        """Build the synthetic WIP :class:`CommitInfo` shown above ``HEAD``."""
        return CommitInfo(
            sha=WIP_SHA,
            short_sha="WIP",
            message=WIP_MESSAGE,
            author_name="",
            author_email="",
            author_time=int(time.time()),
            committer_name="",
            committer_email="",
            committer_time=int(time.time()),
            parents=[head_target] if head_target else [],
            kind="wip",
        )

    @staticmethod
    def _stash_commit(entry: StashInfo, head_target: str | None) -> CommitInfo:
        """Build a synthetic :class:`CommitInfo` for a stash entry.

        Uses the *real* stash commit OID as the SHA so the right
        panel can resolve it via :meth:`RepositoryManager.get_commit`
        and show the stash commit's message, author, and changed
        files. The ``kind="stash"`` flag tells the graph widget
        to render the golden dashed icon.

        The parent is set to the stash's actual parent commit (the
        HEAD at the time the stash was created) so the graph draws
        the stash as a branch forking off that commit.
        """
        raw = entry.message
        if ": " in raw:
            raw = raw.split(": ", 1)[1]
        label = f"Stash @{{{entry.index}}}: {raw}"

        parent = entry.parent_sha or head_target
        return CommitInfo(
            sha=entry.sha,
            short_sha=entry.sha[:7],
            message=label,
            author_name="",
            author_email="",
            author_time=entry.author_time if entry.author_time else int(time.time()),
            committer_name="",
            committer_email="",
            committer_time=entry.author_time if entry.author_time else int(time.time()),
            parents=[parent] if parent else [],
            kind="stash",
        )
