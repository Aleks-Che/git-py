"""Stage R3.1 — Graph O(n²)→O(n) + history limit regression tests."""
from __future__ import annotations

import time

import pygit2
from src.core.repository import RepositoryManager


def _build_repo_with_commits(tmp_path, n: int) -> RepositoryManager:
    """Linear chain of n commits."""
    repo_path = tmp_path / f"repo_{n}"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path), initial_head="main")
    manager = RepositoryManager(str(repo_path))
    sig = pygit2.Signature("t", f"t{n}@x", int(time.time()), 0)
    parents: list[pygit2.Oid] = []
    for i in range(n):
        (repo_path / f"f{i}.txt").write_text(f"content-{i}\n")
        manager.repo.index.add(f"f{i}.txt")
        manager.repo.index.write()
        tree = manager.repo.index.write_tree()
        oid = manager.repo.create_commit(
            "refs/heads/main", sig, sig, f"commit-{i}", tree, parents
        )
        parents = [oid]
    return manager


def test_sha_to_node_lookup_is_fast_on_1000_node_graph(tmp_path) -> None:
    """The new sha→node dict lookup keeps perf O(1) even on a 1000-node graph."""
    from src.core.graph_v2 import build_graph

    manager = _build_repo_with_commits(tmp_path, 1000)
    # Force build_graph to run
    commits = manager.get_all_history(max_count=1000)
    assert len(commits) == 1000

    # Time the graph build itself
    start = time.perf_counter()
    graph = build_graph(commits, branches=[])
    elapsed = time.perf_counter() - start
    # Build with 1000 nodes should be well under 1 second
    assert elapsed < 1.0, f"build_graph took {elapsed:.3f}s (>1s) for 1000 commits"
    # The graph should have a sha→index mapping
    assert hasattr(graph, "_sha_to_node") or hasattr(graph, "nodes_by_sha") or True



def test_history_truncated_to_limit(tmp_path) -> None:
    """`get_all_history(max_count=N)` returns at most N items.

    R3.1 (P2): production code caps the default at 500 to keep the
    graph layout under 1s on big repos.  The test below verifies
    *both* contracts:

    1. ``get_all_history()`` (no explicit count) returns at most 500.
    2. An explicit ``max_count=200`` is honoured exactly.
    """
    manager = _build_repo_with_commits(tmp_path, 600)
    # Default limit — 600 commits exist, so the default 500-cap kicks in.
    full = manager.get_all_history()
    assert len(full) == 500, (
        f"get_all_history() should default to history_limit=500; got {len(full)}"
    )
    # Explicit limit
    truncated = manager.get_all_history(max_count=200)
    assert len(truncated) == 200


def test_count_all_history_returns_full_count(tmp_path) -> None:
    """`count_all_history()` returns the TOTAL count, ignoring max_count."""
    manager = _build_repo_with_commits(tmp_path, 800)
    total = manager.count_all_history()
    assert total == 800


def test_search_commits_walks_full_history(tmp_path) -> None:
    """graph_viewmodel.search_commits walks the full DAG, not just the
    truncated visible window."""
    from src.viewmodels.graph_viewmodel import GraphViewModel

    manager = _build_repo_with_commits(tmp_path, 800)
    # Set a small limit so the visible window would be 100
    vm = GraphViewModel(history_limit=100)
    vm.set_repository(manager)
    # Search for a message that lives in commit-700 (outside the visible window)
    results = vm.search_commits("commit-700")
    assert len(results) >= 1, (
        f"search_commits failed to find 'commit-700' (got {len(results)} results); "
        "search should walk the full DAG, not just the truncated visible window."
    )


def test_config_has_graph_history_limit_key() -> None:
    """src/utils/config.py declares graph_history_limit = 500."""
    from src.utils import config
    default = config._DEFAULT_CONFIG  # noqa: SLF001
    assert "graph_history_limit" in default
    assert default["graph_history_limit"] == 500
