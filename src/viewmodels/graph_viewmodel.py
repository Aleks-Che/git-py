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
from collections.abc import Callable

import pygit2
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
from src.utils.config import default_config_path, get_int, load_config
from src.utils.debug_mode import dump_graph, is_debug_mode

WIP_SHA = "WIP"
WIP_MESSAGE = "WIP: Uncommitted changes"

# R3.1 (P2): default upper bound for the graph's visible history.  The
# constant mirrors the ``graph_history_limit`` config key (see
# :mod:`src.utils.config`) and is used as the fallback when the config
# has not been loaded yet — e.g. inside the background thread that
# opens a worker-owned ``RepositoryManager`` (see
# :meth:`MainViewModel.load_repository_data`).
DEFAULT_GRAPH_HISTORY_LIMIT: int = 500
"""Fallback cap for the graph's visible history.

Matches :data:`src.utils.config._DEFAULT_CONFIG['graph_history_limit']`.
We keep the value duplicated here (instead of importing the private
``_DEFAULT_CONFIG``) so the constant is part of this module's public
contract for callers that do not have a config file on disk.
"""

# R3.1 (P2): the search bar walks the **full** DAG (no truncation)
# so the user can find any commit, even if it is older than the
# visible graph cap.  ``get_all_history`` is bounded by ``max_count``;
# pass a value large enough to cover any real-world repo.  The
# revwalk is still O(n) because libgit2 deduplicates ancestors and
# we stop as soon as the limit is hit.
SEARCH_HISTORY_MAX_COUNT: int = 100_000
"""Cap for the search bar's full-history walk.

Effectively unbounded for the repositories the app targets; the
literal value documents the intent ("walk the full DAG, not the
truncated 500-row slice") and gives a safety net against pathological
repos.
"""


def _load_graph_history_limit() -> int:
    """Read the ``graph_history_limit`` config value, falling back to the default.

    Centralised here so the limit is sourced from the same JSON the
    rest of the app reads (via :func:`src.utils.config.load_config`)
    and the same fallback path is used everywhere.  Tests that want
    to override the limit can either write a temporary config file
    *or* construct the ViewModel and replace
    :attr:`GraphViewModel.history_limit` directly — see
    :attr:`history_limit` for the per-instance override hook.
    """
    try:
        config = load_config(default_config_path())
    except (OSError, ValueError):
        # No on-disk config (headless tests, fresh install) — use
        # the documented default.  ``load_config`` already swallows
        # malformed files, so the only realistic failure here is an
        # OS-level error from ``QStandardPaths`` on a hostile
        # environment; in that case the default is still the
        # correct answer.
        return DEFAULT_GRAPH_HISTORY_LIMIT
    return get_int(config, "graph_history_limit", DEFAULT_GRAPH_HISTORY_LIMIT)


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

    def __init__(
        self,
        repo_manager: RepositoryManager | None = None,
        parent=None,
        *,
        history_limit: int | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo: RepositoryManager | None = repo_manager
        # R3.1 (P2): cap the visible history.  When ``history_limit``
        # is ``None`` we read the value from the user config; an
        # explicit argument (used by tests, and by the background
        # worker that may not have a config file on disk) wins.
        # ``truncated_count`` is the number of commits *not* shown
        # by the latest ``_compute_graph`` call; ``0`` when the
        # repo is small enough to fit in the cap.  The widget reads
        # it to render a "showing N of M" label.
        self._history_limit: int = (
            history_limit if history_limit is not None else _load_graph_history_limit()
        )
        self._truncated_count: int = 0
        # R3.2 (P7): branch-priority cache, computed once per
        # ``refresh_graph`` instead of being recomputed for every chip
        # during paint.  ``name -> (bucket, name)``.  ``bucket`` is
        # 0 (HEAD) / 1 (reachable from HEAD) / 3 (everything else).
        # The widget reads from this map via
        # :meth:`branch_priority_for` instead of calling pygit2 on
        # the UI thread.
        self._branch_priority_cache: dict[str, tuple[int, str]] = {}

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

    @property
    def history_limit(self) -> int:
        """Maximum number of commits the graph will render at once.

        R3.1 (P2): configured via the ``graph_history_limit`` config
        key.  Tests (and the background worker that may run without a
        config file on disk) can override the value at construction
        time via the ``history_limit`` keyword argument.
        """
        return self._history_limit

    @history_limit.setter
    def history_limit(self, value: int) -> None:
        if value <= 0:
            return
        self._history_limit = int(value)

    @property
    def truncated_count(self) -> int:
        """Number of commits hidden by the history-limit cap (R3.1 P2).

        ``0`` when the repository has fewer commits than the cap.  The
        widget reads this to render a "showing N of M" label.
        """
        return self._truncated_count

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    def refresh_graph(self) -> None:
        """Recompute the layout and emit :attr:`graph_updated`."""
        if self._repo is None or not self._repo.is_open:
            self._truncated_count = 0
            self._branch_priority_cache = {}
            self.graph_updated.emit([])
            return
        rows, err = self._compute_graph(
            self._repo, self.error_occurred.emit, self._history_limit
        )
        if err is not None:
            self.error_occurred.emit(err)
            return
        # R3.1 (P2): update the truncation counter so the widget can
        # render a "showing N of M" label.  We compute it here (on
        # the main thread) because the worker path in
        # :meth:`MainViewModel.load_repository_data` calls
        # :meth:`_compute_graph` directly on a background thread
        # without going through this method — see the TODO in
        # ``main_viewmodel.py`` for the async-tracking follow-up.
        self._update_truncated_count()
        # R3.2 (P7): precompute the branch-priority buckets now so
        # the chip renderer can read them in O(1) from a dict during
        # paint instead of walking HEAD's parent chain on the UI
        # thread for every chip.
        self._update_branch_priority_cache()
        self.graph_updated.emit(rows)

    def _update_branch_priority_cache(self) -> None:
        """Recompute :attr:`branch_priority_cache` from the current repo.

        R3.2 (P7): this is the only place that walks HEAD's
        first-parent chain.  The widget reads the result via
        :meth:`branch_priority_for` so paint never touches pygit2.
        """
        if self._repo is None or not self._repo.is_open:
            self._branch_priority_cache = {}
            return
        try:
            branches = self._repo.branches
        except (GitError, pygit2.GitError, OSError):
            self._branch_priority_cache = {}
            return
        try:
            head_target = self._head_target_sha()
        except (GitError, pygit2.GitError, OSError):
            head_target = None
        head_ancestor_tips = self._head_ancestor_tips(branches) if head_target else set()
        cache: dict[str, tuple[int, str]] = {}
        for branch in branches:
            name = branch.name
            if branch.is_head:
                bucket = 0
            elif (
                head_target
                and branch.target_sha
                and branch.target_sha in head_ancestor_tips
                and not branch.is_remote
            ):
                bucket = 1
            else:
                bucket = 3
            cache[name] = (bucket, name)
        self._branch_priority_cache = cache

    def _head_target_sha(self) -> str | None:
        """Return HEAD's current tip SHA, or ``None`` for an unborn HEAD."""
        if self._repo is None or not self._repo.is_open:
            return None
        try:
            if self._repo.repo.head_is_unborn:
                return None
            return str(self._repo.repo.head.target)
        except (KeyError, pygit2.GitError, ValueError, OSError):
            return None

    def _head_ancestor_tips(
        self, branches: list,
    ) -> set[str]:
        """Walk HEAD's first-parent chain and collect branch tips crossed.

        R3.2 (P7): bounded walk (max 256 hops) that mirrors the
        pre-R3.2 heuristic in
        :meth:`src.ui.widgets.graph_panel.GraphPanel._is_branch_reachable_from_head`.
        """
        if self._repo is None or not self._repo.is_open:
            return set()
        branch_tips = {b.target_sha for b in branches if b.target_sha}
        try:
            cur_oid = str(self._repo.repo.head.target)
        except (KeyError, pygit2.GitError, ValueError, OSError):
            return set()
        seen: set[str] = set()
        hops = 0
        max_hops = 256
        while cur_oid and cur_oid not in seen and hops < max_hops:
            seen.add(cur_oid)
            if cur_oid in branch_tips:
                # Crossed a branch tip on the way back from HEAD —
                # any branch pointing here is a candidate "source".
                return branch_tips
            try:
                commit = self._repo.repo.revparse_single(cur_oid)
            except (KeyError, pygit2.GitError, ValueError, OSError):
                return set()
            parents = commit.parents
            cur_oid = str(parents[0].id) if parents else None
            hops += 1
        return set()

    def branch_priority_for(self, branch_name: str) -> tuple[int, str]:
        """Return the priority bucket for *branch_name* (R3.2 P7).

        The widget calls this during paint instead of walking
        ``HEAD``'s parent chain.  Returns ``(3, name)`` for unknown
        branches so the chip renderer falls back to alphabetical
        order rather than crashing.
        """
        if not branch_name:
            return (3, "")
        cached = self._branch_priority_cache.get(branch_name)
        if cached is not None:
            return cached
        return (3, branch_name)

    def _update_truncated_count(self) -> None:
        """Refresh :attr:`truncated_count` from the bound repository.

        R3.1 (P2): best-effort — a failure here (broken repo,
        OS error) must NOT prevent the layout from being emitted,
        so any exception is logged via :attr:`error_occurred` and
        the counter is left at zero.  A zero counter is the right
        fallback value: it suppresses the truncation indicator
        rather than showing a misleading "showing 0 of 0" label.
        """
        if self._repo is None or not self._repo.is_open:
            self._truncated_count = 0
            return
        try:
            total = self._repo.count_all_history()
        except (GitError, pygit2.GitError, OSError) as exc:
            self._truncated_count = 0
            self.error_occurred.emit(f"Failed to count history: {exc}")
            return
        # ``count_all_history`` walks the full DAG; the visible
        # history is the smaller of the limit and the total.
        # ``len(history)`` is not available here without an extra
        # walk, so we approximate: if the cap was hit, total
        # exceeds the cap, so the difference ``total - limit`` is
        # the (approximate) truncated count.  When ``total <=
        # limit`` the truncation is exactly zero and the value
        # below is the safe lower bound.
        if total <= self._history_limit:
            self._truncated_count = 0
        else:
            self._truncated_count = total - self._history_limit

    @staticmethod
    def _compute_graph(
        repo: RepositoryManager,
        error_callback: Callable[[str], None] | None = None,
        history_limit: int = DEFAULT_GRAPH_HISTORY_LIMIT,
    ) -> tuple[list[dict], str | None]:
        """Pure data-in/data-out — safe for background threads.

        Reads history / branches / stashes from *repo*, inserts stash
        nodes, and passes everything to :func:`build_graph`.

        R3.1 (P2): the visible history is capped at *history_limit*
        (the most recent *N* commits).  Search and other consumers
        that need the full DAG use :meth:`search_commits`, which
        walks the un-truncated history.
        """
        try:
            history = repo.get_all_history(max_count=history_limit)
            branches = repo.branches
            tags = repo.tags
            head_target, head_shorthand = GraphViewModel._head_info_from(repo)
            status = repo.get_status()
        except GitError as exc:
            return [], str(exc)

        try:
            stash_entries = repo.stash_list
        except (GitError, pygit2.GitError, OSError) as exc:
            message = f"Stash list failed: {exc}"
            if error_callback is not None:
                error_callback(message)
            # A stale/deleted stash ref must not prevent the rest of the
            # history from being rendered for this refresh.
            stash_entries = []
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
        """Case-insensitive search by SHA / message / author.

        R3.1 (P2): walks the **full** DAG via
        :data:`SEARCH_HISTORY_MAX_COUNT` — the user can find a
        match even when it is older than the visible graph cap.
        The revwalk still stops as soon as the cap is hit, but the
        cap is set high enough to cover any real-world repo, so
        search is effectively un-truncated.
        """
        if not query:
            self.search_results_changed.emit([])
            return []
        if self._repo is None or not self._repo.is_open:
            self.search_results_changed.emit([])
            return []
        try:
            # R3.1 (P1) — was: ``self._repo.get_all_history(max_count=500)``
            # which both truncated the result and used a linear
            # ``for commit in history`` scan with no early break.
            # The un-truncated walk is the documented behaviour
            # (P2 above); the linear scan is already O(n) and
            # fine — the historical bottleneck was the nested
            # ``any(...)`` lookup in the layout engine, not the
            # search.
            history = self._repo.get_all_history(max_count=SEARCH_HISTORY_MAX_COUNT)
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
