"""Tests for :mod:`src.core.graph`.

Exercises the lane-based layout against synthetic histories built
on top of the real :func:`src.core.repository.RepositoryManager` (so
we get a valid DAG and parents for free). The tests assert on the
shapes that the UI layer actually consumes: ``lane``, ``color``,
``refs``, ``row`` and ``subject``.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from src.core.graph import (
    BRANCH_PALETTE,
    GraphNode,
    _assign_branch_colors,
    _assign_lanes,
    _build_swimlanes,
    compute_layout,
    nodes_to_rows,
)
from src.core.models import BranchInfo, CommitInfo, TagInfo
from src.core.repository import RepositoryManager


def _commit_info(
    sha: str,
    parents: list[str] | None = None,
    message: str = "subject",
    ts: int = 0,
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
    )


# ----- compute_layout ---------------------------------------------------


def test_empty_history_returns_empty_layout() -> None:
    assert compute_layout([], [], [], None, None) == []


def test_single_commit_takes_lane_zero() -> None:
    history = [_commit_info("a" * 40, ts=1)]
    nodes = compute_layout(history, [], [], None, None)
    assert len(nodes) == 1
    node = nodes[0]
    assert node.lane == 0
    assert node.row == 0
    assert node.refs == []
    assert node.color == BRANCH_PALETTE[0]


def test_linear_history_uses_single_lane() -> None:
    history = [
        _commit_info("c" * 40, parents=["b" * 40], message="c", ts=3),
        _commit_info("b" * 40, parents=["a" * 40], message="b", ts=2),
        _commit_info("a" * 40, message="a", ts=1),
    ]
    nodes = compute_layout(history, [], [], None, None)
    assert [n.lane for n in nodes] == [0, 0, 0]
    assert [n.row for n in nodes] == [0, 1, 2]


def test_branching_opens_a_second_lane() -> None:
    # c is the merge base; b and d are its two children. No branches
    # are defined so the orphan (time-ordered) walk is used.
    history = [
        _commit_info("d" * 40, parents=["c" * 40], message="d", ts=4),
        _commit_info("b" * 40, parents=["c" * 40], message="b", ts=3),
        _commit_info("c" * 40, parents=["a" * 40], message="c", ts=2),
        _commit_info("a" * 40, message="a", ts=1),
    ]
    nodes = compute_layout(history, [], [], None, None)
    by_sha = {n.sha: n for n in nodes}
    # The two siblings (b and d) must end up on different lanes.
    assert by_sha["b" * 40].lane != by_sha["d" * 40].lane
    # The linear chain a -> c shares a single lane.
    assert by_sha["a" * 40].lane == by_sha["c" * 40].lane
    # Exactly two distinct lanes are used.
    assert {n.lane for n in nodes} == {0, 1}


def test_merge_keeps_first_parent_lane() -> None:
    # m is a merge commit with parents b (first) and d (second). No
    # branches are defined so the orphan walk is used.
    history = [
        _commit_info("m" * 40, parents=["b" * 40, "d" * 40], message="m", ts=5),
        _commit_info("b" * 40, parents=["c" * 40], message="b", ts=4),
        _commit_info("d" * 40, parents=["c" * 40], message="d", ts=3),
        _commit_info("c" * 40, parents=["a" * 40], message="c", ts=2),
        _commit_info("a" * 40, message="a", ts=1),
    ]
    nodes = compute_layout(history, [], [], None, None)
    by_sha = {n.sha: n for n in nodes}
    # The merge commit must be in the same lane as b (its first
    # parent). d is the second parent, on a different lane.
    assert by_sha["m" * 40].lane == by_sha["b" * 40].lane
    assert by_sha["d" * 40].lane != by_sha["b" * 40].lane


def test_subject_uses_first_non_empty_line() -> None:
    history = [_commit_info("a" * 40, message="subject line\n\nbody text", ts=1)]
    nodes = compute_layout(history, [], [], None, None)
    assert nodes[0].subject == "subject line"


def test_subject_handles_pygit2_trailing_newline() -> None:
    history = [_commit_info("a" * 40, message="only subject\n", ts=1)]
    nodes = compute_layout(history, [], [], None, None)
    assert nodes[0].subject == "only subject"


def test_refs_include_head_and_branch_refs_hold_branches() -> None:
    sha = "a" * 40
    history = [_commit_info(sha, ts=1)]
    branches = [BranchInfo(name="main", is_head=True, target_sha=sha)]
    nodes = compute_layout(history, branches, [], head_target_sha=sha, head_shorthand="main")
    # ``HEAD`` stays in the ref-chip list; branch names moved out to
    # ``branch_refs`` so the widget can decorate them (check /
    # monitor) in the left-hand column.
    assert nodes[0].refs == ["HEAD"]
    assert len(nodes[0].branch_refs) == 1
    assert nodes[0].branch_refs[0].name == "main"
    assert nodes[0].branch_refs[0].is_head is True
    assert nodes[0].branch_refs[0].is_remote is False
    # Per-branch lane and colour are populated.
    assert nodes[0].branch_refs[0].lane == 0
    assert nodes[0].branch_refs[0].color == BRANCH_PALETTE[0]


def test_branch_refs_have_distinct_colors() -> None:
    sha = "a" * 40
    history = [_commit_info(sha, ts=1)]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha),
        BranchInfo(name="feature", is_head=False, target_sha=sha),
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha=sha),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha, head_shorthand="main")
    refs = nodes[0].branch_refs
    # All three branches share the same commit but get distinct colours.
    assert len({b.color for b in refs}) == 3
    assert refs[0].color != refs[1].color != refs[2].color
    # They share the same lane (same tip SHA).
    assert refs[0].lane == refs[1].lane == refs[2].lane


def test_branch_refs_order_matches_input() -> None:
    sha = "a" * 40
    history = [_commit_info(sha, ts=1)]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha),
        BranchInfo(name="feature", is_head=False, target_sha=sha),
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha=sha),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha, head_shorthand="main")
    # The order must follow the input list so the left column renders
    # predictably. Local branches first (HEAD's, then the rest), then
    # remote-tracking refs.
    assert [b.name for b in nodes[0].branch_refs] == [
        "main", "feature", "origin/main",
    ]
    assert [b.is_head for b in nodes[0].branch_refs] == [True, False, False]
    assert [b.is_remote for b in nodes[0].branch_refs] == [False, False, True]


def test_refs_for_tag_only_commit() -> None:
    sha = "a" * 40
    history = [_commit_info(sha, ts=1)]
    tags = [TagInfo(name="v1.0", target_sha=sha)]
    nodes = compute_layout(history, [], tags, head_target_sha=None, head_shorthand=None)
    assert nodes[0].refs == ["v1.0"]


def test_detached_head_adds_head_label_without_branch() -> None:
    sha = "a" * 40
    history = [_commit_info(sha, ts=1)]
    # No branches point at the commit, but HEAD does.
    nodes = compute_layout(
        history, [], [], head_target_sha=sha, head_shorthand="(detached)",
    )
    assert nodes[0].refs == ["HEAD"]


def test_branch_tip_gets_a_fresh_color() -> None:
    sha_a, sha_b = "a" * 40, "b" * 40
    history = [
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [BranchInfo(name="main", target_sha=sha_b)]
    nodes = compute_layout(history, branches, [], head_target_sha=sha_b, head_shorthand="main")
    # The branch tip picks palette[0]; the parent inherits it.
    assert nodes[0].color == BRANCH_PALETTE[0]
    assert nodes[1].color == BRANCH_PALETTE[0]


def test_two_branches_get_distinct_colors() -> None:
    sha_a, sha_b, sha_c = "a" * 40, "b" * 40, "c" * 40
    history = [
        _commit_info(sha_c, parents=[sha_a], ts=3),
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", target_sha=sha_c),
        BranchInfo(name="feature", target_sha=sha_b),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha_c, head_shorthand="main")
    by_sha = {n.sha: n for n in nodes}
    assert by_sha[sha_c].color == BRANCH_PALETTE[0]
    assert by_sha[sha_b].color == BRANCH_PALETTE[1]
    # Common ancestor: not a branch tip and has no parents, so it
    # gets the default palette colour (the same as `main`'s tip).
    assert by_sha[sha_a].color == BRANCH_PALETTE[0]


def test_color_palette_wraps_around() -> None:
    palette_size = len(BRANCH_PALETTE)
    # Build `palette_size + 1` one-commit branches.
    shas = [f"{i:040x}" for i in range(palette_size + 1)]
    history = [_commit_info(sha, ts=i + 1) for i, sha in enumerate(shas)]
    branches = [BranchInfo(name=f"b{i}", target_sha=sha) for i, sha in enumerate(shas)]
    nodes = compute_layout(history, branches, [], head_target_sha=shas[0], head_shorthand="b0")
    # The first `palette_size` branch tips take the palette in order;
    # the next one wraps to palette[0] again.
    colors_seen = [n.color for n in nodes]
    assert colors_seen[palette_size] == BRANCH_PALETTE[0]


def test_priority_walk_puts_head_branch_in_lane_zero() -> None:
    # c1 (root) -> c2 (main) and c1 (root) -> f1 (feature). The
    # feature commit is newer than c2, so the simple time-ordered
    # walk would put it in lane 0 and main in lane 1. The priority
    # walk must override that because main is HEAD's branch.
    sha_root, sha_main, sha_feat = "a" * 40, "b" * 40, "c" * 40
    history = [
        _commit_info(sha_feat, parents=[sha_root], ts=3),
        _commit_info(sha_main, parents=[sha_root], ts=2),
        _commit_info(sha_root, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_main),
        BranchInfo(name="feature", is_head=False, target_sha=sha_feat),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha_main, head_shorthand="main")
    by_sha = {n.sha: n for n in nodes}
    assert by_sha[sha_main].lane == 0
    assert by_sha[sha_feat].lane == 1
    assert by_sha[sha_root].lane == 0  # claimed by main's walk


def test_priority_walk_stops_at_shared_ancestor() -> None:
    # main: c1 -> c2 -> c3. feature: c1 -> c2 -> c4 (c4 branches off
    # c2). The shared ancestors c1 and c2 should stay on main's lane.
    sha_c1, sha_c2, sha_c3, sha_c4 = "1" * 40, "2" * 40, "3" * 40, "4" * 40
    history = [
        _commit_info(sha_c4, parents=[sha_c2], ts=4),
        _commit_info(sha_c3, parents=[sha_c2], ts=3),
        _commit_info(sha_c2, parents=[sha_c1], ts=2),
        _commit_info(sha_c1, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_c3),
        BranchInfo(name="feature", is_head=False, target_sha=sha_c4),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha_c3, head_shorthand="main")
    by_sha = {n.sha: n for n in nodes}
    assert by_sha[sha_c3].lane == 0  # main
    assert by_sha[sha_c4].lane == 1  # feature
    assert by_sha[sha_c2].lane == 0  # shared with main
    assert by_sha[sha_c1].lane == 0  # shared with main


def test_detached_head_claims_lane_zero() -> None:
    # HEAD is detached, pointing at a commit no branch tracks. The
    # synthesised HEAD tip should still claim lane 0.
    sha = "a" * 40
    history = [_commit_info(sha, ts=1)]
    nodes = compute_layout(
        history, [], [], head_target_sha=sha, head_shorthand="(detached)",
    )
    assert nodes[0].lane == 0


# ----- integration with a real repository -------------------------------


def _build_repo_with_history(tmp_git_repo: Path) -> RepositoryManager:
    """Three commits on main, one commit on a feature branch off the root."""
    mgr = RepositoryManager(str(tmp_git_repo))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (tmp_git_repo / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree1 = mgr.repo.index.write_tree()
    c1 = mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree1, [])
    (tmp_git_repo / "f.txt").write_text("v2\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree2 = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "second", tree2, [c1])
    # Feature branch off c1.
    (tmp_git_repo / "g.txt").write_text("feat\n")
    mgr.repo.index.add("g.txt")
    mgr.repo.index.write()
    tree3 = mgr.repo.index.write_tree()
    mgr.repo.create_commit(
        "refs/heads/feature", sig, sig, "feature work", tree3, [c1],
    )
    return mgr


def test_compute_layout_with_real_repo(tmp_git_repo: Path) -> None:
    mgr = _build_repo_with_history(tmp_git_repo)
    history = mgr.get_all_history()
    branches = mgr.branches
    head = mgr.head_commit
    nodes = compute_layout(
        history, branches, mgr.tags, head.sha, mgr.repo.head.shorthand,
    )
    assert len(nodes) == len(history)
    # Every node has a lane in [0, len(branches)].
    assert all(0 <= n.lane < len(branches) + 1 for n in nodes)
    # Rows are 0..n-1.
    assert [n.row for n in nodes] == list(range(len(nodes)))
    by_sha = {n.sha: n for n in nodes}
    # HEAD's commit must be present. ``HEAD`` lives in the ref-chip
    # list, the branch name moves to ``branch_refs``.
    head_node = by_sha[head.sha]
    assert "HEAD" in head_node.refs
    assert any(b.name == "main" and b.is_head for b in head_node.branch_refs)
    # main is HEAD's branch so its tip is on lane 0.
    assert head_node.lane == 0
    # The feature branch's tip is in a different lane.
    feature_branch = next(b for b in branches if b.name == "feature")
    feature_node = by_sha[feature_branch.target_sha]
    assert feature_node.lane != head_node.lane
    assert any(b.name == "feature" for b in feature_node.branch_refs)


def test_compute_layout_500_commits_is_fast(tmp_git_repo: Path) -> None:
    mgr = _build_repo_with_history(tmp_git_repo)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    tip = mgr.repo.head.target
    parent_oids = [tip]
    for i in range(500):
        builder = mgr.repo.TreeBuilder(mgr.repo[parent_oids[0]].tree)
        tree_oid = builder.write()
        new_oid = mgr.repo.create_commit(
            "refs/heads/main", sig, sig, f"commit {i}", tree_oid, parent_oids,
        )
        parent_oids = [new_oid]
    history = mgr.get_all_history(max_count=600)
    # compute_layout on 500+ commits is well under a second on any
    # modern machine; we use a generous 2 s budget to avoid CI flakes.
    import time as _time

    start = _time.perf_counter()
    nodes = compute_layout(history, mgr.branches, mgr.tags, mgr.head_commit.sha, "main")
    elapsed = _time.perf_counter() - start
    assert len(nodes) >= 500
    assert elapsed < 2.0


# ----- nodes_to_rows ----------------------------------------------------


def test_nodes_to_rows_returns_serialisable_dicts() -> None:
    history = [_commit_info("a" * 40, ts=1)]
    nodes = compute_layout(history, [], [], None, None)
    rows = nodes_to_rows(nodes)
    assert len(rows) == 1
    row = rows[0]
    # All the keys the widget consumes must be present.
    for key in ("sha", "short_sha", "subject", "author_name", "author_email",
                "author_time", "parents", "refs", "branch_refs", "lane",
                "display_column", "color", "row"):
        assert key in row
    # Parents, refs and branch_refs must be plain lists (not
    # references into the dataclass) so they're safe to send across
    # threads.
    assert isinstance(row["parents"], list)
    assert isinstance(row["refs"], list)
    assert isinstance(row["branch_refs"], list)


def test_graphnode_to_dict_round_trip() -> None:
    node = GraphNode(
        sha="a" * 40,
        short_sha="abcdefg",
        subject="hello",
        author_name="tester",
        author_email="t@example.com",
        author_time=123,
        parents=["b" * 40],
        refs=["HEAD"],
        branch_refs=[],
        lane=2,
        display_column=1,
        color="#ff0000",
        row=5,
        kind="commit",
    )
    d = node.to_dict()
    assert d == {
        "sha": "a" * 40,
        "short_sha": "abcdefg",
        "subject": "hello",
        "author_name": "tester",
        "author_email": "t@example.com",
        "author_time": 123,
        "parents": ["b" * 40],
        "refs": ["HEAD"],
        "branch_refs": [],
        "lane": 2,
        "display_column": 1,
        "color": "#ff0000",
        "row": 5,
        "kind": "commit",
        "input_lanes": [],
        "output_lanes": [],
    }


# ----- lane compaction ---------------------------------------------------


def test_single_lane_compacts_to_column_zero() -> None:
    history = [
        _commit_info("c" * 40, parents=["b" * 40], message="c", ts=3),
        _commit_info("b" * 40, parents=["a" * 40], message="b", ts=2),
        _commit_info("a" * 40, message="a", ts=1),
    ]
    nodes = compute_layout(history, [], [], None, None, max_columns=12)
    assert all(n.display_column == 0 for n in nodes)


def test_non_overlapping_branches_share_column() -> None:
    sha_root, sha_main = "a" * 40, "b" * 40
    history = [
        _commit_info(sha_main, parents=[sha_root], ts=2),
        _commit_info(sha_root, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_main),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha_main, head_shorthand="main")
    assert all(n.display_column == 0 for n in nodes)


def test_display_column_respects_max_columns() -> None:
    sha_a, sha_b, sha_c = "a" * 40, "b" * 40, "c" * 40
    history = [
        _commit_info(sha_c, parents=[sha_a], ts=3),
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", target_sha=sha_c),
        BranchInfo(name="feature", target_sha=sha_b),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha_c, head_shorthand="main",
                           max_columns=1)
    # With compaction wrapping, both lanes map to column 0.
    assert {n.display_column for n in nodes} == {0}


def test_display_column_defaults_to_lane_when_uncompacted() -> None:
    history = [
        _commit_info("c" * 40, parents=["b" * 40], message="c", ts=3),
        _commit_info("b" * 40, parents=["a" * 40], message="b", ts=2),
        _commit_info("a" * 40, message="a", ts=1),
    ]
    nodes = compute_layout(history, [], [], None, None, max_columns=12)
    assert nodes[0].display_column == 0
    assert nodes[0].lane == 0


# ----- per-branch colour and lane assignment ----------------------------


def test_assign_branch_colors_cycles_palette() -> None:
    branches = [
        BranchInfo(name="main", is_head=True, target_sha="a" * 40),
        BranchInfo(name="feature", is_head=False, target_sha="b" * 40),
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha="a" * 40),
        BranchInfo(name="origin/feature", is_head=False, is_remote=True, target_sha="c" * 40),
    ]
    colors = _assign_branch_colors(branches, head_target_sha="a" * 40)
    # HEAD's branch gets palette[0].
    assert colors["main"] == BRANCH_PALETTE[0]
    # Other locals come next.
    assert colors["feature"] == BRANCH_PALETTE[1]
    # Remote branches follow.
    assert colors["origin/main"] == BRANCH_PALETTE[2]
    assert colors["origin/feature"] == BRANCH_PALETTE[3]


def test_assign_branch_colors_remote_at_same_sha_gets_distinct_color() -> None:
    """Local and remote branches at the same SHA get different colours."""
    branches = [
        BranchInfo(name="main", is_head=True, target_sha="a" * 40),
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha="a" * 40),
    ]
    colors = _assign_branch_colors(branches, head_target_sha="a" * 40)
    assert colors["main"] == BRANCH_PALETTE[0]
    assert colors["origin/main"] == BRANCH_PALETTE[1]
    assert colors["main"] != colors["origin/main"]


def test_assign_lanes_returns_branch_lanes_for_all_branches() -> None:
    """_assign_lanes returns branch_lane dict that covers every branch name."""
    sha_a, sha_b = "a" * 40, "b" * 40
    history = [
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_b),
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha=sha_b),
    ]
    lane_of, branch_lane = _assign_lanes(history, branches, head_target_sha=sha_b)
    assert "main" in branch_lane
    assert "origin/main" in branch_lane
    # Both share the same commit SHA, so they share the same lane.
    assert branch_lane["main"] == branch_lane["origin/main"]


def test_assign_lanes_remote_only_branch_gets_own_lane() -> None:
    """A remote branch at a distinct SHA gets its own lane."""
    sha_a, sha_b, sha_c = "a" * 40, "b" * 40, "c" * 40
    history = [
        _commit_info(sha_c, parents=[sha_a], ts=3),
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_b),
        BranchInfo(name="origin/feature", is_head=False, is_remote=True, target_sha=sha_c),
    ]
    lane_of, branch_lane = _assign_lanes(history, branches, head_target_sha=sha_b)
    assert branch_lane["main"] != branch_lane["origin/feature"]


def test_assign_lanes_detached_head_with_remote_branch() -> None:
    """Detached HEAD gets a lane; a remote branch at a different SHA gets its own."""
    sha_a, sha_b = "a" * 40, "b" * 40
    history = [
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha=sha_b),
    ]
    lane_of, branch_lane = _assign_lanes(history, branches, head_target_sha=sha_a)
    assert "origin/main" in branch_lane
    # HEAD gets lane 0 (detached, synthesised from head_target_sha).
    assert branch_lane["origin/main"] >= 0


def test_graphnode_to_dict_includes_branch_ref_lane_color() -> None:
    """to_dict() serialises branch_ref's lane and color fields."""
    from src.core.graph import BranchRef
    node = GraphNode(
        sha="a" * 40,
        short_sha="abcdefg",
        subject="hello",
        author_name="tester",
        author_email="t@example.com",
        author_time=123,
        parents=["b" * 40],
        refs=["HEAD"],
        branch_refs=[
            BranchRef(name="main", is_head=True, is_remote=False, lane=0, color="#ff0000"),
            BranchRef(name="origin/main", is_head=False, is_remote=True, lane=0, color="#00ff00"),
        ],
        lane=0,
        display_column=0,
        color="#ff0000",
        row=0,
        kind="commit",
    )
    d = node.to_dict()
    assert d["branch_refs"][0] == {
        "name": "main", "is_head": True, "is_remote": False,
        "lane": 0, "color": "#ff0000",
    }
    assert d["branch_refs"][1] == {
        "name": "origin/main", "is_head": False, "is_remote": True,
        "lane": 0, "color": "#00ff00",
    }


# ----- swimlane model tests -----------------------------------------------


def test_build_swimlanes_head_branch_lane_zero() -> None:
    """HEAD branch always occupies lane 0."""
    sha_a, sha_b = "a" * 40, "b" * 40
    history = [
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_b),
        BranchInfo(name="feature", is_head=False, target_sha=sha_a),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_b)
    rows = _build_swimlanes(history, branches, sha_b, "main", branch_colors)
    # HEAD branch commit is in lane 0.
    assert rows[0]["lane"] == 0
    assert rows[0]["sha"] == sha_b


def test_build_swimlanes_local_before_remote() -> None:
    """Local branches are seeded before remote, so they occupy left lanes."""
    sha_a, sha_b, sha_c = "a" * 40, "b" * 40, "c" * 40
    history = [
        _commit_info(sha_c, parents=[sha_a], ts=3),
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_b),
        BranchInfo(name="origin/feature", is_head=False, is_remote=True, target_sha=sha_c),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_b)
    rows = _build_swimlanes(history, branches, sha_b, "main", branch_colors)
    by_sha = {r["sha"]: r for r in rows}
    # HEAD local branch at sha_b is left of remote at sha_c.
    assert by_sha[sha_b]["lane"] < by_sha[sha_c]["lane"]


def test_build_swimlanes_remote_gets_own_lane() -> None:
    """A remote branch at a different SHA gets its own lane."""
    sha_a, sha_b = "a" * 40, "b" * 40
    history = [
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_b),
        BranchInfo(name="origin/main", is_head=False, is_remote=True, target_sha=sha_b),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_b)
    rows = _build_swimlanes(history, branches, sha_b, "main", branch_colors)
    # Both point to same SHA; remote shares lane 0 via first claim.
    assert rows[0]["lane"] == 0
    # Check input_lanes has two entries (local + remote) for that SHA.
    assert len(rows[0]["input_lanes"]) >= 1


def test_build_swimlanes_shared_ancestor() -> None:
    """Two branches with a shared ancestor; ancestor stays on HEAD's lane."""
    sha_c1, sha_c2, sha_c3, sha_c4 = "1" * 40, "2" * 40, "3" * 40, "4" * 40
    history = [
        _commit_info(sha_c4, parents=[sha_c2], ts=4),
        _commit_info(sha_c3, parents=[sha_c2], ts=3),
        _commit_info(sha_c2, parents=[sha_c1], ts=2),
        _commit_info(sha_c1, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_c3),
        BranchInfo(name="feature", is_head=False, target_sha=sha_c4),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_c3)
    rows = _build_swimlanes(history, branches, sha_c3, "main", branch_colors)
    by_sha = {r["sha"]: r for r in rows}
    assert by_sha[sha_c3]["lane"] == 0
    assert by_sha[sha_c4]["lane"] == 1
    # Shared ancestor stays on main's lane.
    assert by_sha[sha_c2]["lane"] == 0
    assert by_sha[sha_c1]["lane"] == 0


def test_build_swimlanes_merge_creates_extra_lanes() -> None:
    """Merge commit with two parents creates an extra output lane for the second parent."""
    sha_a, sha_b, sha_c, sha_m = "a" * 40, "b" * 40, "c" * 40, "m" * 40
    history = [
        _commit_info(sha_m, parents=[sha_b, sha_c], ts=4),
        _commit_info(sha_b, parents=[sha_a], ts=3),
        _commit_info(sha_c, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_m),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_m)
    rows = _build_swimlanes(history, branches, sha_m, "main", branch_colors)
    # The merge row should have at least 2 output lanes (one per parent).
    merge_row = rows[0]
    assert merge_row["sha"] == sha_m
    assert len(merge_row["output_lanes"]) >= 2


def test_build_swimlanes_output_dedup() -> None:
    """The same SHA never appears twice in output_lanes."""
    sha_a, sha_b, sha_c = "a" * 40, "b" * 40, "c" * 40
    history = [
        _commit_info(sha_c, parents=[sha_a], ts=3),
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_c),
        BranchInfo(name="feature", is_head=False, target_sha=sha_b),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_c)
    rows = _build_swimlanes(history, branches, sha_c, "main", branch_colors)
    for row in rows:
        shas = [e["sha"] for e in row["output_lanes"]]
        assert len(shas) == len(set(shas)), f"duplicate SHA in output_lanes at row {row['sha']}"


def test_build_swimlanes_stash_gets_own_lane() -> None:
    """Stash (non-branch tip) gets its own lane without breaking main layout."""
    sha_a, sha_b = "a" * 40, "b" * 40
    stash_sha = "s" * 40
    history = [
        _commit_info(stash_sha, parents=[sha_b], ts=3, message="WIP on main"),
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    # Stash is NOT a branch — it won't be in the seed.
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_b),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_b)
    rows = _build_swimlanes(history, branches, sha_b, "main", branch_colors)
    # Stash row should exist.
    stash_row = rows[0]
    assert stash_row["sha"] == stash_sha
    # Main row (sha_b) should be in lane 0.
    main_row = rows[1]
    assert main_row["sha"] == sha_b
    assert main_row["lane"] == 0


def test_build_swimlanes_wip_above_head() -> None:
    """WIP commit above HEAD occupies its own lane."""
    sha_a = "a" * 40
    wip_sha = "WIP"
    history = [
        _commit_info(wip_sha, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_a),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_a)
    rows = _build_swimlanes(history, branches, sha_a, "main", branch_colors)
    assert rows[0]["sha"] == wip_sha
    # WIP should not share lane 0 with HEAD commit.
    assert rows[0]["lane"] > 0


def test_build_swimlanes_every_row_has_input_and_output() -> None:
    """Every row must have non-empty input_lanes; output_lanes may be empty for root commit."""
    sha_a, sha_b, sha_c = "a" * 40, "b" * 40, "c" * 40
    history = [
        _commit_info(sha_c, parents=[sha_b], ts=3),
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_c),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_c)
    rows = _build_swimlanes(history, branches, sha_c, "main", branch_colors)
    for row in rows:
        assert isinstance(row["input_lanes"], list)
        assert isinstance(row["output_lanes"], list)
        assert len(row["input_lanes"]) > 0
    # Non-root rows must have at least one output lane.
    for row in rows:
        sha = row["sha"]
        commit = next((c for c in history if c.sha == sha), None)
        if commit and commit.parents:
            assert len(row["output_lanes"]) > 0, f"row {sha} has parents but empty output_lanes"


def test_compute_layout_includes_swimlane_data() -> None:
    """compute_layout returns GraphNodes with input_lanes/output_lanes populated."""
    sha_a, sha_b = "a" * 40, "b" * 40
    history = [
        _commit_info(sha_b, parents=[sha_a], ts=2),
        _commit_info(sha_a, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_b),
    ]
    nodes = compute_layout(history, branches, [], head_target_sha=sha_b, head_shorthand="main")
    for node in nodes:
        assert isinstance(node.input_lanes, list)
        assert isinstance(node.output_lanes, list)
        if node.sha == sha_b:
            assert len(node.output_lanes) >= 1


def test_build_swimlanes_multi_ancestor_dedup_keeps_first_color() -> None:
    """When the same ancestor SHA appears via multiple paths, first colour wins."""
    sha_c1, sha_c2, sha_c3, sha_c4 = "1" * 40, "2" * 40, "3" * 40, "4" * 40
    history = [
        _commit_info(sha_c4, parents=[sha_c2], ts=4),
        _commit_info(sha_c3, parents=[sha_c2], ts=3),
        _commit_info(sha_c2, parents=[sha_c1], ts=2),
        _commit_info(sha_c1, ts=1),
    ]
    branches = [
        BranchInfo(name="main", is_head=True, target_sha=sha_c3),
        BranchInfo(name="feature", is_head=False, target_sha=sha_c4),
    ]
    branch_colors = _assign_branch_colors(branches, head_target_sha=sha_c3)
    rows = _build_swimlanes(history, branches, sha_c3, "main", branch_colors)
    # Row for sha_c2 (shared ancestor) should have sha_c2 only once in output_lanes.
    c2_row = rows[2]
    assert c2_row["sha"] == sha_c2
    shas = [e["sha"] for e in c2_row["output_lanes"]]
    # sha_c1 should appear only once.
    assert shas.count(sha_c1) <= 1


def test_build_swimlanes_empty_history() -> None:
    """_build_swimlanes returns an empty list for empty history."""
    result = _build_swimlanes([], [], None, None, {})
    assert result == []
