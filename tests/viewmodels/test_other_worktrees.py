"""Sibling WIP nodes follow their own checkout and update in the background."""
from pathlib import Path

import pygit2
import pytest
from src.core.graph_v2 import UNCOMMITTED_COLOR_INDEX
from src.utils.config import save_config
from src.viewmodels.graph_viewmodel import GraphViewModel
from src.viewmodels.main_viewmodel import MainViewModel


def test_clean_worktree_marks_only_its_local_branch_and_clears_on_detach(
    committed_repo, linked_worktree,
):
    branch = linked_worktree.repo.head.shorthand
    committed_repo.repo.create_reference(f"refs/remotes/origin/{branch}",
                                         linked_worktree.repo.head.target)
    rows, error = GraphViewModel._compute_graph(committed_repo)
    assert error is None
    refs = {b["name"]: b for row in rows for b in row["branch_refs"]}
    assert refs[branch]["worktree_path"] == Path(linked_worktree.path).as_posix()
    assert "worktree_path" not in refs[f"origin/{branch}"]
    assert "worktree_path" not in refs["main"]
    assert not any(row.get("worktree") for row in rows)
    linked_worktree.repo.set_head(linked_worktree.repo.head.target)
    rows, error = GraphViewModel._compute_graph(committed_repo)
    assert error is None
    assert not any(b.get("worktree_path") for row in rows for b in row["branch_refs"])


def test_monitor_tracks_clean_worktree_branches_outside_visible_history(
    qtbot, committed_repo, linked_worktree, tmp_path, monkeypatch,
):
    repo = committed_repo.repo
    head = repo.head.peel()
    repo.create_commit("HEAD", head.author, head.committer, "new main tip", head.tree_id, [head.id])
    config = tmp_path / "settings.json"
    save_config(config, {"worktree_refresh_interval_ms": 100, "graph_history_limit": 1})
    vm = MainViewModel(config_path=config, async_enabled=True)
    vm.set_repository(committed_repo)
    graph = vm.graph_view_model()
    branch = linked_worktree.repo.head.shorthand
    expected = {branch: Path(linked_worktree.path).as_posix()}
    assert graph.branch_worktrees() == expected
    calls = []
    original = GraphViewModel._compute_graph

    def compute(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(GraphViewModel, "_compute_graph", compute)
    try:
        qtbot.wait(300)
        assert calls == []
        linked_worktree.repo.set_head(linked_worktree.repo.head.target)
        qtbot.waitUntil(lambda: graph.branch_worktrees() == {})
        assert graph.other_worktrees() == []
        linked_worktree.repo.set_head(f"refs/heads/{branch}")
        qtbot.waitUntil(lambda: graph.branch_worktrees() == expected)
        assert len(calls) == 2
        qtbot.wait(250)
        assert len(calls) == 2
    finally:
        vm.stop_worktree_refresh()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)


def test_sibling_wip_has_unique_id_own_parent_and_wip_style(committed_repo, linked_worktree):
    repo = linked_worktree.repo
    sig = pygit2.Signature("test", "test@example.com")
    tip = repo.create_commit("HEAD", sig, sig, "linked commit", repo.head.peel().tree_id,
                             [repo.head.target])
    for manager in (committed_repo, linked_worktree):
        (Path(manager.path) / "hello.txt").write_text("dirty\n")
    rows, error = GraphViewModel._compute_graph(committed_repo)
    assert error is None
    sibling = next(row for row in rows if row.get("worktree"))
    assert sibling["sha"] != "WIP"
    assert sibling["parents"] == [str(tip)]
    assert sibling["is_uncommitted"] and sibling["kind"] == "wip"
    assert sibling["color_index"] == UNCOMMITTED_COLOR_INDEX
    assert sibling["uncommitted_count"] == 1
    assert "agents-ide/run/test-worktree" in sibling["subject"]
    assert sibling["row"] < next(row["row"] for row in rows if row["sha"] == str(tip))
    assert next(row for row in rows if row["sha"] == "WIP")["parents"] == [
        str(committed_repo.repo.head.target),
    ]


def test_detached_unreferenced_commit_is_in_graph(committed_repo, linked_worktree):
    repo = linked_worktree.repo
    repo.set_head(repo.head.target)
    sig = pygit2.Signature("test", "test@example.com")
    tip = repo.create_commit("HEAD", sig, sig, "detached commit", repo.head.peel().tree_id,
                             [repo.head.target])
    (Path(linked_worktree.path) / "hello.txt").write_text("dirty\n")
    rows, error = GraphViewModel._compute_graph(committed_repo)
    assert error is None
    assert str(tip) in {row["sha"] for row in rows}
    sibling = next(row for row in rows if row.get("worktree"))
    assert sibling["parents"] == [str(tip)]
    assert "detached HEAD" in sibling["subject"]
    # Opening this checkout must retain its own WIP and unreferenced HEAD too.
    rows, error = GraphViewModel._compute_graph(linked_worktree)
    assert error is None
    assert str(tip) in {row["sha"] for row in rows}
    assert next(row for row in rows if row["sha"] == "WIP")["parents"] == [str(tip)]


def test_multiple_worktrees_at_same_head_have_distinct_nodes(
    committed_repo, linked_worktree, tmp_path,
):
    branch = committed_repo.repo.create_branch("second", committed_repo.repo.head.peel())
    path = tmp_path / "second"
    committed_repo.repo.add_worktree("second", str(path), branch)
    (path / "hello.txt").write_text("dirty\n")
    (Path(linked_worktree.path) / "hello.txt").write_text("dirty\n")
    rows, error = GraphViewModel._compute_graph(committed_repo)
    assert error is None
    markers = [row for row in rows if row.get("worktree")]
    assert len({row["sha"] for row in markers}) == 2
    assert all(row["parents"] == [str(committed_repo.repo.head.target)] for row in markers)
    assert len({row["lane"] for row in markers}) == 2


def test_old_worktree_wip_remains_visible_with_truncated_history(
    committed_repo, linked_worktree,
):
    linked_worktree.repo.set_head(linked_worktree.repo.head.peel().parents[0].id)
    (Path(linked_worktree.path) / "hello.txt").write_text("dirty\n")
    rows, error = GraphViewModel._compute_graph(committed_repo, history_limit=1)
    assert error is None
    marker = next(row for row in rows if row.get("worktree"))
    assert marker["parents"] == [str(linked_worktree.repo.head.target)]


def test_monitor_adds_updates_and_removes_sibling_without_current_changes(
    qtbot, committed_repo, linked_worktree, tmp_path, monkeypatch,
):
    config = tmp_path / "settings.json"
    save_config(config, {"worktree_refresh_interval_ms": 100})
    vm = MainViewModel(config_path=config, async_enabled=True)
    vm.set_repository(committed_repo)
    graph = vm.graph_view_model()
    root = Path(linked_worktree.path)
    original = (root / "hello.txt").read_bytes()
    errors = []
    vm.error_occurred.connect(errors.append)
    try:
        (root / "hello.txt").write_text("dirty\n")
        qtbot.waitUntil(lambda: len(graph.other_worktrees()) == 1)
        entry = graph.other_worktrees()[0]
        vm.select_commit(entry.node_id)
        (root / "new.txt").write_text("new\n")
        qtbot.waitUntil(lambda: graph.other_worktrees()[0].count == 2)
        # A changed checkout with the same count must also update the node.
        linked_worktree.repo.create_branch("renamed-target", linked_worktree.repo.head.peel())
        linked_worktree.repo.set_head("refs/heads/renamed-target")
        qtbot.waitUntil(lambda: graph.other_worktrees()[0].branch == "renamed-target")
        repo = linked_worktree.repo
        sig = pygit2.Signature("test", "test@example.com")
        tip = repo.create_commit(
            "HEAD", sig, sig, "external commit", repo.head.peel().tree_id, [repo.head.target],
        )
        qtbot.waitUntil(lambda: graph.other_worktrees()[0].head_sha == str(tip))
        # Stable ticks must not walk the history or rebuild the graph.
        calls = []
        original_compute = GraphViewModel._compute_graph

        def counted(*args, **kwargs):
            calls.append(1)
            return original_compute(*args, **kwargs)

        monkeypatch.setattr(GraphViewModel, "_compute_graph", counted)
        qtbot.wait(250)
        assert calls == []
        (root / "hello.txt").write_bytes(original)
        (root / "new.txt").unlink()
        qtbot.waitUntil(lambda: not graph.other_worktrees())
        assert vm.selected_commit_sha() is None
        assert vm.commit_panel_view_model().file_changes() == []
        assert not vm.command_processor().can_undo
        assert errors == []
    finally:
        vm.stop_worktree_refresh()
        qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)


@pytest.mark.parametrize("action", ["switch", "close", "busy"])
def test_open_worktree_ignores_stale_selection_or_busy(
    qtbot, committed_repo, linked_worktree, tmp_path, action,
):
    (Path(linked_worktree.path) / "hello.txt").write_text("dirty\n")
    vm = MainViewModel(config_path=tmp_path / "settings.json")
    vm.set_repository(committed_repo)
    entry = vm.graph_view_model().other_worktrees()[0]
    requested = []
    vm.open_worktree_requested.connect(requested.append)
    if action == "switch":
        vm.set_repository(linked_worktree)
    elif action == "close":
        vm.set_repository(None)
    else:
        vm._is_busy = True
    vm.open_worktree(entry.node_id)
    assert requested == []
