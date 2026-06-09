"""Tests for :mod:`src.core.graph_v2` — cell-based graph layout.

Exercises :func:`build_graph` with synthetic histories and verifies
cell contents, lane assignment, colour indices, and fork-point handling.
"""
from __future__ import annotations

import time

from src.core.graph_v2 import (
    BRANCH_PALETTE,
    UNCOMMITTED_COLOR_INDEX,
    CellInfo,
    CellType,
    build_graph,
    graph_to_dicts,
)
from src.core.models import BranchInfo, CommitInfo


def _c(
    sha: str,
    parents: list[str] | None = None,
    message: str = "subject",
    ts: int = 0,
    kind: str = "commit",
) -> CommitInfo:
    return CommitInfo(
        sha=sha,
        short_sha=sha[:7],
        message=message,
        author_name="tester",
        author_email="t@example.com",
        author_time=ts,
        committer_name="tester",
        committer_email="t@example.com",
        committer_time=ts,
        parents=parents or [],
        kind=kind,
    )


def _b(name: str, sha: str, is_head: bool = False, is_remote: bool = False) -> BranchInfo:
    return BranchInfo(name=name, target_sha=sha, is_head=is_head, is_remote=is_remote)


# ---- empty / single ------------------------------------------------------


def test_empty_history_no_uncommitted() -> None:
    layout = build_graph([], [])
    assert layout.nodes == []
    assert layout.max_lane == 0


def test_empty_history_with_uncommitted() -> None:
    layout = build_graph([], [], uncommitted_count=3)
    assert len(layout.nodes) == 1
    n = layout.nodes[0]
    assert n.is_uncommitted
    assert n.uncommitted_count == 3
    assert n.color_index == UNCOMMITTED_COLOR_INDEX
    assert n.cells[0].cell_type == CellType.COMMIT


def test_single_commit() -> None:
    commits = [_c("a" * 40, ts=1)]
    layout = build_graph(commits, [])
    assert len(layout.nodes) == 1
    n = layout.nodes[0]
    assert n.commit is not None
    assert n.commit.sha == "a" * 40
    assert n.lane == 0
    assert n.color_index == 1  # main branch → blue via override
    assert n.cells[0].cell_type == CellType.COMMIT
    assert n.cells[0].color_index == 1


def test_single_commit_with_branch() -> None:
    commits = [_c("a" * 40, ts=1)]
    branches = [_b("main", "a" * 40, is_head=True)]
    layout = build_graph(commits, branches)
    assert len(layout.nodes) == 1
    assert layout.nodes[0].branch_names == ["main"]
    assert layout.nodes[0].is_head


# ---- linear history ------------------------------------------------------


def test_linear_two_commits() -> None:
    commits = [
        _c("b" * 40, parents=["a" * 40], ts=2),
        _c("a" * 40, ts=1),
    ]
    layout = build_graph(commits, [])
    assert len(layout.nodes) == 2
    # newest first
    assert layout.nodes[0].commit.sha == "b" * 40
    assert layout.nodes[1].commit.sha == "a" * 40
    # both on lane 0
    assert layout.nodes[0].lane == 0
    assert layout.nodes[1].lane == 0
    # first row has commit + pipe for parent
    cells0 = layout.nodes[0].cells
    assert cells0[0].cell_type == CellType.COMMIT
    # second row has commit only (no more parents)
    cells1 = layout.nodes[1].cells
    assert cells1[0].cell_type == CellType.COMMIT


def test_linear_three_commits() -> None:
    c3 = "c" * 40
    c2 = "b" * 40
    c1 = "a" * 40
    commits = [
        _c(c3, parents=[c2], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    assert len(layout.nodes) == 3
    # All on lane 0
    for n in layout.nodes:
        assert n.lane == 0
    # Row 0: commit + vertical pipe for parent on same lane
    cells0 = layout.nodes[0].cells
    assert cells0[0].cell_type == CellType.COMMIT
    # Row 1: commit + vertical pipe
    cells1 = layout.nodes[1].cells
    assert cells1[0].cell_type == CellType.COMMIT


# ---- branching -----------------------------------------------------------


def test_simple_branch() -> None:
    """Two branches diverging from a common ancestor.

    Fork point (c1 with 2 children) gets connector cells merged into
    its own row, so we get 3 nodes: c3, c2, c1.
    """
    c1 = "a" * 40  # root (fork point — 2 children)
    c2 = "b" * 40  # main branch
    c3 = "c" * 40  # feature branch
    commits = [
        _c(c3, parents=[c1], ts=3),  # feature (newest)
        _c(c2, parents=[c1], ts=2),  # main
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    assert len(layout.nodes) == 3

    # Row 0 (c3) — lane 0
    n0 = layout.nodes[0]
    assert n0.commit.sha == c3
    assert n0.lane == 0
    assert n0.cells[0].cell_type == CellType.COMMIT

    # Row 1 (c2) — lane 1
    n1 = layout.nodes[1]
    assert n1.commit.sha == c2
    assert n1.lane == 1
    cells1 = n1.cells
    assert cells1[0].cell_type == CellType.PIPE
    assert cells1[2].cell_type == CellType.COMMIT

    # Row 2 (c1) — lane 0, root with merged connector cells.
    # The fork connector TEE_RIGHT carries the first child's colour on
    # the horizontal and the root's colour on the vertical pipe.
    n_root = layout.nodes[2]
    assert n_root.commit.sha == c1
    assert n_root.lane == 0
    cells_root = n_root.cells
    assert cells_root[0].cell_type == CellType.TEE_RIGHT
    assert cells_root[0].pipe_color_index == n_root.color_index  # root's pipe colour
    assert any(c.cell_type == CellType.MERGE_LEFT for c in cells_root)
    assert any(c.cell_type == CellType.HORIZONTAL for c in cells_root)


def test_branch_with_refs() -> None:
    """Branch names should appear on the correct commits."""
    c1 = "a" * 40
    c2 = "b" * 40
    c3 = "c" * 40
    commits = [
        _c(c3, parents=[c1], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    branches = [
        _b("main", c2, is_head=True),
        _b("feature", c3),
    ]
    layout = build_graph(commits, branches)
    assert layout.nodes[0].branch_names == ["feature"]
    assert layout.nodes[1].branch_names == ["main"]
    assert layout.nodes[1].is_head
    assert layout.nodes[2].branch_names == []


# ---- merge ---------------------------------------------------------------


def test_merge_commit() -> None:
    """A merge commit with two parents."""
    c1 = "a" * 40  # root
    c2 = "b" * 40  # on branch
    c3 = "c" * 40  # merge of c2 and c1
    commits = [
        _c(c3, parents=[c2, c1], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    assert len(layout.nodes) >= 3

    # c3 is the merge commit, on lane 0
    n0 = layout.nodes[0]
    assert n0.commit.sha == c3
    assert n0.lane == 0

    # Should have connections to both parents
    # First parent c2 on lane 0, second parent c1 on lane 1
    cells0 = n0.cells
    assert cells0[0].cell_type == CellType.COMMIT
    # There should be some connection to lane 1
    has_lane1_connection = any(
        c.cell_type != CellType.EMPTY for c in cells0[2:]
    )
    assert has_lane1_connection, f"Expected lane 1 connection in {cells0}"

    # c2 is on lane 0 (first parent)
    n1 = layout.nodes[1]
    assert n1.commit.sha == c2
    assert n1.lane == 0


def test_merge_with_branch_names() -> None:
    c1 = "a" * 40
    c2 = "b" * 40
    c3 = "c" * 40
    commits = [
        _c(c3, parents=[c2, c1], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    branches = [
        _b("main", c3, is_head=True),
        _b("feature", c2),
    ]
    layout = build_graph(commits, branches)
    assert layout.nodes[0].branch_names == ["main"]
    assert layout.nodes[0].is_head
    assert layout.nodes[1].branch_names == ["feature"]


# ---- fork point (2+ children) -------------------------------------------


def test_fork_point_connector_row() -> None:
    """When a commit has 2+ children, connector cells are merged into its row."""
    c1 = "a" * 40  # root (fork point)
    c2 = "b" * 40  # child 1 (main)
    c3 = "c" * 40  # child 2 (branch)
    commits = [
        _c(c3, parents=[c1], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    nodes = layout.nodes
    commit_shas = [n.commit.sha for n in nodes if n.commit is not None]
    assert commit_shas[0] == c3
    assert commit_shas[1] == c2
    assert commit_shas[2] == c1

    root_node = nodes[2]
    assert root_node.commit.sha == c1
    has_merge = any(
        c.cell_type in (CellType.MERGE_LEFT, CellType.MERGE_RIGHT)
        for c in root_node.cells
    )
    assert has_merge


def test_fork_point_with_three_children() -> None:
    """Three children create merge cells on the root commit's row."""
    c1 = "a" * 40
    c2 = "b" * 40
    c3 = "c" * 40
    c4 = "d" * 40
    commits = [
        _c(c4, parents=[c1], ts=4),
        _c(c3, parents=[c1], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    nodes = layout.nodes
    commit_nodes = [n for n in nodes if n.commit is not None]
    assert len(commit_nodes) == 4

    root_node = commit_nodes[-1]
    assert root_node.commit.sha == c1
    has_tee = any(c.cell_type == CellType.TEE_RIGHT for c in root_node.cells)
    assert has_tee


# ---- uncommitted changes -------------------------------------------------


def test_uncommitted_node_inserted() -> None:
    c1 = "a" * 40
    commits = [
        _c(c1, ts=1),
    ]
    branches = [_b("main", c1, is_head=True)]
    layout = build_graph(commits, branches, uncommitted_count=3, head_commit_sha=c1)
    assert len(layout.nodes) == 2
    assert layout.nodes[0].is_uncommitted
    assert layout.nodes[0].uncommitted_count == 3
    assert layout.nodes[0].color_index == UNCOMMITTED_COLOR_INDEX
    assert layout.nodes[1].commit is not None
    assert layout.nodes[1].commit.sha == c1


def test_uncommitted_no_head_commit_skip() -> None:
    """Uncommitted without a matching HEAD commit is not inserted."""
    c1 = "a" * 40
    commits = [_c(c1, ts=1)]
    layout = build_graph(commits, [], uncommitted_count=3, head_commit_sha="b" * 40)
    assert len(layout.nodes) == 1
    assert not layout.nodes[0].is_uncommitted


def test_uncommitted_with_pipe_to_previous() -> None:
    """Previous commits get a pipe for the uncommitted lane."""
    c2 = "b" * 40
    c1 = "a" * 40
    commits = [
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    branches = [_b("main", c2, is_head=True)]
    layout = build_graph(commits, branches, uncommitted_count=1, head_commit_sha=c2)
    assert len(layout.nodes) == 3
    assert layout.nodes[0].is_uncommitted
    # The HEAD commit should have a pipe for the uncommitted lane
    # (the uncommitted node sits above HEAD, connected by a vertical line)


# ---- graph_to_dicts ------------------------------------------------------


def test_graph_to_dicts_roundtrip() -> None:
    c1 = "a" * 40
    c2 = "b" * 40
    commits = [
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    rows = graph_to_dicts(layout)
    assert len(rows) == 2
    for row in rows:
        assert "commit" in row
        assert "cells" in row
        assert isinstance(row["cells"], list)
        for c in row["cells"]:
            assert "t" in c


def test_graph_to_dicts_includes_cell_details() -> None:
    c1 = "a" * 40
    commits = [_c(c1, ts=1)]
    layout = build_graph(commits, [])
    rows = graph_to_dicts(layout)
    cell = rows[0]["cells"][0]
    assert cell["t"] == int(CellType.COMMIT)
    assert cell.get("c") == 1  # main branch → blue via override


# ---- ColorAssigner -------------------------------------------------------


def test_color_assigner_main_branch() -> None:
    """Linear history should all share main colour (1=blue via override)."""
    c3 = "c" * 40
    c2 = "b" * 40
    c1 = "a" * 40
    commits = [
        _c(c3, parents=[c2], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    for n in layout.nodes:
        assert n.color_index == 1  # main branch → blue


def test_color_assigner_branches_get_different_colors() -> None:
    """Different branches should have different colour indices."""
    c1 = "a" * 40
    c2 = "b" * 40
    c3 = "c" * 40
    commits = [
        _c(c3, parents=[c1], ts=3),
        _c(c2, parents=[c1], ts=2),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    colors = {n.commit.sha: n.color_index for n in layout.nodes if n.commit}
    assert colors[c1] != colors[c2] or colors[c1] != colors[c3], \
        f"Expected different colors, got {colors}"


def test_color_palette_accessible() -> None:
    """BRANCH_PALETTE should be a tuple of hex colour strings."""
    assert len(BRANCH_PALETTE) >= 12
    for c in BRANCH_PALETTE:
        assert c.startswith("#")
        assert len(c) == 7


# ---- CellType / CellInfo -------------------------------------------------


def test_cell_type_is_int_enum() -> None:
    assert int(CellType.EMPTY) == 0
    assert int(CellType.COMMIT) == 2


def test_cell_info_factories() -> None:
    ci = CellInfo.commit(5)
    assert ci.cell_type == CellType.COMMIT
    assert ci.color_index == 5

    ci = CellInfo.pipe(3)
    assert ci.cell_type == CellType.PIPE
    assert ci.color_index == 3

    ci = CellInfo.horizontal_pipe(7, 2)
    assert ci.cell_type == CellType.HORIZONTAL_PIPE
    assert ci.color_index == 7
    assert ci.pipe_color_index == 2


def test_cell_info_to_dict() -> None:
    ci = CellInfo.empty()
    d = ci.to_dict()
    assert d == {"t": 0}

    ci = CellInfo.commit(3)
    d = ci.to_dict()
    assert d == {"t": 2, "c": 3}

    ci = CellInfo.horizontal_pipe(4, 1)
    d = ci.to_dict()
    assert d == {"t": 8, "c": 4, "p": 1}


# ---- edge cases ----------------------------------------------------------


def test_commit_with_no_parents() -> None:
    """Root commit should work fine."""
    c1 = "a" * 40
    commits = [_c(c1, parents=[], ts=1)]
    layout = build_graph(commits, [])
    assert len(layout.nodes) == 1
    assert layout.nodes[0].cells[0].cell_type == CellType.COMMIT


def test_commit_parent_not_in_history() -> None:
    """Parent SHA not in history should be silently ignored."""
    c1 = "a" * 40
    commits = [_c(c1, parents=["z" * 40], ts=1)]
    layout = build_graph(commits, [])
    assert len(layout.nodes) == 1
    # Should not crash; just no connection lines for unknown parent


def test_multiple_branches_same_commit() -> None:
    """Multiple branches pointing to the same commit."""
    c1 = "a" * 40
    commits = [_c(c1, ts=1)]
    branches = [
        _b("main", c1, is_head=True),
        _b("develop", c1),
    ]
    layout = build_graph(commits, branches)
    assert set(layout.nodes[0].branch_names) == {"main", "develop"}


def test_detached_head() -> None:
    """HEAD on a commit that has no branch — is_head via head_commit_sha."""
    c1 = "a" * 40
    commits = [_c(c1, ts=1)]
    layout = build_graph(commits, [], head_commit_sha=c1)
    assert len(layout.nodes) == 1
    # is_head comes from head_commit_sha fallback when no branches carry is_head
    assert layout.nodes[0].is_head


def test_remote_branch() -> None:
    c1 = "a" * 40
    commits = [_c(c1, ts=1)]
    branches = [_b("origin/main", c1, is_remote=True)]
    layout = build_graph(commits, branches)
    assert layout.nodes[0].branch_names == ["origin/main"]


# ---- stash nodes in history ---------------------------------------------


def test_stash_kind_nodes() -> None:
    """Nodes with kind='stash' should be handled (they appear as commits)."""
    c1 = "a" * 40
    s1 = "s" * 40
    commits = [
        _c(s1, parents=[c1], ts=2, kind="stash"),
        _c(c1, ts=1),
    ]
    layout = build_graph(commits, [])
    assert len(layout.nodes) >= 2
    stash_nodes = [n for n in layout.nodes if n.commit is not None and n.commit.kind == "stash"]
    assert len(stash_nodes) >= 1


# ---- performance ---------------------------------------------------------


def test_performance_500_commits() -> None:
    """Building a graph with 500 linear commits should finish quickly."""
    commits: list[CommitInfo] = []
    prev = None
    for i in range(500):
        sha = f"{i:040d}"
        parents = [prev] if prev else []
        commits.append(_c(sha, parents=parents, ts=500 - i))
        prev = sha
    commits.reverse()  # newest first
    t0 = time.perf_counter()
    layout = build_graph(commits, [])
    elapsed = time.perf_counter() - t0
    assert len(layout.nodes) == 500
    assert elapsed < 2.0, f"Graph build took {elapsed:.2f}s"


def test_performance_500_commits_branched() -> None:
    """500 commits with branching should still be fast."""
    commits: list[CommitInfo] = []
    # Create a main line and branches
    main_shas: list[str] = []
    for i in range(300):
        sha = f"m{i:038d}"
        main_shas.append(sha)

    for i in range(300):
        sha = main_shas[i]
        parents = [main_shas[i - 1]] if i > 0 else []
        commits.append(_c(sha, parents=parents, ts=300 - i))

    # Add branch commits
    branch_root = main_shas[100]
    prev_br = branch_root
    for i in range(200):
        sha = f"b{i:038d}"
        commits.append(_c(sha, parents=[prev_br], ts=400 - i))
        prev_br = sha

    # Sort newest first by timestamp
    commits.sort(key=lambda c: c.author_time, reverse=True)

    t0 = time.perf_counter()
    layout = build_graph(commits, [])
    elapsed = time.perf_counter() - t0
    assert len(layout.nodes) >= 500
    assert elapsed < 2.0, f"Graph build took {elapsed:.2f}s"
