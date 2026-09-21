"""Real worktree: edited branch already merged into a newer merge commit."""

from pathlib import Path

import pygit2
import pytest
from src.core.graph_v2 import CellType
from src.viewmodels.graph_viewmodel import GraphViewModel


@pytest.mark.parametrize("detached", [False, True])
def test_merged_worktree_wip_stays_above_its_head(
    qtbot, committed_repo, linked_worktree, detached,
):
    repo = linked_worktree
    base = repo.repo.head.peel()

    def commit(message, timestamp, *parents):
        sig = pygit2.Signature("tester", "test@example.com", base.commit_time + timestamp, 0)
        return repo.repo.create_commit(None, sig, sig, message, base.tree_id, list(parents))

    side = commit("side", 1, base.id)
    trunk = commit("trunk", 2, base.id)
    head = commit("worktree head", 3, base.id)
    merge_side = commit("merge side", 4, trunk, side)
    merge_head = commit("merge worktree branch", 5, merge_side, head)
    repo.repo.lookup_reference(repo.repo.head.name).set_target(head)
    committed_repo.repo.lookup_reference("refs/heads/main").set_target(merge_head)
    if detached:
        repo.repo.set_head(head)

    path = Path(repo.path) / "hello.txt"
    original = path.read_bytes()
    vm = GraphViewModel(repo)

    def refresh():
        with qtbot.waitSignal(vm.graph_updated, timeout=3000) as signal:
            vm.refresh_graph()
        return signal.args[0]

    clean = refresh()
    original_rows = {row["sha"]: row for row in clean}
    lane = original_rows[str(head)]["lane"]
    crossing = original_rows[str(merge_side)]["cells"][lane * 2]
    assert crossing["t"] == CellType.HORIZONTAL_PIPE
    assert original_rows[str(head)]["cells"][(lane + 1) * 2]["t"] == CellType.PIPE

    path.write_bytes(b"uncommitted edits\n")
    dirty = refresh()
    changed_rows = {row["sha"]: row for row in dirty}
    assert dirty[0]["is_uncommitted"]
    assert dirty[0]["uncommitted_count"] == 1
    assert dirty[0]["parents"] == [str(head)]
    assert dirty[0]["lane"] == lane == changed_rows[str(head)]["lane"]
    assert changed_rows[str(head)]["cells"] == original_rows[str(head)]["cells"]
    assert changed_rows[str(merge_side)]["cells"][lane * 2] == crossing
    assert changed_rows[str(merge_head)]["cells"][lane * 2]["t"] == CellType.TEE_LEFT
    assert str(repo.repo.head.target) == str(head)
    assert path.read_bytes() == b"uncommitted edits\n"

    path.write_bytes(original)
    assert refresh() == clean  # WIP has no lasting effect on the original graph.
