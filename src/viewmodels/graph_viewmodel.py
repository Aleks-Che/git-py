"""ViewModel for the commit graph.

Bridges the pure-Python :mod:`src.core.graph_v2` cell-based layout engine
and the Qt-flavoured UI: holds a reference to a :class:`RepositoryManager`,
recomputes the layout whenever the repository changes, and exposes the
result plus user-driven ``commit_selected`` events as Qt signals.

The WIP (uncommitted changes) node is now handled by :func:`build_graph`
itself — the ViewModel only supplies the uncommitted file count and the
HEAD SHA.  Stash entries are still synthesised as :class:`CommitInfo`
objects and inserted into the history before calling the engine.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, Signal

from src.core.exceptions import GitError
from src.core.graph_v2 import (
    BRANCH_PALETTE,
    _build_branch_refs_map,
    _build_refs_map,
    build_graph,
    graph_to_dicts,
)
from src.core.models import CommitInfo, StashInfo
from src.core.repository import RepositoryManager
from src.utils.debug_mode import dump_graph, is_debug_mode

WIP_SHA = "WIP"
WIP_MESSAGE = "WIP: Uncommitted changes"


class GraphViewModel(QObject):
    """Drives the commit graph view.

    Signals
    -------
    graph_updated(list[dict])
        Emitted with the new layout after :meth:`refresh_graph` (or
        :meth:`set_repository`) finishes.  Each payload item is a
        plain dict with keys ``commit``, ``lane``, ``color_index``,
        ``cells``, etc. — safe to consume on any thread.
    commit_selected(str)
        Emitted when the user picks a commit in the view.
    error_occurred(str)
        Emitted when a Core call raises a :class:`GitError`.
    search_results_changed(list[str])
        Emitted with matching SHA values after a search.
    """

    graph_updated = Signal(list)
    commit_selected = Signal(str)
    error_occurred = Signal(str)
    search_results_changed = Signal(list)
    scroll_to_commit_requested = Signal(str)
    """Emitted by :meth:`scroll_to_commit`; the view scrolls the commit
    (vertically and horizontally) so the user lands on it. Decoupled from
    :attr:`commit_selected` so the view can suppress the visual highlight
    ring (e.g. when the user only navigates from the left panel)."""
    recently_created_changed = Signal(object)
    """Proxy for :attr:`MainViewModel.recently_created_changed`. The
    graph widget listens here so it can demote just-created branches
    (the user-requested "source-branch-first" behaviour for collapse/
    expand). The view model forwards the signal directly; it does not
    own the data — the snapshot lives on the MainViewModel and is
    re-emitted via :meth:`update_recently_created` whenever the
    MainViewModel's bookkeeping changes."""

    def __init__(self, repo_manager: RepositoryManager | None = None, parent=None) -> None:
        super().__init__(parent)
        self._repo: RepositoryManager | None = repo_manager

    # ------------------------------------------------------------------
    # repository binding
    # ------------------------------------------------------------------

    def set_repository(
        self,
        manager: RepositoryManager | None,
        *,
        refresh: bool = True,
    ) -> None:
        """Bind (or unbind) the repository.

        Passing ``None`` clears the graph (emits an empty list).
        """
        self._repo = manager
        if refresh:
            self.refresh_graph()

    def repository(self) -> RepositoryManager | None:
        return self._repo

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    def refresh_graph(self) -> None:
        """Recompute the layout and emit :attr:`graph_updated`."""
        if self._repo is None or not self._repo.is_open:
            self.graph_updated.emit([])
            return
        rows, err = self._compute_graph(self._repo)
        if err is not None:
            self.error_occurred.emit(err)
            return
        self.graph_updated.emit(rows)

    @staticmethod
    def _compute_graph(
        repo: RepositoryManager,
    ) -> tuple[list[dict], str | None]:
        """Pure data-in/data-out — safe for background threads.

        Reads history / branches / stashes from *repo*, inserts stash
        nodes, and passes everything to :func:`build_graph`.
        """
        try:
            history = repo.get_all_history()
            branches = repo.branches
            tags = repo.tags
            head_target, head_shorthand = GraphViewModel._head_info_from(repo)
            status = repo.get_status()
        except GitError as exc:
            return [], str(exc)

        stash_entries = repo.stash_list
        # Find HEAD index so stashes based on HEAD can be placed above it.
        head_idx: int = 0
        if head_target is not None:
            for i, c in enumerate(history):
                if c.sha == head_target:
                    head_idx = i
                    break
        for entry in stash_entries:
            stash_ci = GraphViewModel._stash_commit(entry, head_target)
            t = stash_ci.author_time
            idx = 0
            while idx < len(history) and history[idx].author_time > t:
                idx += 1
            while idx < len(history) and history[idx].author_time == t:
                idx += 1
            # Stashes whose first parent is HEAD must appear above HEAD
            # so the rebalance step in build_graph can move them to
            # offset lanes, freeing lane 0 for the WIP node.
            if stash_ci.parents and stash_ci.parents[0] == head_target:
                idx = min(idx, head_idx)
            history.insert(idx, stash_ci)
            if idx <= head_idx:
                head_idx += 1

        uncommitted_count: int | None = len(status) if status else None

        try:
            layout = build_graph(history, branches, uncommitted_count=uncommitted_count,
                                 head_commit_sha=head_target)
        except GitError as exc:
            return [], str(exc)

        if is_debug_mode():
            stash_sha_set = {e.sha for e in stash_entries}
            dump_graph(layout, stash_sha_set)

        rows = graph_to_dicts(layout)

        # Enrich rows with refs and branch_refs for the widget.
        refs_by_sha = _build_refs_map(branches, tags, head_target, head_shorthand)
        branch_refs_by_sha = _build_branch_refs_map(branches)
        for idx, row in enumerate(rows):
            commit = row.get("commit")
            sha = commit["sha"] if commit else ""
            row["refs"] = refs_by_sha.get(sha, [])
            row["branch_refs"] = [b.to_dict() for b in branch_refs_by_sha.get(sha, [])]

            # Backward-compatible flat keys.
            if row.get("is_uncommitted"):
                row["sha"] = "WIP"
            else:
                row["sha"] = sha
            row["row"] = idx
            ci = row.get("color_index", 0)
            if 0 <= ci < len(BRANCH_PALETTE):
                row["color"] = BRANCH_PALETTE[ci]
            else:
                row["color"] = BRANCH_PALETTE[0]  # fallback for UNCOMMITTED_COLOR_INDEX etc.
            if commit:
                row["short_sha"] = commit.get("short_sha", "")
                row["subject"] = commit.get("subject", "")
                row["author_name"] = commit.get("author_name", "")
                row["author_time"] = commit.get("author_time", 0)
                row["parents"] = commit.get("parents", [])
                row["kind"] = commit.get("kind", "commit")
            elif row.get("is_uncommitted"):
                row["short_sha"] = "WIP"
                row["subject"] = "WIP: Uncommitted changes"
                row["author_name"] = ""
                row["author_time"] = 0
                row["parents"] = [head_target] if head_target else []
                row["kind"] = "wip"
            else:
                row["short_sha"] = ""
                row["subject"] = ""
                row["author_name"] = ""
                row["author_time"] = 0
                row["parents"] = []
                row["kind"] = "commit"
        return rows, None

    def select_commit(self, sha: str) -> None:
        """Forward a user click on a commit."""
        self.commit_selected.emit(sha)

    def scroll_to_commit(self, sha: str) -> None:
        """Ask the view to bring *sha* into view.

        The view (the :class:`GraphTableWidget`) is the only thing that
        knows the current scroll offsets and the per-column overflow
        ranges; the ViewModel just forwards the request. The signal is
        decoupled from :attr:`commit_selected` so callers (the left
        panel, the search bar) can drive the scroll without forcing a
        visual selection on the graph — the caller decides whether to
        pair the scroll with a selection.

        No-op when *sha* is falsy or when no graph is currently loaded
        (e.g. a :attr:`graph_updated` with an empty list is in flight).
        """
        if not sha:
            return
        self.scroll_to_commit_requested.emit(sha)

    def get_commit_details(self, sha: str) -> CommitInfo | None:
        """Resolve *sha* to a :class:`CommitInfo` for the detail panel."""
        if self._repo is None or not self._repo.is_open:
            return None
        try:
            return self._repo.get_commit(sha)
        except GitError:
            return None

    def update_recently_created(self, names: set[str]) -> None:
        """Forward the MainViewModel's session-creation set to listeners.

        Called by :meth:`MainViewModel.create_branch` (and the
        equivalent ``_create_branch_internal``) after the underlying
        Git command succeeds. The graph widget uses this to demote
        the just-created branch in the chip-priority ordering, so the
        "source" branch (the one the user was on when they issued
        the create) keeps the prominent chip.
        """
        self.recently_created_changed.emit(set(names))

    def search_commits(self, query: str) -> list[str]:
        """Case-insensitive search by SHA / message / author."""
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

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _head_info(self) -> tuple[str | None, str | None]:
        if self._repo is None or self._repo.repo.head_is_unborn:
            return None, None
        head = self._repo.repo.head
        return str(head.target), head.shorthand

    @staticmethod
    def _head_info_from(repo: RepositoryManager) -> tuple[str | None, str | None]:
        if repo.repo.head_is_unborn:
            return None, None
        head = repo.repo.head
        return str(head.target), head.shorthand

    @staticmethod
    def _stash_commit(entry: StashInfo, head_target: str | None) -> CommitInfo:
        """Build a synthetic :class:`CommitInfo` for a stash entry."""
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
