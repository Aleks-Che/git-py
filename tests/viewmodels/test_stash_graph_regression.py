"""Real linked-worktree regression: WIP -> stash -> new edits -> clean."""
from pathlib import Path

import pygit2
from src.core.graph_v2 import CellType
from src.viewmodels.commands import CommandProcessor, StashPushCommand
from src.viewmodels.graph_viewmodel import GraphViewModel


def test_stash_in_detached_worktree_keeps_complete_graph_connection(
    qtbot, committed_repo, linked_worktree,
) -> None:
    repo = linked_worktree
    base = repo.repo.head.peel()
    repo.repo.set_head(base.id)
    assert repo.repo.head_is_detached

    # The other worktree's branch advances while this checkout stays at base.
    parent = base.id
    for i in range(3):
        sig = pygit2.Signature("tester", "test@example.com", base.commit_time - 10 + i, 0)
        parent = committed_repo.repo.create_commit(
            "refs/heads/main", sig, sig, f"other branch {i}", base.tree_id, [parent],
        )
    path = Path(repo.path) / "hello.txt"
    original = path.read_bytes()
    path.write_bytes(b"save these edits\n")
    vm = GraphViewModel(repo, history_limit=100)

    def refresh():
        with qtbot.waitSignal(vm.graph_updated, timeout=2000) as signal:
            vm.refresh_graph()
        return signal.args[0]

    assert any(row["is_uncommitted"] for row in refresh())
    processor = CommandProcessor()
    processor.execute(StashPushCommand(repo, "graph regression"))
    saved = repo.stash_list[0]
    assert saved.parent_sha == str(base.id)
    assert path.read_bytes() == original
    assert repo.get_status() == []

    def check_connection(rows, *, has_wip):
        stash_idx = next(i for i, row in enumerate(rows) if row["sha"] == saved.sha)
        head_idx = next(i for i, row in enumerate(rows) if row["sha"] == str(base.id))
        stash = rows[stash_idx]
        head = rows[head_idx]
        assert head_idx - stash_idx == 4  # three branch commits between endpoints
        assert stash["parents"] == [str(base.id)]
        col = stash["lane"] * 2
        if has_wip:
            assert rows[0]["is_uncommitted"]
            assert rows[0]["lane"] == head["lane"] != stash["lane"]
        else:
            assert not any(row["is_uncommitted"] for row in rows)
            assert stash["lane"] == head["lane"]
        for row in rows[stash_idx + 1:head_idx]:
            assert row["cells"][col] == {"t": CellType.PIPE, "c": stash["color_index"]}
        assert all(cell["t"] != CellType.PIPE for cell in rows[0]["cells"])

    check_connection(refresh(), has_wip=False)
    path.write_text("new edits after saving\n", encoding="utf-8")
    check_connection(refresh(), has_wip=True)
    path.write_bytes(original)
    check_connection(refresh(), has_wip=False)
    assert str(repo.repo.head.target) == str(base.id)
    assert repo.stash_list[0].sha == saved.sha
    assert (repo.repo[pygit2.Oid(hex=saved.sha)].tree / "hello.txt").data == b"save these edits\n"
