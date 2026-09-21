"""Branch labels must not recolour an established line of commits."""

import pytest
from src.core.graph_v2 import UNCOMMITTED_COLOR_INDEX, CellType, _pick_branch_color, build_graph
from src.core.models import BranchInfo, CommitInfo


def _commit(sha: str, *parents: str, kind: str = "commit") -> CommitInfo:
    return CommitInfo(
        sha=sha, short_sha=sha, message=sha,
        author_name="tester", author_email="test@example.com", author_time=0,
        committer_name="tester", committer_email="test@example.com", committer_time=0,
        parents=list(parents), kind=kind,
    )


@pytest.mark.parametrize("branch_name", ["dev", "integration"])
@pytest.mark.parametrize("marker_kind", [None, "stash", "wip"])
@pytest.mark.parametrize("reverse_refs", [False, True])
@pytest.mark.parametrize("remote_tip", ["new", "previous"])
def test_ancestor_branch_label_preserves_continuing_history(
    branch_name, marker_kind, reverse_refs, remote_tip,
):
    # agents-ide: dev advances, while redesign and stashes stay at its old tip.
    commits = [
        _commit("new", "previous"), _commit("previous", "base"),
        _commit("base", "root"), _commit("root"),
    ]
    if marker_kind:
        commits.insert(1, _commit("marker", "base", kind=marker_kind))
    branches = [
        BranchInfo(branch_name, target_sha="new"),
        BranchInfo("redesign", target_sha="base"),
        BranchInfo(f"origin/{branch_name}", target_sha=remote_tip, is_remote=True),
    ]
    if reverse_refs:
        branches.reverse()

    # Checkout and WIP must not decide which branch owns shared history.
    for head_name in (branch_name, "redesign"):
        for branch in branches:
            branch.is_head = branch.name == head_name
        for wip_count in (None, 2):
            layout = build_graph(commits, branches, uncommitted_count=wip_count)
            nodes = {n.commit.sha: n for n in layout.nodes if n.commit}
            for sha in ("new", "previous", "base", "root"):
                node = nodes[sha]
                assert node.color_index == _pick_branch_color(branch_name), sha
                cell = node.cells[node.lane * 2]
                color = (
                    cell.color_index if cell.cell_type == CellType.COMMIT
                    else cell.pipe_color_index
                )
                assert color == node.color_index
            assert nodes["base"].branch_names == ["redesign"]
            if marker_kind == "wip":
                assert nodes["marker"].color_index == UNCOMMITTED_COLOR_INDEX


@pytest.mark.parametrize("head_name", ["dev", "redesign"])
def test_new_branch_at_same_tip_does_not_recolor_dev(head_name):
    branches = [BranchInfo(name, target_sha="base", is_head=name == head_name)
                for name in ("redesign", "dev")]
    layout = build_graph([_commit("base", "root"), _commit("root")], branches)
    assert all(node.color_index == _pick_branch_color("dev") for node in layout.nodes)
    assert layout.nodes[0].branch_names == ["redesign", "dev"]


@pytest.mark.parametrize("reverse_commits", [False, True])
def test_diverged_branch_keeps_own_commits_and_dev_keeps_shared_history(reverse_commits):
    tips = [_commit("dev-tip", "base"), _commit("redesign-tip", "base")]
    if reverse_commits:
        tips.reverse()
    layout = build_graph(
        [*tips, _commit("base", "root"), _commit("root")],
        [BranchInfo("dev", target_sha="dev-tip"),
         BranchInfo("redesign", target_sha="redesign-tip", is_head=True)],
    )
    nodes = {n.commit.sha: n for n in layout.nodes}
    assert nodes["redesign-tip"].color_index == _pick_branch_color("redesign")
    for sha in ("dev-tip", "base", "root"):
        assert nodes[sha].color_index == _pick_branch_color("dev")


def test_mainline_ref_still_marks_boundary_below_new_feature_commits():
    layout = build_graph(
        [_commit("feature", "base"), _commit("base", "root"), _commit("root")],
        [BranchInfo("redesign", target_sha="feature", is_head=True),
         BranchInfo("dev", target_sha="base")],
    )
    assert [n.color_index for n in layout.nodes] == [
        _pick_branch_color("redesign"), _pick_branch_color("dev"), _pick_branch_color("dev"),
    ]


@pytest.mark.parametrize("first_parent", ["side", "trunk"])
def test_merge_preserves_named_parent_branch_colors(first_parent):
    other_parent = "trunk" if first_parent == "side" else "side"
    layout = build_graph(
        [_commit("merge", first_parent, other_parent), _commit("trunk", "root"),
         _commit("side", "root"), _commit("root")],
        [BranchInfo("dev", target_sha="merge", is_head=True),
         BranchInfo("redesign", target_sha="side")],
    )
    nodes = {n.commit.sha: n for n in layout.nodes}
    assert nodes["merge"].color_index == _pick_branch_color("dev")
    assert nodes["side"].color_index == _pick_branch_color("redesign")


@pytest.mark.parametrize("head_name", ["main", "dev"])
@pytest.mark.parametrize("marker_kind", [None, "stash", "wip"])
@pytest.mark.parametrize("with_merge", [False, True])
def test_main_fork_keeps_blue_trunk_and_green_dev(head_name, marker_kind, with_merge):
    """TC-GRAPH-COLOR-02: git-py 811563a, including a no-ff merge into main."""
    commits = [
        _commit("dev-tip", "dev-old"), _commit("dev-old", "fork"),
        _commit("main-tip", "fork"), _commit("fork", "older"), _commit("older", "root"),
        _commit("root"),
    ]
    main_tip = "main-tip"
    if with_merge:
        # main did not advance before the merge, so its first parent is the
        # unlabelled fork itself, exactly like 3771b2f -> 811563a.
        commits.remove(next(c for c in commits if c.sha == "main-tip"))
        commits.insert(0, _commit("merge", "fork", "dev-tip"))
        main_tip = "merge"
    if marker_kind:
        commits.insert(2, _commit("marker", "fork", kind=marker_kind))
    branches = [
        BranchInfo("dev", target_sha="dev-tip", is_head=head_name == "dev"),
        BranchInfo("main", target_sha=main_tip, is_head=head_name == "main"),
        BranchInfo("redesign", target_sha="dev-old"),
    ]
    layout = build_graph(commits, branches)
    nodes = {n.commit.sha: n for n in layout.nodes}
    blue, green = _pick_branch_color("main"), _pick_branch_color("dev")
    for sha in (main_tip, "fork", "older", "root"):
        assert nodes[sha].color_index == blue, sha
    for sha in ("dev-tip", "dev-old"):
        assert nodes[sha].color_index == green, sha
    fork = nodes["fork"]
    trunk = fork.cells[fork.lane * 2]
    assert trunk.cell_type in (CellType.TEE_RIGHT, CellType.TEE_LEFT)
    assert trunk.pipe_color_index == blue  # below the fork
    # Check the actual dev connector too, including intermediate PIPE/TEE cells.
    if with_merge:
        dev_lane = nodes["dev-old"].lane
        assert dev_lane != fork.lane
        dev_connector = fork.cells[dev_lane * 2]
        dev_color = (
            dev_connector.pipe_color_index if dev_connector.cell_type == CellType.TEE_UP
            else dev_connector.color_index
        )
        assert dev_color == green


def test_merge_preserves_named_first_parent_behind_unlabelled_commits():
    """Crossing a merge must still let an older named parent identify its branch."""
    layout = build_graph(
        [_commit("merge", "unlabelled", "other"), _commit("unlabelled", "side"),
         _commit("other", "root"), _commit("side", "root"), _commit("root")],
        [BranchInfo("main", target_sha="merge"), BranchInfo("redesign", target_sha="side")],
    )
    nodes = {n.commit.sha: n for n in layout.nodes}
    assert nodes["side"].color_index == _pick_branch_color("redesign")
