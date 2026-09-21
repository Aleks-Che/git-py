"""WIP above a merged branch must not overwrite a neighbouring merge lane."""

from dataclasses import replace

import pytest
from src.core import graph_v2
from src.core.graph_v2 import UNCOMMITTED_COLOR_INDEX, CellInfo, CellType, build_graph
from src.core.models import BranchInfo, CommitInfo


def crossing_history(*, detached=False, marker_kind=None):
    """Minimal topology of math-portal's 65a1b4 worktree (2026-09-21)."""
    def commit(sha, *parents, kind="commit"):
        return CommitInfo(
            sha=sha, short_sha=sha, message=sha, parents=list(parents),
            author_name="tester", author_email="test@example.com", author_time=0,
            committer_name="tester", committer_email="test@example.com", committer_time=0,
            kind=kind,
        )

    commits = [
        commit("merge-head", "merge-side", "head"),
        commit("merge-side", "trunk", "side"),
        commit("head", "head-parent"),
        commit("head-parent", "root"),
        commit("side", "root"),
        commit("trunk", "root"),
        commit("root"),
    ]
    if marker_kind:
        commits.insert(2, commit("marker", "side", kind=marker_kind))
    branches = [
        BranchInfo("dev", target_sha="merge-head"),
        BranchInfo("feature", target_sha="head", is_head=not detached),
        BranchInfo("main", target_sha="side"),
    ]
    return commits, branches


@pytest.mark.parametrize("detached", [False, True])
@pytest.mark.parametrize("marker_kind", [None, "stash", "wip"])
def test_wip_continues_head_lane_through_merge_crossing(detached, marker_kind):
    commits, branches = crossing_history(detached=detached, marker_kind=marker_kind)
    clean = build_graph(commits, branches, head_commit_sha="head")
    dirty = build_graph(commits, branches, uncommitted_count=12, head_commit_sha="head")
    before = {n.commit.sha: n for n in clean.nodes if n.commit}
    after = {n.commit.sha: n for n in dirty.nodes if n.commit}
    lane = before["head"].lane
    col = lane * 2
    assert before["merge-side"].cells[col].cell_type == CellType.HORIZONTAL_PIPE
    assert before["head"].cells[col + 2].cell_type == CellType.PIPE

    assert dirty.nodes[0].is_uncommitted
    assert dirty.nodes[0].uncommitted_count == 12
    assert dirty.nodes[0].lane == lane == after["head"].lane
    assert dirty.max_lane == clean.max_lane
    for sha, original in before.items():
        current = after[sha]
        assert current.lane == original.lane
        assert current.color_index == original.color_index
        assert current.commit.parents == original.commit.parents
        expected_cells = original.cells + [CellInfo.empty()] * (
            len(current.cells) - len(original.cells)
        )
        if sha == "merge-head":
            # Extend the down-bend upwards without losing its left arm or colour.
            assert current.cells[col].cell_type == CellType.TEE_LEFT
            assert current.cells[col].color_index == original.cells[col].color_index
            assert current.cells[col].pipe_color_index == original.cells[col].color_index
            assert current.cells[:col] == expected_cells[:col]
            assert current.cells[col + 1:] == expected_cells[col + 1:]
        else:
            # Includes the other merge's crossing AND its pipe beside HEAD.
            assert current.cells == expected_cells, sha


def test_offset_fallback_checks_later_rows_and_keeps_crossed_pipe(monkeypatch):
    commits, branches = crossing_history()
    clean = build_graph(commits, branches)
    # Exercise the fallback independently: the old compatibility check sent
    # this exact topology here. Short first row, occupied lane in later rows.
    monkeypatch.setattr(graph_v2, "_is_wip_compatible", lambda *args: False)
    dirty = build_graph(commits, branches, uncommitted_count=1)
    wip = dirty.nodes[0]
    head_idx = next(i for i, n in enumerate(clean.nodes) if n.is_head)
    head = dirty.nodes[head_idx + 1]
    assert wip.lane == 3  # lane 2 belongs to the other merge, not to WIP
    assert all(
        wip.lane * 2 >= len(n.cells)
        or n.cells[wip.lane * 2].cell_type == CellType.EMPTY
        for n in clean.nodes[:head_idx + 1]
    )
    original_pipe = clean.nodes[head_idx].cells[4]
    assert original_pipe.cell_type == CellType.PIPE
    assert head.cells[4] == CellInfo.horizontal_pipe(
        UNCOMMITTED_COLOR_INDEX, original_pipe.color_index,
    )
    assert head.cells[head.lane * 2] == CellInfo(
        CellType.TEE_RIGHT, UNCOMMITTED_COLOR_INDEX, head.color_index,
    )
    assert head.cells[wip.lane * 2] == CellInfo.merge_left(UNCOMMITTED_COLOR_INDEX)


def test_true_obstruction_still_uses_free_lane_without_erasing_branches():
    # An octopus merge crosses a released lane which is later reused by HEAD.
    # Unlike HORIZONTAL_PIPE, this HORIZONTAL has no vertical to extend.
    parents = [
        [1, 6], [6, 3], [11], [9], [7], [7, 11, 10],
        [9, 10, 7], [11, 9, 10], [9], [11, 10], [11], [],
    ]
    prototype = crossing_history()[0][0]
    commits = [replace(prototype, sha=str(i), parents=list(map(str, p)))
               for i, p in enumerate(parents)]
    branches = [BranchInfo("dev", target_sha="0"),
                BranchInfo("feature", target_sha="8", is_head=True)]
    clean = build_graph(commits, branches)
    dirty = build_graph(commits, branches, uncommitted_count=1)
    head_idx = next(i for i, n in enumerate(clean.nodes) if n.is_head)
    head = clean.nodes[head_idx]
    assert head.lane == 1
    assert clean.nodes[head_idx - 1].cells[head.lane * 2].cell_type == CellType.HORIZONTAL
    assert dirty.nodes[0].lane == clean.max_lane + 1 == 7
    for col, cell in enumerate(head.cells):
        if cell.cell_type == CellType.PIPE and col > head.lane * 2:
            assert dirty.nodes[head_idx + 1].cells[col] == CellInfo.horizontal_pipe(
                UNCOMMITTED_COLOR_INDEX, cell.color_index,
            )


def test_compound_relay_bend_is_not_replaced_with_simple_tee():
    # A relay has a third arm to the right, in a different colour. Unlike a
    # plain merge bend, TEE_LEFT cannot preserve all of this geometry.
    relay = CellInfo(CellType.BRANCH_LEFT, color_index=7, pipe_color_index=0)
    nodes = [graph_v2.GraphNode(cells=[relay]), graph_v2.GraphNode()]
    assert not graph_v2._is_wip_compatible(nodes, 1, 0)
    nodes[0].cells[0] = CellInfo.branch_left(7)
    assert graph_v2._is_wip_compatible(nodes, 1, 0)
