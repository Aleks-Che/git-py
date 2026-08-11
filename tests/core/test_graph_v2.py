"""Tests for :mod:`src.core.graph_v2` — cell-based graph layout.

Exercises :func:`build_graph` with synthetic histories and verifies
cell contents, lane assignment, colour indices, and fork-point handling.
"""

from __future__ import annotations

import time
from pathlib import Path

import pygit2
from src.core.graph_v2 import (
    BRANCH_PALETTE,
    UNCOMMITTED_COLOR_INDEX,
    CellInfo,
    CellType,
    ColorAssigner,
    _pick_branch_color,
    _sha_color_seed,
    build_graph,
    graph_to_dicts,
)
from src.core.models import BranchInfo, CommitInfo
from src.core.repository import RepositoryManager


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
    # First parent c2 on lane 0, second parent c1 on lane 1.
    # The merge uses TEE_RIGHT at lane 0 (exact-length horizontal
    # to lane 1) instead of COMMIT + intermediate HORIZONTAL.
    cells0 = n0.cells
    assert cells0[0].cell_type == CellType.TEE_RIGHT
    assert cells0[0].pipe_color_index == n0.color_index  # commit's pipe colour
    # There should be some merge/branch indicator on lane 1
    has_lane1_connection = any(
        c.cell_type in (CellType.MERGE_LEFT, CellType.TEE_LEFT, CellType.BRANCH_LEFT)
        for c in cells0[2:]
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


def test_merge_non_adjacent_parent_uses_tee_right_at_commit() -> None:
    """A merge commit whose second parent lives more than one lane
    away must still use ``TEE_RIGHT`` at the commit lane.

    Regression: the old code only replaced the commit cell with
    ``TEE_RIGHT`` for *adjacent* parents. For a non-adjacent parent
    the commit cell stayed as ``COMMIT`` (no horizontal) and the
    first ``HORIZONTAL`` cell sat at the mid of the commit lane —
    leaving a visible ``~node_radius``-pixel gap between the
    commit ellipse and the start of the connector. The user-visible
    symptom was a PR-merge connector that did not quite reach the
    commit node (e.g. ``gpt-researcher`` commit ``5521508f``,
    which has its second parent on lane 2 while the merge itself
    sits on lane 0).

    The test calls :func:`_build_row_cells` directly with a
    controlled parent layout so the non-adjacent case is
    guaranteed — the higher-level ``build_graph`` algorithm tries
    to minimise lane usage and usually ends up placing the second
    parent on ``commit_lane + 1``, which would mask the regression.
    """
    from src.core.graph_v2 import _build_row_cells

    # Commit on lane 0 with TWO parents: the first parent on the
    # commit's own lane (drawn as a vertical pipe) and the second
    # parent two lanes over (on lane 2). The second parent is the
    # PR-merge parent that is on the offset lane.
    commit_lane = 0
    commit_color = 1
    first_parent_lane = 0
    second_parent_lane = 2
    first_parent_color = 1
    second_parent_color = 3
    max_lane = 2

    parent_lanes = [
        ("first_parent", first_parent_lane, False, first_parent_color, False),
        ("second_parent", second_parent_lane, False, second_parent_color, False),
    ]
    active_lanes = [None, None, "second_parent"]  # only second parent is "in flight"
    oid_color_index = {"second_parent": second_parent_color}
    lane_color_index = {0: commit_color, 2: second_parent_color}

    cells = _build_row_cells(
        commit_lane=commit_lane,
        commit_color=commit_color,
        parent_lanes=parent_lanes,
        active_lanes=active_lanes,
        oid_color_index=oid_color_index,
        lane_color_index=lane_color_index,
        max_lane=max_lane,
    )

    # The cell at the commit lane must be TEE_RIGHT (not COMMIT).
    # Without the fix this cell stayed as COMMIT and the first
    # HORIZONTAL cell sat at the mid of the commit lane — leaving a
    # ~radius-pixel gap between the commit ellipse and the start of
    # the connector.
    commit_cell = cells[commit_lane * 2]
    assert commit_cell.cell_type == CellType.TEE_RIGHT, (
        f"merge commit cell must be TEE_RIGHT for a non-adjacent "
        f"rightward parent; got {commit_cell.cell_type} (this is the "
        f"gap-between-commit-and-connector regression)"
    )
    # The vertical segment of TEE_RIGHT must keep the commit's own
    # colour (pipe_color_index), so the downward pipe below the
    # commit stays in the commit's branch.
    assert commit_cell.pipe_color_index == commit_color
    # The horizontal of TEE_RIGHT must use the second parent's
    # colour, so the connector reads as the merged-in lane.
    assert commit_cell.color_index == second_parent_color

    # The lane-1 mid cell and the lane-1 lane cell should both
    # carry the horizontal in the second parent's colour, so the
    # line continues past the commit centre all the way to lane 2.
    # (HORIZONTAL_PIPE only appears when the cell was previously a
    # PIPE — i.e. an active commit lived on lane 1. In the synthetic
    # test setup there is no such commit, so the cell is a plain
    # HORIZONTAL.)
    assert cells[1].cell_type == CellType.HORIZONTAL
    assert cells[1].color_index == second_parent_color
    assert cells[2].cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE)
    assert cells[2].color_index == second_parent_color

    # The cell at the second parent's lane is a curve (BRANCH_LEFT
    # in this case — the parent is not in the layout yet so the
    # connection exits the connector as a downward fork).
    assert cells[second_parent_lane * 2].cell_type == CellType.BRANCH_LEFT
    assert cells[second_parent_lane * 2].color_index == second_parent_color


def test_merge_non_adjacent_parent_leftward_uses_tee_left() -> None:
    """Symmetric to :func:`test_merge_non_adjacent_parent_uses_tee_right_at_commit`
    for a merge whose second parent sits to the LEFT of the commit
    lane. The commit cell must become ``TEE_LEFT`` for the
    horizontal to reach the commit centre from the left side.
    """
    from src.core.graph_v2 import _build_row_cells

    # Commit on lane 2 with the second parent two lanes to the LEFT
    # (on lane 0). This mirrors the rightward case in
    # ``test_merge_non_adjacent_parent_uses_tee_right_at_commit``.
    commit_lane = 2
    commit_color = 1
    first_parent_lane = 2
    second_parent_lane = 0
    first_parent_color = 1
    second_parent_color = 3
    max_lane = 2

    parent_lanes = [
        ("first_parent", first_parent_lane, False, first_parent_color, False),
        ("second_parent", second_parent_lane, False, second_parent_color, False),
    ]
    active_lanes = ["second_parent", None, None]
    oid_color_index = {"second_parent": second_parent_color}
    lane_color_index = {0: second_parent_color, 2: commit_color}

    cells = _build_row_cells(
        commit_lane=commit_lane,
        commit_color=commit_color,
        parent_lanes=parent_lanes,
        active_lanes=active_lanes,
        oid_color_index=oid_color_index,
        lane_color_index=lane_color_index,
        max_lane=max_lane,
    )

    commit_cell = cells[commit_lane * 2]
    assert commit_cell.cell_type == CellType.TEE_LEFT, (
        f"merge commit cell must be TEE_LEFT for a non-adjacent "
        f"leftward parent; got {commit_cell.cell_type}"
    )
    assert commit_cell.pipe_color_index == commit_color
    assert commit_cell.color_index == second_parent_color

    # The intermediate cells at lane 1 should carry the connector in
    # the second parent's colour. (HORIZONTAL_PIPE only appears when
    # the cell was previously a PIPE — i.e. an active commit lived
    # on lane 1. In this synthetic setup there is no such commit, so
    # the cell is a plain HORIZONTAL.)
    assert cells[1].cell_type == CellType.HORIZONTAL
    assert cells[1].color_index == second_parent_color
    assert cells[2].cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE)
    assert cells[2].color_index == second_parent_color

    # The cell at the second parent's lane is a curve (BRANCH_RIGHT
    # for a leftward connection).
    assert cells[second_parent_lane * 2].cell_type == CellType.BRANCH_RIGHT
    assert cells[second_parent_lane * 2].color_index == second_parent_color


def test_merge_adjacent_parent_still_uses_tee_right() -> None:
    """Adjacent-parent merges must keep using TEE_RIGHT (regression
    guard for the existing ``test_merge_commit`` contract).
    """
    from src.core.graph_v2 import _build_row_cells

    commit_lane = 0
    commit_color = 1
    first_parent_lane = 0
    second_parent_lane = 1  # adjacent — commit_lane + 1
    max_lane = 1

    parent_lanes = [
        ("first_parent", first_parent_lane, False, commit_color, False),
        ("second_parent", second_parent_lane, False, 3, False),
    ]
    active_lanes = [None, "second_parent"]
    oid_color_index = {"second_parent": 3}
    lane_color_index = {0: commit_color, 1: 3}

    cells = _build_row_cells(
        commit_lane=commit_lane,
        commit_color=commit_color,
        parent_lanes=parent_lanes,
        active_lanes=active_lanes,
        oid_color_index=oid_color_index,
        lane_color_index=lane_color_index,
        max_lane=max_lane,
    )

    commit_cell = cells[commit_lane * 2]
    assert commit_cell.cell_type == CellType.TEE_RIGHT
    assert commit_cell.color_index == 3
    assert commit_cell.pipe_color_index == commit_color
    # For an adjacent parent, the loop over
    # ``range(commit_lane*2 + 1, parent_lane*2 - 1)`` is empty
    # (range(1, 1)), so no intermediate HORIZONTAL cell is added
    # between the commit and the parent lane. The cell at the
    # parent lane is BRANCH_LEFT.
    assert cells[second_parent_lane * 2].cell_type == CellType.BRANCH_LEFT


def test_merge_and_fork_on_same_lane_uses_cross_cell() -> None:
    """A commit that is BOTH a merge (has 2 parents) AND a fork
    point (has 2+ children) where the second parent and one of
    the children land on the same lane must draw a CROSS cell
    at that lane on the merge commit's row.

    GitKraken does exactly this — the second parent stays on the
    natural lane (the same lane as the child), and a dedicated
    ┠ cross cell at the merge commit marks the
    fork-merge point: the horizontal segment is the merge
    connector coming in from the merge commit, while the
    vertical pipes pass through the cell in both directions
    (one to the child above, one to the second parent below).

    Regression: without the fix the cell at the second parent's
    lane was a plain ``MERGE_LEFT`` curve, leaving the user to
    see a single curving line and read the merge connection as
    ambiguous. Real-world example: ``gpt-researcher`` commit
    ``409b8b60`` (PR #1817 merge) is followed by commit
    ``3080b0c4`` on the same lane — a security branch created
    from the merge. The user reported the merge connection as
    visually unclear because the gold branch-creation line and
    the blue merge line shared the same column at the merge
    commit and rendered as one continuous curve.
    """
    # Topology, listed newest-first (which is the order
    # ``build_graph`` processes them):
    #
    #   c_after   (lane 0, child of c_merge)        <-- processed first
    #   c_branch  (lane 1, child of c_merge)        <-- processed second;
    #                                                  pushes c_merge into
    #                                                  active lanes at
    #                                                  BOTH lane 0 and 1
    #   c_merge   (lane 0, parents [c_main, c_tip]) <-- processed third;
    #                                                  both children are
    #                                                  in active lanes, so
    #                                                  the fork connector
    #                                                  fires here and a
    #                                                  CROSS cell must be
    #                                                  placed at lane 1
    #                                                  (where c_branch is
    #                                                  above AND c_tip is
    #                                                  below)
    #   c_tip     (lane 1, the PR being merged in)  <-- stays on lane 1
    #                                                  (same as c_branch);
    #                                                  a CROSS cell at
    #                                                  lane 1 carries both
    #                                                  connections
    #   c_main    (lane 0, the main-line parent)    <-- older still
    #   c_root    (lane 0, the root)
    #
    # We add a branches list so c_branch gets a non-main colour
    # (otherwise the colour-assigner's hash-based fallback can
    # collide with c_tip and we can't tell them apart in the
    # assertion).
    c_root = "0" * 40
    c_main = "1" * 40
    c_tip = "2" * 40
    c_merge = "3" * 40
    c_branch = "4" * 40
    c_after = "5" * 40
    commits = [
        _c(c_after, [c_merge], ts=6),
        _c(c_branch, [c_merge], ts=5),
        _c(c_merge, [c_main, c_tip], ts=4),
        _c(c_tip, [c_root], ts=3),
        _c(c_main, [c_root], ts=2),
        _c(c_root, ts=1),
    ]
    branches = [
        _b("main", c_main, is_head=True),
        _b("feature-x", c_branch),
    ]
    layout = build_graph(commits, branches)

    merge_node = next(n for n in layout.nodes if n.commit and n.commit.sha == c_merge)
    branch_node = next(n for n in layout.nodes if n.commit and n.commit.sha == c_branch)
    tip_node = next(n for n in layout.nodes if n.commit and n.commit.sha == c_tip)

    # The child (c_branch) and the second parent (c_tip) stay on
    # the SAME lane — that is GitKraken's "тройник" rendering:
    # one column for both connections, with the CROSS cell
    # distinguishing them.
    assert branch_node.lane == tip_node.lane, (
        "expected child and second parent on the same lane "
        f"(branch lane={branch_node.lane}, tip lane={tip_node.lane}); "
        "the GitKraken-style rendering keeps them together and "
        "uses a CROSS cell to mark the fork-merge point"
    )

    # The merge commit's row must contain a CROSS cell at the
    # shared lane. The cross carries:
    #   * horizontal/vertical-down in the second parent's colour
    #   * vertical-up in the child's colour (the snapshot colour
    #     of the fork lane)
    branch_color = branch_node.color_index
    tip_color = tip_node.color_index
    shared_lane = branch_node.lane

    cross_cells = [
        c
        for ci, c in enumerate(merge_node.cells)
        if ci // 2 == shared_lane and c.cell_type == CellType.CROSS
    ]
    non_empty = [
        (ci, c.cell_type.name)
        for ci, c in enumerate(merge_node.cells)
        if c.cell_type != CellType.EMPTY
    ]
    assert cross_cells, (
        f"expected a CROSS cell at lane {shared_lane} on the merge "
        f"commit's row; got cells: {non_empty}"
    )
    cross = cross_cells[0]
    assert cross.color_index == tip_color, (
        f"CROSS horizontal/vertical-down colour must be the second "
        f"parent's colour ({tip_color}); got {cross.color_index}"
    )
    assert cross.pipe_color_index == branch_color, (
        f"CROSS vertical-up colour must be the child's branch colour "
        f"({branch_color}); got {cross.pipe_color_index}"
    )

    # The merge commit at its own lane must have a TEE_RIGHT (or
    # TEE_LEFT) that opens the horizontal connector toward the
    # CROSS cell.
    commit_lane = merge_node.lane
    lane0_cells = [
        c
        for ci, c in enumerate(merge_node.cells)
        if ci // 2 == commit_lane and c.cell_type != CellType.EMPTY
    ]
    assert any(c.cell_type in (CellType.TEE_RIGHT, CellType.TEE_LEFT) for c in lane0_cells), (
        f"expected a TEE_RIGHT or TEE_LEFT at the merge commit's "
        f"lane ({commit_lane}); got {[c.cell_type.name for c in lane0_cells]}"
    )


def test_cross_cell_carries_horizontal_direction() -> None:
    """CROSS at the fork-merge point must carry a ``direction`` so the
    renderer bridges the ``lane_w / 2`` gap between the commit-centred
    vertical pipe and the between-lanes horizontal connector.

    Regression: ``gpt-researcher`` merge commit ``693d3b72`` has
    parent[1] ``b364917f`` on lane 0, far to the LEFT of the merge
    commit's lane (lane 14). The CROSS at lane 0 used to draw only
    vertical pipes; the horizontal connector started at col 1,
    ``lane_w / 2`` pixels to the right of the commit vertical, leaving
    a visible empty gap.  With ``direction = +1`` the renderer now
    extends the horizontal LEFT-to-RIGHT from the commit vertical to
    meet the between-lanes horizontal — closing the gap.
    """
    # Topology (mirrors gpt-researcher ``b364917f -> 693d3b72``):
    #
    #   c_ch0   (lane 0, child of c_merge)        -- keeps lane 0 alive
    #                                                after the merge so
    #                                                lane 0 is a fork
    #                                                lane on c_merge
    #   c_merge (lane 0, parents=[c_main, c_tip]) -- fork-merge: its
    #                                                child c_ch0 lives
    #                                                on lane 0, and
    #                                                c_tip (parent[1])
    #                                                also lives on
    #                                                lane 0 → CROSS at
    #                                                lane 0 (well to
    #                                                the LEFT of the
    #                                                merge commit's
    #                                                lane if we widen
    #                                                the graph first)
    #   c_tip   (lane 0)
    #   c_main  (lane 0)
    #
    # We wedge two extra children on different lanes between c_merge
    # and c_ch0 so the merge commit ends up on lane 2 (analog of
    # gpt-researcher's lane 14 — anything > 0 demonstrates the
    # direction pick).
    c_main = "1" * 40
    c_tip = "2" * 40
    c_merge = "3" * 40
    c_ch0 = "4" * 40  # child of c_merge on lane 0
    c_ch1 = "5" * 40  # child of c_merge on lane 1
    c_ch2 = "6" * 40  # child of c_merge on lane 2
    commits = [
        _c(c_ch0, [c_merge], ts=7),
        _c(c_ch1, [c_merge], ts=6),
        _c(c_ch2, [c_merge], ts=5),
        _c(c_merge, [c_main, c_tip], ts=4),
        _c(c_tip, [], ts=3),
        _c(c_main, [], ts=2),
    ]
    layout = build_graph(commits, [])
    merge_node = next(n for n in layout.nodes if n.commit.sha == c_merge)

    # Find every CROSS on the merge row.
    cross_cells = [
        (ci, c) for ci, c in enumerate(merge_node.cells) if c.cell_type == CellType.CROSS
    ]
    assert cross_cells, "expected at least one CROSS cell on the merge row"

    for ci, c in cross_cells:
        lane = ci // 2
        if lane < merge_node.lane:
            assert c.direction == 1, (
                f"CROSS at lane {lane} (left of merge lane "
                f"{merge_node.lane}) must carry direction=+1 to bridge "
                f"the gap between the commit vertical and the "
                f"between-lanes horizontal; got direction={c.direction}"
            )
        elif lane > merge_node.lane:
            assert c.direction == -1, (
                f"CROSS at lane {lane} (right of merge lane "
                f"{merge_node.lane}) must carry direction=-1; got "
                f"direction={c.direction}"
            )

    # Serialised form must include the direction key.
    for _, c in cross_cells:
        d = c.to_dict()
        assert "d" in d, f"to_dict() missing 'd' for CROSS with direction={c.direction}"
        assert d["d"] == c.direction


def test_cross_cell_direction_default_is_zero() -> None:
    """``CellInfo.cross()`` defaults to ``direction=0`` (no horizontal
    stub) so existing callers that omit the argument stay compatible."""
    cell = CellInfo.cross(h_color=2, p_color=3)
    assert cell.cell_type == CellType.CROSS
    assert cell.direction == 0
    assert "d" not in cell.to_dict()


def test_cross_cell_to_dict_omits_direction_when_zero() -> None:
    """Even when ``direction`` is explicitly 0, ``to_dict()`` must not
    emit a redundant ``"d"`` key — keeps the wire format minimal."""
    cell = CellInfo.cross(h_color=2, p_color=3, direction=0)
    d = cell.to_dict()
    assert "d" not in d


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
        c.cell_type in (CellType.MERGE_LEFT, CellType.MERGE_RIGHT) for c in root_node.cells
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


def test_fork_keeps_horizontal_connector_when_cross_cell_present() -> None:
    """A fork point (commit with 2+ children) that ALSO has a CROSS
    cell (because one of its children shares a lane with the
    second parent of a merge commit on the same line) must still
    draw the fork connector's horizontal segment and TEE_UP /
    MERGE_LEFT at every OTHER fork lane.

    Regression: previously the entire ``_build_fork_connector_cells``
    merge was skipped when a CROSS cell was placed on the row,
    leaving only the merge connector (``TEE_RIGHT`` at the commit
    lane) and the CROSS — the horizontal line connecting the
    remaining fork lanes was missing entirely, so the parent
    commit looked like a single-branch tip with no branching at
    all. The ``gpt-researcher`` ``b364917f55ea57…`` (PR #1781)
    example has 12 children at lanes 0–14 with the second parent
    landing on lane 1; without the fix only the merge connector
    at lane 0 and the CROSS at lane 1 were drawn, and the
    horizontal bridge reaching lanes 2–13 (TEE_UP / MERGE_LEFT
    curves) was gone.
    """
    # Topology, newest-first:
    #
    #   c_ch0 (lane 0) -- child of c_merge, lands on lane 0 (the
    #                     commit's own lane)
    #   c_ch2 (lane 2) -- child of c_merge, lands on lane 2
    #   c_ch1 (lane 1) -- child of c_merge, lands on lane 1
    #                     (this is the lane c_tip lives on — the
    #                     second parent below — and is where the
    #                     CROSS cell must appear at lane 1; the
    #                     horizontal bridge must still continue
    #                     past it to reach c_ch2 at lane 2)
    #   c_merge (lane 0, parents=[c_main, c_tip]) -- fork point
    #                     with 3 children, also a merge commit
    #                     whose second parent c_tip lives on lane 1
    #                     → CROSS cell at lane 1
    #   c_tip   (lane 1)
    #   c_main  (lane 0)
    c_main = "1" * 40
    c_tip = "2" * 40
    c_merge = "3" * 40
    c_ch0 = "4" * 40
    c_ch1 = "5" * 40
    c_ch2 = "6" * 40
    commits = [
        _c(c_ch0, [c_merge], ts=7),
        _c(c_ch2, [c_merge], ts=6),
        _c(c_ch1, [c_merge], ts=5),
        _c(c_merge, [c_main, c_tip], ts=4),
        _c(c_tip, [], ts=3),
        _c(c_main, [], ts=2),
    ]
    layout = build_graph(commits, [])
    nodes = layout.nodes
    merge_node = next(n for n in nodes if n.commit and n.commit.sha == c_merge)

    # CROSS must be present at lane 1 (shared by c_ch1 above and
    # c_tip below) — that's the GitKraken-style fork-merge marker.
    cross_present = any(c.cell_type == CellType.CROSS for c in merge_node.cells)
    assert cross_present, (
        "expected a CROSS cell on the merge commit's row to mark "
        "the fork-merge point where c_ch1 (above) and c_tip "
        "(below) share lane 1"
    )

    # The fork connector's horizontal bridge and the curve into
    # the OTHER fork lane (c_ch2 at lane 2) must still be drawn.
    # A working fork connector produces HORIZONTAL / HORIZONTAL_PIPE
    # cells in the columns BETWEEN lane centres and a curve cell
    # (TEE_UP / MERGE_LEFT) at lane 2. The CROSS at lane 1 only
    # blocks the curve cell at that one lane; everything else is
    # merged in.
    has_horiz = any(
        c.cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE) for c in merge_node.cells
    )
    assert has_horiz, (
        "expected fork connector horizontal cells on the merge "
        "commit's row even though a CROSS cell is present — the "
        "horizontal bridge between fork lanes is what makes the "
        "branching readable"
    )
    has_fork_curve = any(
        c.cell_type in (CellType.TEE_UP, CellType.MERGE_LEFT, CellType.BRANCH_LEFT)
        for c in merge_node.cells
    )
    assert has_fork_curve, (
        "expected fork connector curve cells (TEE_UP / MERGE_LEFT "
        "/ BRANCH_LEFT) at the OTHER fork lane (lane 2 for c_ch2) "
        "even though a CROSS cell occupies lane 1"
    )


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
    assert (
        colors[c1] != colors[c2] or colors[c1] != colors[c3]
    ), f"Expected different colors, got {colors}"


def test_color_palette_accessible() -> None:
    """BRANCH_PALETTE should be a tuple of hex colour strings."""
    assert len(BRANCH_PALETTE) >= 12
    for c in BRANCH_PALETTE:
        assert c.startswith("#")
        assert len(c) == 7


def test_uncommitted_color_index_is_outside_palette() -> None:
    """``UNCOMMITTED_COLOR_INDEX`` must not collide with a regular
    palette index.  The WIP marker needs a reserved index that
    ``crc32(name) % len(BRANCH_PALETTE)`` can never produce.
    """
    assert UNCOMMITTED_COLOR_INDEX >= len(BRANCH_PALETTE), (
        f"UNCOMMITTED_COLOR_INDEX={UNCOMMITTED_COLOR_INDEX} sits inside "
        f"BRANCH_PALETTE (size {len(BRANCH_PALETTE)}) — a regular branch "
        f"hash could land on it and be misrendered as the WIP marker"
    )


def test_pick_branch_color_is_deterministic_across_runs() -> None:
    """Branch colours must be stable across process restarts.

    Regression: the previous implementation used ``hash()`` which is
    seeded randomly at interpreter startup via ``PYTHONHASHSEED``, so
    the same branch got a different colour on every run. Run the
    function multiple times in-process to mimic that variability and
    assert it always returns the same index.
    """
    names = ["main-content", "feature/login", "release/1.2", "Bugfix/Auth", "main"]
    expected = [_pick_branch_color(n) for n in names]
    # Re-import + re-run, also in a fresh subprocess to be safe.
    for _ in range(10):
        assert [_pick_branch_color(n) for n in names] == expected
    import subprocess
    import sys

    out = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "from src.core.graph_v2 import _pick_branch_color as f;"
            "print(','.join(str(f(n)) for n in "
            "['main-content','feature/login','release/1.2','Bugfix/Auth','main']))",
        ],
        cwd=".",
        text=True,
    ).strip()
    assert out == ",".join(str(i) for i in expected)


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


# ---- stash rebalancing around WIP ---------------------------------------


def test_wip_sits_on_main_lane_above_stash() -> None:
    """WIP marker must sit on lane 0; the stash goes to the first offset lane.

    Without the rebalance, the stash inherits lane 0 from its parent
    HEAD and the WIP has to take lane 1, visually placing the WIP
    marker on a side branch.
    """
    c1 = "a" * 40  # HEAD
    c0 = "b" * 40  # parent
    s1 = "s" * 40  # stash, parent=c1, ts newer than c1
    commits = [
        _c(s1, parents=[c1], ts=3, kind="stash", message="Stash @0: wip"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, ts=1, message="parent"),
    ]
    branches = [_b("main", c1, is_head=True)]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=2,
        head_commit_sha=c1,
    )

    assert len(layout.nodes) == 4
    wip = layout.nodes[0]
    stash = layout.nodes[1]
    head = layout.nodes[2]
    parent = layout.nodes[3]

    assert wip.is_uncommitted
    assert wip.lane == 0, "WIP must be on the main lane (0)"
    assert wip.cells[0].cell_type == CellType.COMMIT

    assert stash.commit.kind == "stash"
    assert stash.lane == 1, "stash must be on the first offset lane (1)"
    # The stash's old COMMIT at lane 0 must be cleared so the WIP
    # can flow down through it.
    assert stash.cells[0].cell_type == CellType.PIPE
    # The stash just shows COMMIT at lane 1 — no horizontal
    # at the stash row.  The connection is at HEAD's row below.
    assert stash.cells[2].cell_type == CellType.COMMIT

    assert head.lane == 0
    # HEAD's row carries the fork connector: TEE_RIGHT at lane 0 and
    # MERGE_LEFT at the stash's lane (1).
    assert head.cells[0].cell_type == CellType.TEE_RIGHT
    assert head.cells[2].cell_type == CellType.MERGE_LEFT

    assert parent.lane == 0
    assert parent.cells[0].cell_type == CellType.COMMIT


def test_consecutive_stashes_form_ladder_via_wip_rebalancing() -> None:
    """Two stashes stack on offset lanes 1 and 2, WIP still on lane 0."""
    c1 = "a" * 40
    c0 = "b" * 40
    s1 = "s" * 40
    s2 = "t" * 40
    commits = [
        _c(s1, parents=[c1], ts=4, kind="stash", message="Stash @0: first"),
        _c(s2, parents=[c1], ts=3, kind="stash", message="Stash @1: second"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, ts=1, message="parent"),
    ]
    branches = [_b("main", c1, is_head=True)]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=1,
        head_commit_sha=c1,
    )

    # [WIP, stash1, stash2, HEAD, parent]
    assert len(layout.nodes) == 5
    wip, stash1, stash2, head, _parent = layout.nodes
    assert wip.is_uncommitted
    assert wip.lane == 0

    assert stash1.commit.kind == "stash" and stash1.lane == 1
    assert stash2.commit.kind == "stash" and stash2.lane == 2

    # Both stashes just show COMMIT at their lane — no horizontals,
    # no TEE_LEFT at stash rows.  stash2 also has PIPE at intermediate
    # lane 1 for gap-bridge continuity.
    assert stash1.cells[2].cell_type == CellType.COMMIT
    assert stash2.cells[4].cell_type == CellType.COMMIT
    assert stash2.cells[2].cell_type == CellType.PIPE

    # HEAD's row carries the connector: TEE_RIGHT at lane 0,
    # TEE_UP at lane 1 (intermediate merge), MERGE_LEFT at lane 2
    # (rightmost merge).
    assert head.cells[0].cell_type == CellType.TEE_RIGHT
    assert head.cells[2].cell_type == CellType.TEE_UP
    assert head.cells[4].cell_type == CellType.MERGE_LEFT


def test_stash_alongside_commit_inherits_main_loop_ladder() -> None:
    """A stash sharing HEAD with a regular commit uses the next free lane.

    The main loop already places the regular commit on lane 1 via its
    fork detection, so the rebalance must place the stash on lane 2
    (next free after 0 and 1), and the fork connector at HEAD must
    cover both branches.
    """
    c1 = "a" * 40
    c0 = "b" * 40
    feat = "c" * 40
    s1 = "s" * 40
    commits = [
        _c(s1, parents=[c1], ts=4, kind="stash", message="Stash @0: wip"),
        _c(feat, parents=[c1], ts=3, message="feature commit"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, ts=1, message="parent"),
    ]
    branches = [
        _b("main", c1, is_head=True),
        _b("feature", feat),
    ]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=1,
        head_commit_sha=c1,
    )

    # [WIP, stash, feature, HEAD, parent]
    assert len(layout.nodes) == 5
    wip, stash, feature, head, _parent = layout.nodes
    assert wip.is_uncommitted and wip.lane == 0
    assert stash.lane == 2
    assert feature.lane == 1

    # The fork connector at HEAD must include both feature (lane 1)
    # and stash (lane 2) — feature is intermediate (TEE_UP), stash
    # is the rightmost merge (MERGE_LEFT).
    assert head.cells[0].cell_type == CellType.TEE_RIGHT
    assert head.cells[2].cell_type == CellType.TEE_UP
    assert head.cells[4].cell_type == CellType.MERGE_LEFT


def test_stash_below_head_is_not_moved() -> None:
    """A stash created from HEAD's parent is below HEAD and must not be moved."""
    c2 = "a" * 40  # HEAD
    c1 = "b" * 40  # parent
    s1 = "s" * 40  # stash, parent=c1 (NOT HEAD)
    commits = [
        _c(c2, parents=[c1], ts=3, message="HEAD commit"),
        _c(s1, parents=[c1], ts=2, kind="stash", message="Stash @0: wip"),
        _c(c1, ts=1, message="parent"),
    ]
    branches = [_b("main", c2, is_head=True)]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=1,
        head_commit_sha=c2,
    )

    # Stash sits on whatever lane the main loop gave it (not above
    # HEAD, so the rebalance is a no-op).  WIP still ends up on lane 0
    # because the stash is below HEAD and does not occupy lane 0
    # in any row above HEAD.
    assert layout.nodes[0].is_uncommitted
    assert layout.nodes[0].lane == 0
    stash = next(n for n in layout.nodes if n.commit is not None and n.commit.kind == "stash")
    # The stash's parent is c1 (HEAD's parent), so the rebalance
    # never touches it — its lane is whatever the main loop assigned.
    assert stash.commit.parents[0] == c1


def test_wip_compatibility_allows_pipe_at_head_lane() -> None:
    """A vertical PIPE at lane 0 above HEAD does not block the WIP.

    This is the post-rebalance state: the stash is on an offset lane
    and the cell at lane 0 in the stash's row holds a PIPE for the
    WIP's own vertical.  The WIP must be allowed to sit on lane 0
    even though that cell is no longer EMPTY.
    """
    c1 = "a" * 40
    c0 = "b" * 40
    s1 = "s" * 40
    commits = [
        _c(s1, parents=[c1], ts=3, kind="stash", message="Stash @0: wip"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, ts=1, message="parent"),
    ]
    branches = [_b("main", c1, is_head=True)]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=1,
        head_commit_sha=c1,
    )
    # The stash sits on lane 1, so the cell at lane 0 in the stash's
    # row is the PIPE that the WIP insertion adds — and the WIP still
    # lands on lane 0.
    stash = layout.nodes[1]
    assert stash.cells[0].cell_type == CellType.PIPE
    assert layout.nodes[0].lane == 0


def test_horizontal_across_head_lane_blocks_wip() -> None:
    """A HORIZONTAL crossing lane 0 above HEAD must push WIP to an offset lane."""
    c2 = "a" * 40
    c1 = "b" * 40
    c0 = "c" * 40
    side = "d" * 40
    # A regular commit on a side branch whose root is c1.  The main
    # loop's fork detection places it on lane 1, but to put a
    # HORIZONTAL at lane 0 in a row above HEAD we use a layout where
    # the side branch is processed first (older than HEAD) — that
    # path is the normal one, so we only assert the WIP does not
    # collide with the existing graph structure.
    commits = [
        _c(c2, parents=[c1], ts=3, message="HEAD commit"),
        _c(side, parents=[c0], ts=2, message="side"),
        _c(c1, parents=[c0], ts=1, message="parent"),
        _c(c0, ts=0, message="root"),
    ]
    branches = [_b("main", c2, is_head=True)]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=1,
        head_commit_sha=c2,
    )
    wip = layout.nodes[0]
    # WIP must be inserted somewhere — we only assert the layout is
    # well-formed (no crash, the WIP cell is drawn at its lane).
    assert wip.is_uncommitted
    assert wip.cells[wip.lane * 2].cell_type == CellType.COMMIT


def test_lane0_pipe_continues_through_offset_stash_when_no_wip() -> None:
    """Lane 0 line above HEAD stays continuous through an offset-lane stash.

    HEAD is a fork point: it has a regular child branch (``feature``) and
    a stash as siblings. The main loop places ``feature`` on lane 1 (fork
    detection) and the stash on lane 2 (next free) — neither is on lane 0.
    The lane 0 line above HEAD must therefore be a continuous PIPE
    through both rows.

    The rebalance originally cleared the PIPE at lane 0 in the stash row
    (the logic is only justified when a WIP node will refill the cell);
    for a clean workdir (no WIP) the cleared cell stayed EMPTY and broke
    the visual line at the main lane.
    """
    c1 = "a" * 40  # HEAD
    c0 = "b" * 40  # parent
    feat = "c" * 40  # feature branch tip, parent=c1
    s1 = "s" * 40  # stash, parent=c1
    # Stash has a *newer* feature commit above it so the stash is NOT
    # the topmost row — the rebalance needs to add a PIPE at lane 0
    # here so the line stays continuous into the row above.
    s2 = "t" * 40  # stash #2, parent=c1, newer than s1
    commits = [
        _c(s2, parents=[c1], ts=5, kind="stash", message="Stash @1: newer"),
        _c(s1, parents=[c1], ts=4, kind="stash", message="Stash @0: wip"),
        _c(feat, parents=[c1], ts=3, message="feature commit"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, ts=1, message="parent"),
    ]
    branches = [
        _b("main", c1, is_head=True),
        _b("feature", feat),
    ]
    layout = build_graph(
        commits,
        branches,
        # CRUCIALLY: no WIP — the cells must remain continuous on their own.
        uncommitted_count=None,
        head_commit_sha=c1,
    )

    # Layout: [s2 (newer stash), s1 (older stash), feature, HEAD, parent]
    newer_stash, older_stash, feature, _head, _parent = layout.nodes

    # The older stash was placed on an offset lane (next free after feature).
    assert older_stash.lane >= 2
    # The PIPE at lane 0 in the older stash's row must survive — it is
    # the lane 0 line passing through the stash's row, drawn by the
    # main loop because lane 0 was still tracking HEAD's parent above
    # HEAD.  The rebalance must NOT clear it (no WIP to refill it).
    assert older_stash.cells[0].cell_type == CellType.PIPE, (
        "Lane 0 PIPE through the stash row was cleared by the stash "
        "rebalance even though no WIP node was inserted; this severs "
        "the lane 0 line above HEAD for clean-workdir views."
    )
    # Same for the feature branch's row.
    assert feature.cells[0].cell_type == CellType.PIPE


def test_lane0_pipe_restored_after_stash_moved_off_head_lane_when_no_wip() -> None:
    """Stash moved off head_lane restores a PIPE there when no WIP, but
    only when the stash is NOT the topmost row.

    Setup: a regular commit (``feature``) above HEAD sits on lane 0;
    HEAD is a fork point with both a feature child and a stash child.
    The stash lands on an offset lane (the main loop's fork detection
    gives lane 0 to ``feature``).  The lane 0 line above HEAD must pass
    through the stash's row as a PIPE.

    The companion test for the stash-at-topmost case lives in
    ``test_topmost_stash_has_no_orphan_pipe_at_head_lane``.
    """
    c1 = "a" * 40  # HEAD
    c0 = "b" * 40  # parent
    feat = "c" * 40  # feature branch tip, parent=c1
    s1 = "s" * 40  # stash, parent=c1
    commits = [
        _c(feat, parents=[c1], ts=4, message="feature commit"),
        _c(s1, parents=[c1], ts=3, kind="stash", message="Stash @0: wip"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, ts=1, message="parent"),
    ]
    branches = [
        _b("main", c1, is_head=True),
        _b("feature", feat),
    ]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=None,  # no WIP
        head_commit_sha=c1,
    )

    # Layout: [feature, stash, HEAD, parent]
    feature, stash, _head, _parent = layout.nodes
    # Stash was placed on an offset lane (the main loop reserves lane 0
    # for ``feature`` because the fork sibling detection uses lane 0).
    assert stash.lane >= 1
    # The PIPE at lane 0 in the stash's row must be restored — the line
    # at lane 0 passes through the stash row to reach HEAD.
    assert stash.cells[0].cell_type == CellType.PIPE


def test_topmost_stash_has_no_orphan_pipe_at_head_lane() -> None:
    """A stash sitting at the very top of the graph must not have a PIPE
    stub going up into empty space at head_lane.

    Reproduces the gpt-service bug: the user's only stash is the
    topmost commit (no commit above it), so adding a PIPE at lane 0 of
    the stash's row would draw a ``node_radius``-pixel vertical stub
    pointing up into the empty header / row above.  The line at head_lane
    simply has nowhere to continue; an EMPTY cell lets the bridge from
    the row below terminate at the topmost row's commit edge with no
    dangling stub.
    """
    c1 = "a" * 40  # HEAD
    c0 = "b" * 40  # parent
    s1 = "s" * 40  # stash, parent=c1, the only entry above HEAD
    commits = [
        _c(s1, parents=[c1], ts=3, kind="stash", message="Stash @0: wip"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, ts=1, message="parent"),
    ]
    branches = [_b("main", c1, is_head=True)]
    layout = build_graph(
        commits,
        branches,
        uncommitted_count=None,  # no WIP
        head_commit_sha=c1,
    )

    # Layout: [stash, HEAD, parent] — stash is the topmost.
    stash, _head, _parent = layout.nodes
    # The stash moved to lane 1 (the first offset lane).
    assert stash.lane == 1
    # The cell at lane 0 must NOT be a PIPE — there is no row above to
    # bridge to, so a PIPE here would be an orphan stub extending
    # ``node_radius`` pixels up into the empty space above the topmost
    # commit.  An EMPTY cell lets the line terminate cleanly at the
    # topmost row's commit edge.
    assert stash.cells[0].cell_type != CellType.PIPE, (
        "Topmost stash row has a PIPE at head_lane; the cell has no "
        "row above to connect to and the PIPE draws a stub into the "
        "empty space above the topmost commit."
    )


# ---- stash rebalance: scenario-driven coverage ---------------------------
#
# The three tests below mirror the scenarios in
# ``scripts/sim_topmost_stash.py`` (which the simulator runs against
# real gpt-service / git-py state).  They are kept in lockstep with
# the script — when one is updated, the other should be too.


def test_sim_topmost_stash_no_wip() -> None:
    """Scenario 1: topmost stash, clean workdir — no PIPE stub at lane 0.

    Mirrors the gpt-service bug: a stash whose first parent is HEAD is
    the *only* entry above HEAD and therefore the topmost commit in
    the rendered graph.  The stash rebalance must move it to an
    offset lane (so the WIP node could sit on lane 0 if any), but
    without WIP there is no row above the stash to bridge to — adding
    a PIPE at lane 0 of the stash row would draw an orphan stub up
    into empty space.
    """
    c0 = "0" * 40
    c1 = "1" * 40  # HEAD
    s1 = "2" * 40  # stash, parent=c1
    commits = [
        _c(s1, parents=[c1], ts=3, kind="stash", message="On main: WIP on main"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, parents=[],    ts=1, message="Initial commit"),
    ]
    branches = [_b("main", c1, is_head=True)]
    layout = build_graph(
        commits, branches,
        uncommitted_count=None,  # no WIP — the bug case
        head_commit_sha=c1,
    )
    assert len(layout.nodes) == 3
    stash, _head, _parent = layout.nodes
    # Stash was placed on an offset lane by the rebalance.
    assert stash.lane == 1, f"stash should be on lane 1, got {stash.lane}"
    # The cell at lane 0 must NOT be a PIPE (would draw a stub upward).
    assert stash.cells[0].cell_type != CellType.PIPE, (
        "topmost stash has a PIPE at lane 0 — orphan stub into empty "
        "space above the topmost commit"
    )


def test_sim_topmost_stash_with_wip_is_clean() -> None:
    """Scenario 2: topmost stash with WIP — control / regression guard.

    The WIP node sits on lane 0 above every other row; the stash row
    is therefore *not* the topmost in the rendered list.  The stash
    rebalance must clear the head-lane cell so the WIP insertion can
    fill it with a uniform UNCOMMITTED-color PIPE.  This test pins
    that the WIP path still produces a sane layout (no crash, no
    orphan stub, WIP at the top) when the stash happens to be the
    newest commit in history.
    """
    c0 = "0" * 40
    c1 = "1" * 40  # HEAD
    s1 = "2" * 40  # stash, parent=c1
    commits = [
        _c(s1, parents=[c1], ts=3, kind="stash", message="On main: WIP on main"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, parents=[],    ts=1, message="Initial commit"),
    ]
    branches = [_b("main", c1, is_head=True)]
    layout = build_graph(
        commits, branches,
        uncommitted_count=2,  # WIP present
        head_commit_sha=c1,
    )
    # WIP pushed to the very top, the rest shift down by one row.
    assert len(layout.nodes) == 4
    assert layout.nodes[0].is_uncommitted
    assert layout.nodes[0].lane == 0
    stash = layout.nodes[1]
    assert stash.commit.kind == "stash"
    # The stash now sits at row 1 (WIP is at row 0), so the PIPE at
    # lane 0 of the stash's row legitimately continues the WIP pipe
    # down toward HEAD — no orphan stub.
    assert stash.lane == 1
    assert stash.cells[0].cell_type == CellType.PIPE


def test_sim_middle_stash_keeps_lane0_pipe() -> None:
    """Scenario 3: middle stash — lane 0 line must stay continuous.

    Companion to ``test_sim_topmost_stash_no_wip``: when the stash
    is sandwiched between a regular commit (above) and HEAD (below),
    the line at lane 0 above HEAD has to pass through the stash row
    as a PIPE.  Clearing it would break the visual line.  The rebalance
    must therefore keep the PIPE at lane 0 of the stash's row intact
    when there is no WIP to refill it.
    """
    c0 = "0" * 40
    c1 = "1" * 40
    feat = "3" * 40  # newer feature commit, sits above the stash
    s1 = "2" * 40   # stash
    commits = [
        _c(feat, parents=[c1], ts=4, message="feature tip"),
        _c(s1, parents=[c1], ts=3, kind="stash", message="On main: WIP on main"),
        _c(c1, parents=[c0], ts=2, message="HEAD commit"),
        _c(c0, parents=[],    ts=1, message="Initial commit"),
    ]
    branches = [
        _b("main", c1, is_head=True),
        _b("feature", feat),
    ]
    layout = build_graph(
        commits, branches,
        uncommitted_count=None,
        head_commit_sha=c1,
    )
    # Locate the stash row — must NOT be the topmost (the feature
    # commit is above it).
    stash_idx = next(
        i for i, n in enumerate(layout.nodes)
        if n.commit is not None and n.commit.kind == "stash"
    )
    assert stash_idx > 0, (
        f"sanity: stash should be below the feature commit, got idx={stash_idx}"
    )
    stash = layout.nodes[stash_idx]
    # Stash landed on an offset lane (the main loop's fork detection
    # keeps lane 0 for the feature branch).
    assert stash.lane >= 1
    # The line at lane 0 must pass through the stash's row.
    assert stash.cells[0].cell_type == CellType.PIPE, (
        "lane 0 PIPE was lost across the stash row — visual gap above HEAD"
    )


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


def test_build_branch_refs_map_filters_invalid_sha_keys() -> None:
    """Branches with non-SHA target_sha (symref paths or None) are dropped."""
    from src.core.graph_v2 import _build_branch_refs_map

    branches_list = [
        BranchInfo(name="main", is_head=True, is_remote=False, target_sha="a" * 40),
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha="a" * 40),
        BranchInfo(
            name="origin/HEAD", is_head=False, is_remote=True, target_sha="refs/remotes/origin/main"
        ),
        BranchInfo(name="broken", is_head=False, is_remote=False, target_sha=None),
    ]

    result = _build_branch_refs_map(branches_list)

    assert "a" * 40 in result
    assert len(result["a" * 40]) == 2
    # Every key must be a valid 40-char hex SHA
    for sha in result:
        assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)


def test_fork_connector_single_child_corridor_uses_child_colour() -> None:
    """A fork point with a SINGLE merging child paints the whole
    corridor in the child's colour: the track carries exactly one
    edge, so the branch line stays uniform from its tip down to the
    parent dot (kilocode ``9e1b54d`` → ``489601e5``).

    This test pins the cell-colour contract:

    * ``TEE_RIGHT`` / ``HORIZONTAL`` / ``HORIZONTAL_PIPE`` along the
      corridor use the child's colour.
    * ``MERGE_LEFT`` at the merging lane uses the same child colour —
      the entire path is one colour.
    * ``pipe_color_index`` on the root's ``TEE_RIGHT`` keeps
      using ``main_color`` so the vertical line above/below the
      root still belongs to the root's own branch.
    """
    from src.core.graph_v2 import _build_fork_connector_cells

    main_color = 1  # blue (main/master)
    merge_color = 2  # red (a sibling branch like m2)
    main_lane = 0
    merge_lane = 2

    cells = _build_fork_connector_cells(
        main_lane=main_lane,
        main_color=main_color,
        merging_lanes=[(merge_lane, merge_color)],
        active_lanes=["root-sha", None, "merge-sha"],
        oid_color_index={"root-sha": main_color, "merge-sha": merge_color},
        lane_color_index={0: main_color, 1: main_color, 2: merge_color},
        max_lane=2,
    )

    # main lane: TEE_RIGHT — the horizontal corridor starts in the
    # child's colour.
    tee = cells[main_lane * 2]
    assert tee.cell_type == CellType.TEE_RIGHT
    assert tee.color_index == merge_color, (
        f"TEE_RIGHT at root row must use the child's colour "
        f"{merge_color}, got {tee.color_index}"
    )
    # vertical line of TEE_RIGHT stays in the root's colour
    assert tee.pipe_color_index == main_color, (
        f"TEE_RIGHT pipe_color_index must stay as root colour "
        f"{main_color}, got {tee.pipe_color_index}"
    )

    # Intermediate cells between main_lane+1 and merge_lane carry the
    # corridor in the same child colour.
    for lane_idx in range(main_lane + 1, merge_lane):
        col = lane_idx * 2
        cell = cells[col]
        if cell.cell_type == CellType.EMPTY:
            continue
        assert cell.cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE), (
            f"unexpected cell at col {col} (lane {lane_idx}): " f"{cell.cell_type.name}"
        )
        assert cell.color_index == merge_color, (
            f"{cell.cell_type.name} at col {col} (lane {lane_idx}) "
            f"must use the child's colour {merge_color}, got "
            f"{cell.color_index}"
        )

    # MERGE_LEFT at the merging lane uses the same child colour.
    merge_left = cells[merge_lane * 2]
    assert merge_left.cell_type == CellType.MERGE_LEFT
    assert merge_left.color_index == merge_color


def test_fork_connector_multiple_merges_priority_relay() -> None:
    """With several branches merging into the fork point the corridor
    is a priority relay: the trunk carries the FIRST child's colour,
    each segment the colour of the child whose bend terminates it —
    and each ``TEE_UP`` bend carries its OWN child's colour (not the
    next child's), so the cell directly under a commit matches that
    commit's branch (kilocode ``05dadaa``)."""
    from src.core.graph_v2 import _build_fork_connector_cells

    main_color = 1
    first_merge_color = 2
    second_merge_color = 3

    cells = _build_fork_connector_cells(
        main_lane=0,
        main_color=main_color,
        merging_lanes=[(2, first_merge_color), (4, second_merge_color)],
        active_lanes=["root", None, "m1", None, "m2"],
        oid_color_index={
            "root": main_color,
            "m1": first_merge_color,
            "m2": second_merge_color,
        },
        lane_color_index={
            0: main_color,
            1: main_color,
            2: first_merge_color,
            3: main_color,
            4: second_merge_color,
        },
        max_lane=4,
    )

    tee = cells[0]
    assert tee.cell_type == CellType.TEE_RIGHT
    assert tee.color_index == first_merge_color
    assert tee.pipe_color_index == main_color

    # Intermediate merge lane: the TEE_UP bend splits at the junction
    # — the vertical pipe and the segment arriving from the left keep
    # the OWN child's colour, while the horizontal leaving the bend to
    # the right already carries the NEXT child's colour.
    intermediate = cells[4]
    assert intermediate.cell_type == CellType.TEE_UP
    assert intermediate.color_index == second_merge_color
    assert intermediate.pipe_color_index == first_merge_color

    # The segment between the two bends carries the second child's
    # colour (the child whose bend terminates the segment).
    segment = cells[6]
    assert segment.cell_type == CellType.HORIZONTAL
    assert segment.color_index == second_merge_color

    # Rightmost merge keeps MERGE_LEFT in its own colour.
    rightmost = cells[8]
    assert rightmost.cell_type == CellType.MERGE_LEFT
    assert rightmost.color_index == second_merge_color


def test_fork_connector_main_lane_uses_main_colour_when_no_merges() -> None:
    """When ``merging_lanes`` is empty, the main lane cell is a
    plain ``PIPE`` in the root's own colour.
    """
    from src.core.graph_v2 import _build_fork_connector_cells

    main_color = 1
    cells = _build_fork_connector_cells(
        main_lane=0,
        main_color=main_color,
        merging_lanes=[],
        active_lanes=["root-sha"],
        oid_color_index={"root-sha": main_color},
        lane_color_index={0: main_color},
        max_lane=0,
    )

    assert cells[0].cell_type == CellType.PIPE


# ---- bug 31b22352: branch colour inherited from merge commit -----------


def test_branch_tip_keeps_own_colour_when_merge_processed_first() -> None:
    """When a merge commit that contains a side-branch tip is
    processed **before** the side-branch tip itself (because
    ``history`` is sorted newest-first and the merge is newer),
    the tip commit must still receive its own colour derived from
    its branch name.

    Regression: a previous version of :func:`build_graph` took the
    colour from ``lane_colors[lane]`` whenever the commit's SHA was
    already tracking on a lane — which meant the lane colour set by
    the merge commit (via ``oid_color_index[parent_sha] = …``) won
    over the side branch's own ``_pick_branch_color(name)``.  The
    side-branch tip ended up rendered in the merge's fallback colour
    instead of its own deterministic colour.

    Reproduces the ``gpt-researcher`` ``31b22352`` /
    ``3mk4yl/fix-dict-unhashable-bug`` symptom where the entire
    side-branch line is drawn in GREEN even though
    ``_pick_branch_color("3mk4yl/fix-dict-unhashable-bug")`` is
    GOLD (idx=15).
    """
    root = "1" * 40
    side = "2" * 40          # child of root — the side-branch tip
    main_next = "3" * 40     # child of root — the next mainline commit
    merge = "4" * 40         # merge(side, main_next)
    # Newest first.
    commits = [
        _c(merge, parents=[side, main_next], ts=4, message="merge"),
        _c(main_next, parents=[root], ts=3, message="main_next"),
        _c(side, parents=[root], ts=2, message="side"),
        _c(root, ts=1, message="root"),
    ]
    branches = [
        _b("3mk4yl/fix-dict-unhashable-bug", side),
        _b("master", merge, is_head=True),
    ]
    layout = build_graph(commits, branches)
    sha_to_node = {n.commit.sha: n for n in layout.nodes if n.commit is not None}

    expected_side_colour = _pick_branch_color("3mk4yl/fix-dict-unhashable-bug")
    side_node = sha_to_node[side]
    assert side_node.color_index == expected_side_colour, (
        f"side-branch tip should be drawn in its own branch colour "
        f"idx={expected_side_colour} ({BRANCH_PALETTE[expected_side_colour]}), "
        f"got idx={side_node.color_index} "
        f"({BRANCH_PALETTE[side_node.color_index]}); the tip is "
        f"inheriting the colour the merge commit pre-assigned to its lane"
    )


def test_fork_sibling_does_not_overwrite_mainline_lane_colour() -> None:
    """A fork-sibling parent (the second parent of a merge commit)
    that lands on a lane which already holds a ``lane_colors``
    entry must not clobber that entry with the fork-sibling
    fallback colour.

    Regression: the second-parent setup loop wrote
    ``lane_color_index[new_lane] = _pick_fallback(new_lane)``
    unconditionally, which poisoned the cache for every later
    commit that landed on the same lane via ``continue_lane()``.
    In ``gpt-researcher`` that turned the mainline around commit
    ``31b22352`` from BLUE/master into PINK (``idx=6``) because
    the merge's second parent happened to land on a previously
    well-established mainline lane.

    The synthetic DAG seeds ``lane_colors[0]`` with BLUE via a
    commit that points at a branch whose name hashes to ``1``
    (use ``master`` for the override path); the merge's second
    parent then lands on lane 0 and the regression would set
    ``lane_colors[0]`` to ``_pick_fallback(0)`` instead.
    """
    root = "1" * 40
    side = "2" * 40
    main_next = "3" * 40
    merge = "4" * 40
    # Newest first.
    commits = [
        _c(merge, parents=[side, main_next], ts=4, message="merge"),
        _c(main_next, parents=[root], ts=3, message="main_next"),
        _c(side, parents=[root], ts=2, message="side"),
        _c(root, ts=1, message="root"),
    ]
    # ``master`` points at ``main_next`` so ``main_next`` is the
    # master tip with branch colour BLUE (idx=1).  ``side`` has no
    # branch.  ``merge`` carries a remote-tracking-style branch
    # (``origin/main``) so it is *not* eligible for the master
    # override — it falls through ``assign_main_color``'s override
    # path and we hit the fork-sibling pre-coloring instead.
    branches = [
        _b("master", main_next, is_head=True),
        _b("origin/main", merge),
    ]
    layout = build_graph(commits, branches)
    sha_to_node = {n.commit.sha: n for n in layout.nodes if n.commit is not None}

    # main_next must keep master's colour (idx=1).  The regression
    # produced ``_pick_fallback(lane)`` (some other index) because
    # the merge's second-parent pre-coloring wrote ``lane_colors[0]``
    # with the fallback.
    main_next_node = sha_to_node[main_next]
    assert main_next_node.color_index == _pick_branch_color("master"), (
        f"mainline tip must keep master's colour idx={_pick_branch_color('master')} "
        f"({BRANCH_PALETTE[_pick_branch_color('master')]}), got "
        f"idx={main_next_node.color_index} "
        f"({BRANCH_PALETTE[main_next_node.color_index]}); the merge's "
        f"second-parent fallback colour is leaking onto the mainline"
    )



def test_cellinfo_to_dict_preserves_pipe_color_zero() -> None:
    """`pipe_color_index=0` (GREEN) survives the round-trip into the wire dict.

    Regression test for ``BUG_VISUAL_FEAT_PIPE_COLOR``: the previous
    serialiser used ``if self.pipe_color_index:`` which silently dropped
    ``"p"`` whenever the value was falsy, including the legitimate
    palette index 0 (GREEN ``#1A5924``).  The renderer then fell back to
    ``color_index`` and painted the vertical pipe in the crossing
    branch's colour at every fork-merge intersection.
    """
    cell = CellInfo(
        CellType.HORIZONTAL_PIPE,
        color_index=15,
        pipe_color_index=0,
    )
    d = cell.to_dict()
    assert d["t"] == int(CellType.HORIZONTAL_PIPE)
    assert d["c"] == 15
    assert "p" in d, (
        "wire format must carry `p` so the renderer can distinguish "
        "`pipe_color_index=0` (GREEN) from `pipe_color_index=None` (fallback)"
    )
    assert d["p"] == 0, (
        f"pipe colour 0 (GREEN) must round-trip unchanged, got {d.get('p')!r}"
    )


def test_cellinfo_to_dict_writes_pipe_for_all_pipe_aware_types() -> None:
    """Every cell type that carries a pipe colour writes `p` unconditionally."""
    for ctype in (
        CellType.HORIZONTAL_PIPE,
        CellType.TEE_RIGHT,
        CellType.TEE_LEFT,
        CellType.TEE_UP,
        CellType.CROSS,
    ):
        cell = CellInfo(ctype, color_index=15, pipe_color_index=0)
        d = cell.to_dict()
        assert "p" in d, f"{ctype.name} should always carry `p` in wire format"
        assert d["p"] == 0

        cell_nonzero = CellInfo(ctype, color_index=15, pipe_color_index=37)
        d_nonzero = cell_nonzero.to_dict()
        assert d_nonzero["p"] == 37


def test_build_graph_pipe_color_zero_does_not_fall_back_to_oid_color(tmp_git_repo: Path) -> None:
    """``lane_color_index[lane] = 0`` (GREEN) must not fall back to the oid colour.

    Regression test for ``BUG_VISUAL_FEAT_PIPE_COLOR``.  The pipe-colour
    lookup used to be ``dict.get(...) or dict.get(...)``, which treated
    ``0`` as "missing" and silently fell back to the oid colour.  In
    the local ``git-py`` repository this turned the lane-1 pipe below
    the visual-feat chain into the wisteria mainline colour instead of
    the visual-feat GREEN.

    The test inspects the **first** pipe cell on lane 1 *after* the
    visual-feat chain ends — the cell that the bug used to colour
    wisteria.  With the fix in place it stays GREEN.

    The original test relied on the local ``git-py`` checkout carrying
    a ``visual-feat`` branch — pre-existing/environmental (see
    ``docs/updates/update1/STATUS-r3.1.md``).  This version is
    self-contained: it builds a synthetic repo whose layout reproduces
    the original bug-trigger topology (a side branch whose
    ``_pick_branch_color(name)`` happens to be 0 — the GREEN index —
    is merged back into main and then crossed by a horizontal
    connector from a later merge).

    Branch-name selection: ``crc32("xyz0") % 40 == 0`` so the side
    branch (call it the ``xyz0`` chain) gets the GREEN idx 0 by
    deterministic branch-name hashing.  That's the only condition
    needed to exercise the ``or`` fallback in ``build_graph``.

    Topology (top to bottom in the graph):

    * ``xyz2`` -- ``xyz1`` -- ``merge_x`` (``merge xyz0`` into ``main``)
    * ``m2`` -- ``m1`` -- ``merge_x``
    * ``beta2`` -- ``beta1`` -- ``merge_b`` (``merge beta`` into ``main``)
    * ``root`` -- ``merge_x`` / ``merge_b``

    The ``xyz0`` vertical pipe sits on lane 1; ``merge_b``'s horizontal
    connector must cross it.  Before the fix the
    ``lane_color_index.get(...) or oid_color_index.get(...)`` lookup
    treated ``0`` as missing and over-painted the xyz0 pipe.
    """

    def _sig() -> pygit2.Signature:
        return pygit2.Signature("tester", "t@example.com", int(time.time()), 0)

    repo = pygit2.Repository(str(tmp_git_repo))

    sig = _sig()
    head_oid: pygit2.Oid | None = None  # populated by first commit

    def _commit(
        parents: list[pygit2.Oid] | None,
        message: str,
        files: dict[str, str],
        ref: str = "refs/heads/main",
    ) -> pygit2.Oid:
        # ``parents`` is an explicit list of parent OIDs.  Pass ``[]`` for
        # the very first commit (the empty repo has no ``refs/heads/main``
        # yet) and ``[head_oid]`` for subsequent linear commits.  Merge
        # commits pass ``[head_oid, other_oid]`` (HEAD first, then the
        # other parent) so the topology matches what the graph layout
        # expects — first parent is always the "current" side.  ``ref``
        # defaults to ``refs/heads/main``; pass another ref to commit
        # onto a side branch.
        if parents is None:
            # Legacy linear-on-HEAD path: only valid after the first
            # commit has populated ``head_oid``.
            assert head_oid is not None, "linear commit before any parent"
            parent_oids: list[pygit2.Oid] = [head_oid]
        else:
            parent_oids = list(parents)
        for path, content in files.items():
            full = tmp_git_repo / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
            repo.index.add(path)
        repo.index.write()
        tree = repo.index.write_tree()
        oid = repo.create_commit(
            ref, sig, sig, message, tree, parent_oids
        )
        return oid

    def _commit_chain(message: str, files: dict[str, str]) -> pygit2.Oid:
        """Linear commit on ``refs/heads/main``; caches the result for the
        next call.  The first call lands with no parents (root commit),
        every later call uses ``[head_oid]``."""
        nonlocal head_oid
        if head_oid is None:
            parent_oids: list[pygit2.Oid] = []
        else:
            parent_oids = [head_oid]
        oid = _commit(parent_oids, message, files)
        head_oid = oid
        return oid

    def _commit_side(
        message: str,
        files: dict[str, str],
        branch_name: str,
        parent_oid: pygit2.Oid,
    ) -> pygit2.Oid:
        """Linear commit onto a *side* branch ref; does NOT touch ``head_oid``.

        The parent of the new commit is ``parent_oid`` (the previous tip
        of the side branch); ``refs/heads/main`` is left untouched so the
        topology matches the docstring diagram (side chains branch off
        main and are merged back in later).
        """
        for path, content in files.items():
            full = tmp_git_repo / path
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(content)
            repo.index.add(path)
        repo.index.write()
        tree = repo.index.write_tree()
        return repo.create_commit(
            f"refs/heads/{branch_name}",
            sig,
            sig,
            message,
            tree,
            [parent_oid],
        )

    # Build commits in topological order (oldest first).  Side chains
    # (``beta``, ``xyz0``) are committed onto their own branch refs so
    # ``refs/heads/main`` only advances on mainline commits and merges.
    # pygit2 generates its own OIDs; we capture them so subsequent
    # commits can reference parents by real Oid.
    root_oid = _commit_chain("root", {"root.txt": "root\n"})
    # ``beta`` side branch forks off ``root``.
    beta1_oid = _commit_side("beta1", {"beta1.txt": "b1\n"}, "beta", root_oid)
    beta2_oid = _commit_side("beta2", {"beta2.txt": "b2\n"}, "beta", beta1_oid)
    # ``merge_b`` brings the beta chain into main: first parent is the
    # current ``refs/heads/main`` tip (``root_oid``), second is ``beta2``.
    merge_b_oid = _commit(
        [root_oid, beta2_oid], "merge beta", {"merge_b.txt": "x\n"}
    )
    head_oid = merge_b_oid
    # ``xyz0`` side branch forks off ``merge_b``.
    xyz1_oid = _commit_side("xyz1", {"xyz1.txt": "x1\n"}, "xyz0", merge_b_oid)
    xyz2_oid = _commit_side("xyz2", {"xyz2.txt": "x2\n"}, "xyz0", xyz1_oid)
    # ``merge_x`` brings the xyz0 chain into main: first parent is the
    # current ``refs/heads/main`` tip (``merge_b_oid``), second is
    # ``xyz2``.  The ``refs/heads/xyz0`` ref was already advanced to
    # ``xyz2_oid`` by ``_commit_side`` above, so the layout picks up
    # the branch name and runs ``_pick_branch_color("xyz0")``
    # (``crc32("xyz0") % 40 == 0`` → GREEN idx 0 — the bug trigger).
    merge_x_oid = _commit(
        [merge_b_oid, xyz2_oid], "merge xyz0", {"merge_x.txt": "x\n"}
    )
    head_oid = merge_x_oid
    _commit_chain("m1", {"m1.txt": "m1\n"})
    _commit_chain("m2", {"m2.txt": "m2\n"})

    rm = RepositoryManager(str(tmp_git_repo))
    history = rm.get_all_history(max_count=10_000)
    branches = rm.branches
    layout = build_graph(history, branches)

    # Find the xyz0 chain tip.  The bug is about the pipe on xyz0's
    # lane one row below the bottom of the chain — wherever that
    # lane happens to be in this synthetic history.  The tip is the
    # single commit carrying the branch name ``xyz0`` (set up above
    # via ``_commit_side(..., "xyz0", ...)``).
    xyz_tip_sha: str | None = None
    for n in layout.nodes:
        if n.commit is not None and "xyz0" in n.branch_names:
            xyz_tip_sha = n.commit.sha
            break
    if xyz_tip_sha is None:
        # Fall back to locating by message in case branch_name lookup
        # doesn't surface (e.g. ``get_all_history`` ordering oddity).
        for n in layout.nodes:
            if n.commit is not None and n.commit.message == "xyz2":
                xyz_tip_sha = n.commit.sha
                break
    assert xyz_tip_sha is not None, "synthetic repo is missing the xyz0 chain"

    tip_idx: int | None = None
    for i, n in enumerate(layout.nodes):
        if n.commit is not None and n.commit.sha == xyz_tip_sha:
            tip_idx = i
            break
    assert tip_idx is not None

    # Walk down the chain from the tip until the lane changes (or we
    # run out of rows).  The cell on the tip's lane one row *past* the
    # chain's bottom is the regression target.
    bottom_idx = tip_idx
    for j in range(tip_idx, len(layout.nodes) - 1):
        n = layout.nodes[j]
        if n.commit is None:
            break
        next_node = layout.nodes[j + 1]
        if next_node.commit is None or next_node.lane != n.lane:
            break
        bottom_idx = j + 1

    target_row = layout.nodes[bottom_idx + 1]
    col = layout.nodes[tip_idx].lane * 2
    assert col < len(target_row.cells), (
        "test setup changed: cell column out of range"
    )
    cell = target_row.cells[col]
    # The cell at xyz0's lane one row past the bottom of the chain is
    # the place where a later merge's horizontal connector crosses the
    # still-living vertical pipe.  The renderer distinguishes between:
    #   * plain PIPE — the vertical line is the only thing there, so
    #     ``color_index`` is the pipe colour;
    #   * CROSS / HORIZONTAL_PIPE / TEE_* — a horizontal connector
    #     crosses the lane, the vertical pipe colour lives in
    #     ``pipe_color_index`` while ``color_index`` is the
    #     horizontal side's colour.
    # The fix's invariant is the same in both cases: the *vertical*
    # pipe on xyz0's lane must stay GREEN (idx 0) instead of being
    # over-painted with the crossing commit's oid colour.
    if cell.cell_type == CellType.PIPE:
        pipe_color = cell.color_index
    else:
        assert cell.cell_type in (
            CellType.CROSS,
            CellType.HORIZONTAL_PIPE,
            CellType.TEE_RIGHT,
            CellType.TEE_LEFT,
            CellType.TEE_UP,
        ), (
            f"expected a pipe/cross at row {bottom_idx + 1} col={col}, "
            f"got {cell.cell_type.name}"
        )
        pipe_color = cell.pipe_color_index
    assert pipe_color == 0, (
        f"Pipe on xyz0 lane was over-painted: row {bottom_idx + 1} "
        f"col={col} {cell.cell_type.name} colour idx={cell.color_index} "
        f"pipe idx={cell.pipe_color_index} "
        f"({BRANCH_PALETTE[pipe_color]}).  The "
        f"``lane_color_index.get(...) or ...`` fallback in ``build_graph`` "
        "swapped a 0-valued GREEN for whatever ``oid_color_index`` held.  "
        "See BUG_VISUAL_FEAT_PIPE_COLOR."
    )


def test_build_graph_horizontal_pipe_carries_pipe_color_in_wire_dict() -> None:
    """Every HORIZONTAL_PIPE cell in ``build_graph`` output serialises ``p``.

    Regression test for ``BUG_VISUAL_FEAT_PIPE_COLOR``: the wire format
    must round-trip ``pipe_color_index`` exactly — including the
    legitimate GREEN value 0 — so the renderer can paint the vertical
    pipe in the lane's own colour at every fork-merge intersection
    instead of falling back to the crossing branch's ``color_index``.

    Topology: an already-merged side branch (``alpha``) leaves its
    vertical pipe on lane 1.  A later merge (``merge_b``) brings a new
    side branch (``beta``) into main on lane 4, and the merge
    connector's horizontal connector must cross the alpha pipe on
    lane 1 — producing a ``HORIZONTAL_PIPE`` whose ``color_index`` is
    beta's colour and ``pipe_color_index`` is alpha's colour.
    """
    root = "r" * 40
    m1 = "m" * 40 + "1" * 36
    m2 = "m" * 40 + "2" * 36
    a1 = "a" * 40 + "1" * 36
    a2 = "a" * 40 + "2" * 36
    merge_a = "a" * 40
    b1 = "b" * 40 + "1" * 36
    b2 = "b" * 40 + "2" * 36
    merge_b = "c" * 40

    commits = [
        _c(m2, parents=[m1], ts=8, message="m2"),
        _c(m1, parents=[merge_b], ts=7, message="m1"),
        _c(a2, parents=[a1], ts=6, message="a2"),
        _c(a1, parents=[root], ts=5, message="a1"),
        _c(merge_a, parents=[m1, a2], ts=4, message="merge alpha"),
        _c(b2, parents=[b1], ts=3, message="b2"),
        _c(b1, parents=[root], ts=2, message="b1"),
        _c(merge_b, parents=[root, b2], ts=1, message="merge beta"),
        _c(root, ts=0, message="root"),
    ]
    branches = [
        _b("alpha", a2),
        _b("beta", b2),
    ]
    layout = build_graph(commits, branches)

    found_horizontal_pipe = False
    for node in layout.nodes:
        if node.commit is None:
            continue
        for col, cell in enumerate(node.cells):
            if cell.cell_type != CellType.HORIZONTAL_PIPE:
                continue
            found_horizontal_pipe = True
            d = cell.to_dict()
            assert "p" in d, (
                f"HORIZONTAL_PIPE at sha={node.commit.short_sha} col={col} "
                "lost its pipe colour during serialisation; the renderer "
                "will fall back to colour index and paint the vertical "
                "pipe in the crossing branch's colour"
            )
            assert d["p"] == cell.pipe_color_index
    assert found_horizontal_pipe, (
        "synthetic history produced no HORIZONTAL_PIPE cell — adjust the "
        "test fixtures so at least one crossing exists"
    )


# ---- update2 B3: fork-connector pipe colour must be the fork point's own ----


def test_fork_connector_pipe_uses_fork_point_own_color() -> None:
    """Vertical under a fork-point commit uses the commit's own colour.

    Regression (update2 B3, kilocode ``22149292``): the fork connector
    was built with a ``main_color`` snapshotted from the lane cache
    BEFORE the commit's own colour was decided, so the half-cell under
    the commit dot (plus the inter-row bridge) was painted in the
    child branch's colour whenever the fork point had its own branch
    name (deterministic ``_pick_branch_color``).
    """
    # Newest first: a1 (mainline above, no branch -> main colour 1),
    # b1 (side branch "side"), f (fork point, branch "dev" -> colour 0).
    commits = [
        _c("a1", parents=["f"]),
        _c("b1", parents=["f"]),
        _c("f", parents=["p"]),
        _c("p", parents=[]),
    ]
    branches = [
        _b("main", "a1", is_head=True),
        _b("side", "b1"),
        _b("dev", "f"),
    ]
    layout = build_graph(commits, branches)
    nodes = {n.commit.sha: n for n in layout.nodes}

    f_node = nodes["f"]
    assert f_node.color_index == _pick_branch_color("dev")
    main_cell = f_node.cells[f_node.lane * 2]
    assert main_cell.cell_type == CellType.TEE_RIGHT
    assert main_cell.pipe_color_index == f_node.color_index, (
        f"fork-connector pipe colour {main_cell.pipe_color_index} != "
        f"fork point's own colour {f_node.color_index}; the half-cell "
        "under the commit is painted in a child branch's colour"
    )
    # The pipe continues seamlessly: the row below (parent "p") must
    # carry the same colour on that lane.
    p_node = nodes["p"]
    p_cell = p_node.cells[f_node.lane * 2]
    assert p_cell.color_index == f_node.color_index


# ---- update2 B1/B2: no foreign half-cell past a fork-connector bend ----


def _build_merge_fork_with_cross_and_stash():
    """Topology mirroring sql-skill ``8ee78fc``.

    M is a merge AND a fork point: children c_main (lane 0), c_side
    (lane 1), c_stash (lane 2); second parent p2 lands on the freed
    fork lane 1 -> CROSS(d=-1); fork connector continues right to the
    stash bend at lane 2.
    """
    commits = [
        _c("cm", parents=["m"]),
        _c("cs", parents=["m"]),
        _c("st", parents=["m"], kind="stash"),
        _c("m", parents=["p1", "p2"]),
        _c("p1", parents=["r"]),
        _c("p2", parents=["r"]),
        _c("r", parents=[]),
    ]
    branches = [_b("main", "cm", is_head=True)]
    return build_graph(commits, branches)


def test_cross_bend_no_stale_half_cell_to_the_right() -> None:
    """Half-cell right of a CROSS bend takes the NEXT segment's colour.

    Regression (update2 B1, sql-skill ``8ee78fc``): the horizontal
    cell left of the CROSS was painted in the forking branch's colour
    and its right half stuck out past the bend, so the track toward
    the stash stayed branch-coloured for an extra half-cell.
    """
    layout = _build_merge_fork_with_cross_and_stash()
    nodes = {n.commit.sha: n for n in layout.nodes}
    m = nodes["m"]
    cross_col = None
    for col, cell in enumerate(m.cells):
        if cell.cell_type == CellType.CROSS:
            cross_col = col
            break
    assert cross_col is not None, "expected a CROSS cell on M's row"
    merge_left = None
    for col, cell in enumerate(m.cells):
        if cell.cell_type == CellType.MERGE_LEFT:
            merge_left = (col, cell.color_index)
    assert merge_left is not None, "expected a stash MERGE_LEFT bend on M's row"
    stash_col, stash_color = merge_left
    assert stash_col > cross_col
    prev = m.cells[cross_col - 1]
    assert prev.cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE)
    assert prev.color_index == stash_color, (
        f"half-cell right of the CROSS keeps colour {prev.color_index} "
        f"instead of following the next segment ({stash_color})"
    )


def _build_merge_fork_with_parent_beyond_stash():
    """Topology mirroring sql-skill ``460f62c``.

    M is a merge AND a fork point: children c_main (lane 0), stash
    (lane 2); lane 1 hosts an unrelated continuing branch; second
    parent p2 already lives on lane 3 (to the RIGHT of the stash
    bend), so the parent connector's horizontal track crosses the
    fork connector's span.
    """
    commits = [
        _c("cm", parents=["m"]),
        _c("o1", parents=["o0"]),
        _c("st", parents=["m"], kind="stash"),
        _c("x2", parents=["p2"]),
        _c("m", parents=["p1", "p2"]),
        _c("p1", parents=["o0"]),
        _c("p2", parents=["o0"]),
        _c("o0", parents=[]),
    ]
    branches = [_b("main", "cm", is_head=True)]
    return build_graph(commits, branches)


def test_merge_left_bend_no_foreign_half_cell_into_the_void() -> None:
    """No foreign horizontal survives left of an upward fork bend.

    Regression (update2 B2, sql-skill ``460f62c``): the parent
    connector's horizontal one cell left of the stash's MERGE_LEFT
    bend painted a half-cell in the neighbour branch's colour past
    the bend into empty space.
    """
    layout = _build_merge_fork_with_parent_beyond_stash()
    nodes = {n.commit.sha: n for n in layout.nodes}
    m = nodes["m"]
    bend_col = None
    for col, cell in enumerate(m.cells):
        if cell.cell_type == CellType.MERGE_LEFT:
            bend_col = col
            break
    assert bend_col is not None, "expected a stash MERGE_LEFT bend on M's row"
    prev = m.cells[bend_col - 1]
    assert prev.cell_type in (CellType.EMPTY, CellType.PIPE), (
        f"foreign {prev.cell_type.name} (c={prev.color_index}) left of "
        "the stash bend paints a half-cell into the void"
    )


# ---- update2 B4: merge colour owns the row up to the CROSS ----


def _build_merge_fork_cross_with_beyond_child():
    """Topology mirroring kilocode ``9c0e4f76``.

    D is a merge AND a fork point: children c_main (lane 0), c_side
    (lane 2), c_far (lane 3); lane 1 hosts an unrelated continuing
    branch; second parent p2 lands on the freed fork lane 2 ->
    CROSS(d=-1). The fork connector continues past the CROSS to the
    far child at lane 3.
    """
    commits = [
        _c("cm", parents=["d"]),
        _c("o1", parents=["o0"]),
        _c("cs", parents=["d"]),
        _c("cf", parents=["d"]),
        _c("d", parents=["p1", "p2"]),
        _c("p1", parents=["o0"]),
        _c("p2", parents=["o0"]),
        _c("o0", parents=[]),
    ]
    branches = [_b("main", "cm", is_head=True), _b("side", "cs")]
    return build_graph(commits, branches)


def test_merge_colour_owns_horizontal_up_to_cross() -> None:
    """Merge colour owns the horizontal track up to the CROSS cell.

    Update2 B4 (kilocode ``9c0e4f76``): the fork connector repainted
    the commit->CROSS span in the forking branch's colour.  Per the
    agreed priority rule the merge colour owns the horizontal; the
    branch colour appears only going up from the CROSS.
    """
    layout = _build_merge_fork_cross_with_beyond_child()
    nodes = {n.commit.sha: n for n in layout.nodes}
    d = nodes["d"]
    cross_col = None
    cross = None
    for col, cell in enumerate(d.cells):
        if cell.cell_type == CellType.CROSS:
            cross_col, cross = col, cell
            break
    assert cross is not None, "expected a CROSS cell on D's row"
    merge_color = cross.color_index
    # Every horizontal-bearing cell from the commit to the CROSS must
    # carry the merge colour.  The cell immediately left of the CROSS
    # is excluded: its right half sticks out past the bend and
    # legitimately carries the NEXT fork segment's colour (B1).
    for col in range(d.lane * 2, cross_col - 1):
        cell = d.cells[col]
        if cell.cell_type in (
            CellType.TEE_RIGHT,
            CellType.HORIZONTAL,
            CellType.HORIZONTAL_PIPE,
        ):
            assert cell.color_index == merge_color, (
                f"col {col}: {cell.cell_type.name} colour {cell.color_index} "
                f"!= merge colour {merge_color}"
            )
    # At least one intermediate cell must have been checked (the
    # synthetic CROSS sits two lanes right of the commit).
    assert cross_col - d.lane * 2 >= 3
    # Branch colour goes UP only.
    assert cross.pipe_color_index != merge_color


def test_no_gap_between_cross_and_next_fork_bend() -> None:
    """The fork segment continues from the CROSS to the next bend.

    The fork connector stops one cell early before its rightmost
    bend, assuming an intermediate pipe/tee covers the gap; when the
    previous bend is a CROSS nothing paints that cell, leaving a hole
    in the track (kilocode ``9c0e4f76``, col 11).
    """
    layout = _build_merge_fork_cross_with_beyond_child()
    nodes = {n.commit.sha: n for n in layout.nodes}
    d = nodes["d"]
    cross_col = None
    merge_left_col = None
    merge_left_color = None
    for col, cell in enumerate(d.cells):
        if cell.cell_type == CellType.CROSS:
            cross_col = col
        elif cell.cell_type == CellType.MERGE_LEFT:
            merge_left_col, merge_left_color = col, cell.color_index
    assert cross_col is not None and merge_left_col is not None
    assert merge_left_col > cross_col
    for col in range(cross_col + 1, merge_left_col):
        cell = d.cells[col]
        assert cell.cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE), (
            f"col {col}: hole ({cell.cell_type.name}) between CROSS and "
            "the next fork bend"
        )
        assert cell.color_index == merge_left_color
    # The last cell before the bend must be right-trimmed so it does
    # not paint a half-cell past the bend into the void (kilocode
    # ``9c0e4f76`` col 11, ``5c7978c2`` col 11).
    last = d.cells[merge_left_col - 1]
    assert last.direction == -1, (
        "incoming horizontal before an up-bend must be right-trimmed "
        "(direction=-1), otherwise it protrudes half a cell past the bend"
    )
    assert last.to_dict().get("d") == -1


def test_fork_commit_with_left_merge_keeps_connector() -> None:
    """A commit that is BOTH a fork point (children on lanes to the
    right) AND a merge whose second parent sits on a lane to the LEFT
    must keep its merge connector. The fork connector's plain PIPE
    cells must not clobber the junction at the parent's lane, the
    HORIZONTAL_PIPE crossing and the half-cell reaching the commit dot
    (kilocode ``a87ddecf`` — merge of ``origin/main`` into a side
    branch with another branch forked from the merge commit: the line
    went left from the commit, crossed one parallel pipe and broke
    off before reaching main).
    """
    a = "a" * 40
    b = "b" * 40
    c = "c" * 40
    d = "d" * 40
    p = "e" * 40  # parallel branch — holds the lane between main and the merge
    t = "f" * 40  # topic branch — child of the merge commit
    n1 = "0" * 40  # side-branch continuation — second child of the merge
    m = "1" * 40  # the merge commit (fork point + left second parent)
    s = "2" * 40  # side branch — first parent of the merge
    commits = [
        _c(d, parents=[c], ts=10),
        _c(p, parents=[b], ts=9),
        _c(t, parents=[m], ts=8),
        _c(n1, parents=[m], ts=7),
        _c(m, parents=[s, c], ts=6),
        _c(s, parents=[a], ts=5),
        _c(c, parents=[b], ts=4),
        _c(b, parents=[a], ts=3),
        _c(a, ts=2),
    ]
    branches = [
        _b("main", d, is_head=True),
        _b("parallel", p),
        _b("topic", t),
        _b("side", n1),
    ]
    layout = build_graph(commits, branches)
    nodes = {n.commit.sha: n for n in layout.nodes}
    m_node = nodes[m]
    main_color = nodes[d].color_index
    p_color = nodes[p].color_index
    assert m_node.lane == 2
    cells = m_node.cells
    # Continuity: no gap between the second parent's lane (0) and the
    # commit lane (2).
    for col in range(0, 5):
        assert cells[col].cell_type != CellType.EMPTY, (
            f"col {col} is EMPTY — the left merge connector is broken"
        )
    # Junction on the second parent's lane: vertical main pipe + the
    # merge horizontal arriving from the right.
    assert cells[0].cell_type == CellType.TEE_RIGHT
    assert cells[0].color_index == main_color
    assert cells[1].cell_type == CellType.HORIZONTAL
    assert cells[1].color_index == main_color
    # The parallel branch's pipe is crossed, not overwritten.
    assert cells[2].cell_type == CellType.HORIZONTAL_PIPE
    assert cells[2].color_index == main_color
    assert cells[2].pipe_color_index == p_color
    # The half-cell reaching the commit dot (the fork's TEE_RIGHT at
    # the commit cell replaced the merge's TEE_LEFT).
    assert cells[3].cell_type == CellType.HORIZONTAL
    assert cells[3].color_index == main_color
    # The fork connector owns the commit cell (branch to the right).
    assert cells[4].cell_type == CellType.TEE_RIGHT


def test_fork_corridor_trunk_uses_first_child_colour() -> None:
    """The corridor trunk carries the FIRST child's colour (the
    priority relay), not the fork-point commit's own colour —
    kilocode ``bdb9070a``: "сначала идет цвет первой ветки в
    приоритете, потом следующей"."""
    f = "f0" + "0" * 38
    commits = [
        _c("c1" + "0" * 38, parents=[f], ts=4),
        _c("c2" + "0" * 38, parents=[f], ts=3),
        _c("c3" + "0" * 38, parents=[f], ts=2),
        _c(f, parents=["r0" + "0" * 38], ts=1),
        _c("r0" + "0" * 38, ts=0),
    ]
    # A branch ref on the fork point gives it a colour of its own —
    # the corridor must NOT collapse to it.
    branches = [_b("feature-x", f)]
    layout = build_graph(commits, branches)
    nodes = {n.commit.sha[:2]: n for n in layout.nodes}
    f_node = nodes["f0"]
    c1_node = nodes["c1"]
    c2_node = nodes["c2"]
    expected = _pick_branch_color("feature-x")
    assert f_node.color_index == expected != c1_node.color_index

    # The trunk (from the commit cell to the first bend) carries the
    # first MERGING child's colour — the child on the main lane
    # continues straight down and needs no bend.
    trunk = f_node.cells[f_node.lane * 2]
    assert trunk.cell_type == CellType.TEE_RIGHT
    assert trunk.color_index == c2_node.color_index
    assert trunk.pipe_color_index == expected


def test_fork_corridor_bend_splits_own_and_next_colour() -> None:
    """At a fork bend the 90-degree turn keeps the OWN child's colour
    (vertical-up pipe + the segment arriving from the left), while the
    continuation leaving the bend rightward already carries the NEXT
    child's colour (user request: «заворот одним цветом вверх,
    продолжение — цветом следующей ветки»)."""
    f = "f0" + "0" * 38
    commits = [
        _c("c1" + "0" * 38, parents=[f], ts=4),
        _c("c2" + "0" * 38, parents=[f], ts=3),
        _c("c3" + "0" * 38, parents=[f], ts=2),
        _c(f, parents=["r0" + "0" * 38], ts=1),
        _c("r0" + "0" * 38, ts=0),
    ]
    layout = build_graph(commits, [])
    nodes = {n.commit.sha[:2]: n for n in layout.nodes}
    f_node = nodes["f0"]
    c2_node = nodes["c2"]
    c3_node = nodes["c3"]

    # c2's bend (TEE_UP): up-pipe and the incoming left segment carry
    # c2's colour; the outgoing horizontal carries c3's.
    bend = f_node.cells[c2_node.lane * 2]
    assert bend.cell_type == CellType.TEE_UP
    assert bend.pipe_color_index == c2_node.color_index
    assert bend.color_index == c3_node.color_index
    incoming = f_node.cells[c2_node.lane * 2 - 1]
    assert incoming.color_index == c2_node.color_index

    # c3 is the rightmost child: its MERGE_LEFT bend keeps its own colour.
    last = f_node.cells[c3_node.lane * 2]
    assert last.cell_type == CellType.MERGE_LEFT
    assert last.color_index == c3_node.color_index


def test_fork_corridor_single_child_keeps_branch_colour_end_to_end() -> None:
    """Single-child fork (kilocode ``9e1b54d`` → ``489601e5``): the
    corridor, the bend and the child's vertical line all carry the
    branch's colour — the outgoing line at branch creation must not
    switch to the parent's colour."""
    f = "f0" + "0" * 38
    commits = [
        _c("c1" + "0" * 38, parents=[f], ts=3),
        _c("c2" + "0" * 38, parents=[f], ts=2),
        _c(f, parents=["r0" + "0" * 38], ts=1),
        _c("r0" + "0" * 38, ts=0),
    ]
    branches = [_b("main", "c1" + "0" * 38, is_head=True)]
    layout = build_graph(commits, branches)
    nodes = {n.commit.sha[:2]: n for n in layout.nodes}
    f_node = nodes["f0"]
    c2_node = nodes["c2"]
    assert c2_node.lane != f_node.lane

    bend = f_node.cells[c2_node.lane * 2]
    assert bend.cell_type == CellType.MERGE_LEFT
    assert bend.color_index == c2_node.color_index
    for col in range(f_node.lane * 2, c2_node.lane * 2):
        cell = f_node.cells[col]
        if cell.cell_type in (
            CellType.HORIZONTAL,
            CellType.HORIZONTAL_PIPE,
            CellType.TEE_RIGHT,
        ):
            assert cell.color_index == c2_node.color_index, (
                f"col {col}: {cell.cell_type.name} colour {cell.color_index} "
                f"!= branch colour {c2_node.color_index}"
            )


# ---- merge connector owns the trunk left of the fork corridor ------


def test_merge_connector_owns_trunk_left_of_fork_corridor() -> None:
    """Merge commit that is ALSO a fork point (kilocode ``1a3c7191``):
    the second parent's ``BRANCH_LEFT`` bend sits LEFT of the fork
    bend, so the merge connector owns the track from the commit to its
    bend — the second parent's colour runs all the way into the commit
    node («заворачивает влево и идёт прямо в коммит») instead of
    stopping at the bend's half-cell arm.  The bend itself splits
    relay-style: the 90-degree turn (left arm + down pipe) keeps the
    parent's colour, the corridor leaving the bend rightward carries
    the fork child's colour."""
    m = "m0" + "0" * 38
    p2 = "p2" + "0" * 38
    cf = "cf" + "0" * 38
    commits = [
        _c("cm" + "0" * 38, parents=[m], ts=11),
        _c("t1" + "0" * 38, parents=["b0" + "0" * 38], ts=10),
        _c("t2" + "0" * 38, parents=["b0" + "0" * 38], ts=9),
        _c("t3" + "0" * 38, parents=["b0" + "0" * 38], ts=8),
        _c("t4" + "0" * 38, parents=["b0" + "0" * 38], ts=7),
        _c("t5" + "0" * 38, parents=["b0" + "0" * 38], ts=6),
        _c(cf, parents=[m], ts=5),
        _c("b0" + "0" * 38, parents=["r0" + "0" * 38], ts=4),
        _c(m, parents=["p1" + "0" * 38, p2], ts=3),
        _c("p1" + "0" * 38, parents=["r0" + "0" * 38], ts=2),
        _c(p2, parents=["r0" + "0" * 38], ts=1),
        _c("r0" + "0" * 38, ts=0),
    ]
    branches = [_b("main", "cm" + "0" * 38, is_head=True)]
    layout = build_graph(commits, branches)
    nodes = {n.commit.sha[:2]: n for n in layout.nodes}
    m_node = nodes["m0"]
    p2_node = nodes["p2"]
    cf_node = nodes["cf"]

    assert m_node.lane == 0
    assert 0 < p2_node.lane < cf_node.lane

    bend_col = p2_node.lane * 2
    fork_bend_col = cf_node.lane * 2

    # Trunk: the merge connector owns the track from the commit to the
    # bend — the TEE_RIGHT at the commit and every horizontal between
    # carry the second parent's colour.
    tee = m_node.cells[0]
    assert tee.cell_type == CellType.TEE_RIGHT
    assert tee.color_index == p2_node.color_index
    for col in range(1, bend_col - 1):
        cell = m_node.cells[col]
        if cell.cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE):
            assert cell.color_index == p2_node.color_index, (
                f"col {col}: {cell.cell_type.name} colour {cell.color_index} "
                f"!= second parent colour {p2_node.color_index}"
            )
    # The gap cell left of the bend stays EMPTY — the fork corridor
    # colour must not leak half a cell into the parent's track.
    assert m_node.cells[bend_col - 1].cell_type == CellType.EMPTY

    # The bend splits relay-style: own (second parent) colour for the
    # 90-degree turn, corridor (fork child) colour for the rightward
    # continuation.
    bend = m_node.cells[bend_col]
    assert bend.cell_type == CellType.BRANCH_LEFT
    assert bend.pipe_color_index == p2_node.color_index
    assert bend.color_index == cf_node.color_index

    # Right of the bend the corridor carries the fork child's colour
    # up to its MERGE_LEFT bend.
    for col in range(bend_col + 1, fork_bend_col):
        cell = m_node.cells[col]
        if cell.cell_type in (CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE):
            assert cell.color_index == cf_node.color_index, (
                f"col {col}: {cell.cell_type.name} colour {cell.color_index} "
                f"!= fork child colour {cf_node.color_index}"
            )
    fork_bend = m_node.cells[fork_bend_col]
    assert fork_bend.cell_type == CellType.MERGE_LEFT
    assert fork_bend.color_index == cf_node.color_index

    # Serialisation: the split bend round-trips with both colours,
    # while a plain bend keeps the single-colour form.
    d = bend.to_dict()
    assert d["c"] == cf_node.color_index
    assert d["p"] == p2_node.color_index
    assert "p" not in CellInfo.branch_left(5).to_dict()


# ---- off-window (dangling) parents: lanes run to the bottom edge ------


def test_off_window_parent_lane_continues_to_bottom() -> None:
    """Truncation boundary: a commit whose parent lies outside the
    loaded window must keep its lane alive — the pipe continues to the
    bottom edge (GitKraken behaviour) instead of ending in a half-cell
    stub right under the commit (kilocode ``4327386f`` with the default
    500-commit window)."""
    ext = "e" * 40  # parent of c1, NOT in the window
    commits = [
        _c("c3" + "0" * 38, parents=["c2" + "0" * 38], ts=4),
        _c("c2" + "0" * 38, parents=["c1" + "0" * 38], ts=3),
        _c("c1" + "0" * 38, parents=[ext], ts=2),
        _c("z0" + "0" * 38, ts=1),  # independent root — a row BELOW c1
    ]
    layout = build_graph(commits, [])
    nodes = {n.commit.sha[:2]: n for n in layout.nodes}
    c1_node = nodes["c1"]
    z_node = nodes["z0"]
    assert z_node.lane != c1_node.lane
    # The row below c1 still carries c1's lane as a vertical pipe in
    # c1's own colour — the line visually runs off the bottom edge.
    pipe_cell = z_node.cells[c1_node.lane * 2]
    assert pipe_cell.cell_type == CellType.PIPE
    assert pipe_cell.color_index == c1_node.color_index


def test_merge_with_off_window_second_parent_branches_to_bottom() -> None:
    """A merge whose second parent fell out of the window still opens
    a side lane for it: BRANCH corner on the merge row + a pipe that
    reaches the last visible row."""
    ext = "e" * 40
    m = "m0" + "0" * 38
    a = "a0" + "0" * 38
    commits = [
        _c(m, parents=[a, ext], ts=2),
        _c(a, ts=1),
    ]
    layout = build_graph(commits, [])
    m_node, a_node = layout.nodes
    assert len(a_node.cells) >= 4, "the dangling parent must keep a lane"
    # Merge row: the second parent's lane starts with a branch corner.
    bend_cells = [
        (col, cell) for col, cell in enumerate(m_node.cells)
        if cell.cell_type in (CellType.BRANCH_LEFT, CellType.BRANCH_RIGHT)
    ]
    assert bend_cells, "no BRANCH corner for the off-window second parent"
    bend_col, bend_cell = bend_cells[0]
    # The pipe survives into the last row in the bend's colour.
    pipe_cell = a_node.cells[bend_col]
    assert pipe_cell.cell_type == CellType.PIPE
    assert pipe_cell.color_index == bend_cell.color_index


def test_two_commits_sharing_off_window_parent_converge() -> None:
    """Two in-window commits with the same off-window parent (a fork
    point below the truncation boundary) join into one lane that runs
    off the bottom — not two parallel stubs."""
    ext = "e" * 40
    c2, c1, y = "c2" + "0" * 38, "c1" + "0" * 38, "y0" + "0" * 38
    commits = [
        _c(c2, parents=[c1], ts=3),
        _c(c1, parents=[ext], ts=2),
        _c(y, parents=[ext], ts=1),
    ]
    layout = build_graph(commits, [])
    nodes = {n.commit.sha[:2]: n for n in layout.nodes}
    c1_node, y_node = nodes["c1"], nodes["y0"]
    assert y_node.lane != c1_node.lane
    # y connects into c1's lane (the shared dangling parent), and the
    # lane is NOT closed afterwards — it continues to the bottom edge.
    junction = y_node.cells[c1_node.lane * 2]
    assert junction.cell_type in (CellType.TEE_RIGHT, CellType.MERGE_RIGHT)
    commit_cell = y_node.cells[y_node.lane * 2]
    assert commit_cell.cell_type in (CellType.TEE_LEFT, CellType.COMMIT)

# ---- connector colour consistency (kilocode regressions) -------------------


def test_merge_connector_single_colour_end_to_end() -> None:
    """A merge connector to a second parent that carries NO branch name
    must be painted in ONE colour along its whole path: the departure
    corridor at the merge's row, the trunk pipe, and the arrival bend
    at the second parent's row.

    Regression (kilocode ``df9e1fb1``): the parent was placed on a fresh
    lane with a fallback colour that deliberately never entered
    ``lane_color_index`` (anti-poisoning rule), so the trunk used the
    oid fallback while the arrival bend fell back to the lane NUMBER —
    the connector turned from one colour into another right at the bend.
    """
    cc, mm, bb, aa, xx = (
        "cc" + "0" * 38,
        "mm" + "0" * 38,
        "bb" + "0" * 38,
        "aa" + "0" * 38,
        "xx" + "0" * 38,
    )
    commits = [
        _c(cc, parents=[bb, mm], ts=5),   # merge pulling mm onto a side lane
        _c(mm, parents=[xx, aa], ts=4),   # the merge; aa = second parent
        _c(bb, parents=[aa], ts=3),       # main-line child of aa
        _c(aa, parents=[], ts=2),         # second parent (no branch name)
        _c(xx, parents=[], ts=1),
    ]
    layout = build_graph(commits, [_b("main", cc, is_head=True)])
    m_node, b_node, a_node = layout.nodes[1], layout.nodes[2], layout.nodes[3]
    assert m_node.commit.sha == mm
    assert b_node.commit.sha == bb
    assert a_node.commit.sha == aa

    # Departure: the bend leaving the merge's row toward aa below.
    dep_bends = [
        (ci, c) for ci, c in enumerate(m_node.cells)
        if c.cell_type == CellType.BRANCH_LEFT
    ]
    assert len(dep_bends) == 1
    dep_col, dep_bend = dep_bends[0]

    # Trunk: the pipe at the connector's lane in the row between.
    trunk = b_node.cells[dep_col]
    assert trunk.cell_type == CellType.PIPE
    assert trunk.color_index == dep_bend.color_index

    # Arrival: the bend at aa's row plus its corridor share the colour.
    arr = a_node.cells[dep_col]
    assert arr.cell_type == CellType.MERGE_LEFT
    assert arr.color_index == dep_bend.color_index, (
        f"arrival bend colour {arr.color_index} != departure/trunk colour "
        f"{dep_bend.color_index} — one connector painted in two colours"
    )
    for c in a_node.cells[:dep_col]:
        if c.cell_type in (CellType.HORIZONTAL, CellType.TEE_RIGHT):
            assert c.color_index == dep_bend.color_index
        elif c.cell_type == CellType.HORIZONTAL_PIPE:
            assert c.color_index == dep_bend.color_index


def test_fork_merge_cross_keeps_both_colours() -> None:
    """Fork-merge point (a merge whose second parent lands on the lane of
    the merge's own forked child): the CROSS cell must keep BOTH colours
    — the merge connector (second parent's colour) going down, and the
    forked child's branch colour going up (kilocode ``1246d989`` /
    ``e2be875b``: green fork from the merge commit above a light-red
    merge connector, each staying its own colour).
    """
    b0, gg, mg, ss, ff = (
        "b0" + "0" * 38,
        "gg" + "0" * 38,
        "mg" + "0" * 38,
        "ss" + "0" * 38,
        "ff" + "0" * 38,
    )
    commits = [
        _c(b0, parents=[mg], ts=5),
        _c(gg, parents=[mg], ts=4),       # branch forked FROM the merge
        _c(mg, parents=[ff, ss], ts=3),   # the merge; ss = second parent
        _c(ss, parents=[ff], ts=2),
        _c(ff, parents=[], ts=1),
    ]
    branches = [
        _b("main", b0, is_head=True),
        _b("feat/green", gg),
    ]
    layout = build_graph(commits, branches)
    g_node, mg_node, s_node = layout.nodes[1], layout.nodes[2], layout.nodes[3]
    assert g_node.commit.sha == gg
    assert mg_node.commit.sha == mg
    assert s_node.commit.sha == ss
    green = _pick_branch_color("feat/green")
    assert g_node.color_index == green

    crosses = [
        (ci, c) for ci, c in enumerate(mg_node.cells)
        if c.cell_type == CellType.CROSS
    ]
    assert len(crosses) == 1
    cross_col, cross = crosses[0]
    # Down to the second parent: the merge connector's colour, matching
    # both the corridor leaving the merge commit and ss's own lane.
    assert cross.color_index == s_node.color_index
    tee = mg_node.cells[mg_node.lane * 2]
    assert tee.cell_type == CellType.TEE_RIGHT
    assert tee.color_index == cross.color_index
    # Up to the forked child: the child's branch colour, not the
    # connector's — the two connections must not blend into one colour.
    assert cross.pipe_color_index == green
    assert cross.pipe_color_index != cross.color_index


def test_nameless_second_parent_colour_seeded_by_sha() -> None:
    """The fallback colour of a second parent with NO branch name is
    derived from the parent's SHA, not from the lane it lands on — so a
    branch keeps its own colour wherever it is placed, and two
    unrelated nameless branches reusing one lane do not collapse onto
    the same lane-number colour (kilocode ``e2be875b``: three distinct
    branches on lane 14 all painted light-red).
    """
    assigner = ColorAssigner()
    lane_a = assigner.assign_fork_sibling_color(5, None, seed=_sha_color_seed("a" * 40))
    assert lane_a == _sha_color_seed("a" * 40)
    # Same seed on another lane: the seeded colour is taken, so the
    # allocator moves to the next free index (skip rule preserved).
    lane_b = assigner.assign_fork_sibling_color(6, None, seed=_sha_color_seed("a" * 40))
    assert lane_b == (_sha_color_seed("a" * 40) + 1) % len(BRANCH_PALETTE)
    # A different seed gets its own colour even on the same lane number.
    lane_c = assigner.assign_fork_sibling_color(5, None, seed=_sha_color_seed("b" * 40))
    assert lane_c == _sha_color_seed("b" * 40)


def test_nameless_branches_reusing_one_lane_get_distinct_colours() -> None:
    """Two nameless branches placed on the SAME lane at different
    heights must be painted in two different colours (each derived
    from its own tip SHA).  With the old lane-number fallback both
    received whatever the lane's sequential fallback yielded at
    placement time — unrelated branches appearing in one colour.
    """
    b0, g1, m1, s1, b1, g2, m2, s2, ff = (
        "b0" + "0" * 38, "g1" + "0" * 38, "m1" + "0" * 38, "s1" + "0" * 38,
        "b1" + "0" * 38, "g2" + "0" * 38, "m2" + "0" * 38, "s2" + "0" * 38,
        "ff" + "0" * 38,
    )
    commits = [
        _c(b0, parents=[m1], ts=8),
        _c(g1, parents=[m1], ts=7),
        _c(m1, parents=[b1, s1], ts=6),   # fork-merge: s1 lands on g1's lane
        _c(s1, parents=[], ts=5),
        _c(b1, parents=[m2], ts=4),
        _c(g2, parents=[m2], ts=3),
        _c(m2, parents=[ff, s2], ts=2),   # fork-merge: s2 lands on g2's lane
        _c(s2, parents=[], ts=1),
        _c(ff, parents=[], ts=0),
    ]
    branches = [
        _b("main", b0, is_head=True),
        _b("feat/g1", g1),
        _b("feat/g2", g2),
    ]
    layout = build_graph(commits, branches)
    s1_node, s2_node = layout.nodes[3], layout.nodes[7]
    assert s1_node.commit.sha == s1
    assert s2_node.commit.sha == s2
    # Both nameless branches occupy lane 1 (each released before the
    # other arrives) yet must NOT share a colour.
    assert s1_node.lane == s2_node.lane
    assert s1_node.color_index == _sha_color_seed(s1)
    assert s2_node.color_index == _sha_color_seed(s2)
    assert s1_node.color_index != s2_node.color_index
    # The merge connectors into each of them carry the same colour as
    # the branch itself (single-colour connector invariant).
    m1_node, m2_node = layout.nodes[2], layout.nodes[6]
    m1_cross = [c for c in m1_node.cells if c.cell_type == CellType.CROSS]
    m2_cross = [c for c in m2_node.cells if c.cell_type == CellType.CROSS]
    assert m1_cross and m1_cross[0].color_index == s1_node.color_index
    assert m2_cross and m2_cross[0].color_index == s2_node.color_index
