"""Tests for :class:`src.viewmodels.graph_viewmodel.GraphViewModel`.

The ViewModel is pure logic + Qt signals — no widget is created —
so we drive it with :func:`qtbot.waitSignal` and inspect the
emitted payloads directly. Repositories come from the
``committed_repo`` and ``tmp_git_repo`` fixtures in ``conftest.py``.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QCoreApplication
from src.core.repository import RepositoryManager
from src.viewmodels.graph_viewmodel import GraphViewModel


def _ensure_app() -> None:
    """Make sure a QCoreApplication exists for signal delivery."""
    QCoreApplication.instance() or QCoreApplication([])


# ----- binding / refresh -----------------------------------------------


def test_refresh_with_no_repository_emits_empty_list(qtbot) -> None:
    _ensure_app()
    vm = GraphViewModel()
    with qtbot.waitSignal(vm.graph_updated, timeout=1000) as blocker:
        vm.refresh_graph()
    assert blocker.args[0] == []


def test_refresh_on_empty_repo_emits_empty_list(qtbot, tmp_git_repo: Path) -> None:
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    vm = GraphViewModel(mgr)
    with qtbot.waitSignal(vm.graph_updated, timeout=1000) as blocker:
        vm.refresh_graph()
    assert blocker.args[0] == []


def test_refresh_on_populated_repo_emits_rows_with_expected_keys(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    rows = blocker.args[0]
    assert len(rows) == 2
    for row in rows:
        for key in (
            "sha", "short_sha", "subject", "author_name", "author_time",
            "parents", "refs", "lane", "color", "row",
        ):
            assert key in row, f"missing key {key!r} in row {row!r}"


def test_set_repository_triggers_refresh(qtbot, tmp_git_repo: Path) -> None:
    _ensure_app()
    vm = GraphViewModel()
    # First call: no repo, should emit empty list.
    with qtbot.waitSignal(vm.graph_updated, timeout=1000) as blocker:
        vm.refresh_graph()
    assert blocker.args[0] == []

    # After binding a populated repo, the next emission must be non-empty.
    mgr = RepositoryManager(str(tmp_git_repo))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (tmp_git_repo / "x.txt").write_text("x\n")
    mgr.repo.index.add("x.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])

    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.set_repository(mgr)
    assert len(blocker.args[0]) == 1


def test_set_repository_to_none_clears_graph(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.set_repository(None)
    assert blocker.args[0] == []


def test_refresh_handles_corrupt_history_quietly(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)

    def _boom(*_args, **_kwargs):
        from src.core.exceptions import GitError
        raise GitError("simulated failure")

    # ``monkeypatch.setattr`` rebinds the function on the instance,
    # so we no longer get the ``self`` auto-injection of a method.
    monkeypatch.setattr(committed_repo, "get_all_history", _boom)
    with qtbot.waitSignal(vm.error_occurred, timeout=1000) as blocker:
        vm.refresh_graph()
    assert "simulated failure" in blocker.args[0]


# ----- select_commit ---------------------------------------------------


def test_select_commit_emits_signal(qtbot) -> None:
    _ensure_app()
    vm = GraphViewModel()
    with qtbot.waitSignal(vm.commit_selected, timeout=1000) as blocker:
        vm.select_commit("a" * 40)
    assert blocker.args[0] == "a" * 40


# ----- get_commit_details ---------------------------------------------


def test_get_commit_details_returns_commit_info(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)
    head_sha = committed_repo.head_commit.sha
    info = vm.get_commit_details(head_sha)
    assert info is not None
    assert info.sha == head_sha
    assert info.message.strip() == "greet the world"


def test_get_commit_details_for_unknown_sha_returns_none(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)
    assert vm.get_commit_details("0" * 40) is None


def test_get_commit_details_with_no_repository_returns_none() -> None:
    _ensure_app()
    vm = GraphViewModel()
    assert vm.get_commit_details("a" * 40) is None


# ----- integration: rows reflect the real repo ------------------------


def test_graph_updated_carries_head_and_branch_label(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    rows = blocker.args[0]
    head_row = rows[0]  # newest first
    assert "HEAD" in head_row["refs"]
    # Branch names live in ``branch_refs`` now, with their
    # ``is_head`` / ``is_remote`` metadata, so the widget can draw
    # decorations in the left-hand column.
    assert any(b["name"] == "main" and b["is_head"] for b in head_row["branch_refs"])
    # The head commit's first parent must be the previous commit.
    assert rows[1]["sha"] in head_row["parents"]


def test_graph_updated_layout_changes_after_new_commit(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    initial_rows = blocker.args[0]
    assert len(initial_rows) == 2

    # Add a third commit on top of the existing tip. The worktree
    # path is whatever ``committed_repo.path`` resolves to.
    from pathlib import Path
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    worktree = Path(committed_repo.path)
    (worktree / "extra.txt").write_text("extra\n")
    committed_repo.repo.index.add("extra.txt")
    committed_repo.repo.index.write()
    tree = committed_repo.repo.index.write_tree()
    head = committed_repo.repo.head.target
    committed_repo.repo.create_commit(
        "refs/heads/main", sig, sig, "third commit", tree, [head],
    )

    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    new_rows = blocker.args[0]
    assert len(new_rows) == 3
    assert new_rows[0]["subject"] == "third commit"


# ----- WIP node (Stage 3) ---------------------------------------------


def test_wip_node_appears_when_worktree_has_changes(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A modified worktree file triggers a synthetic WIP node above HEAD."""
    from pathlib import Path

    _ensure_app()
    (Path(committed_repo.path) / "hello.txt").write_text("hello, modified\n")
    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    rows = blocker.args[0]
    # WIP node on top, then the existing two commits.
    assert rows[0]["sha"] == "WIP"
    assert rows[0]["subject"] == "WIP: Uncommitted changes"
    assert rows[0]["parents"] == [rows[1]["sha"]]  # WIP's parent is real HEAD
    assert len(rows) == 3


def test_wip_node_absent_on_clean_worktree(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    rows = blocker.args[0]
    assert all(r["sha"] != "WIP" for r in rows)


def test_wip_node_on_unborn_head_with_untracked_file(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The very-first-commit case: unborn HEAD + an untracked file."""
    _ensure_app()
    (tmp_git_repo / "first.txt").write_text("hi\n")
    vm = GraphViewModel(RepositoryManager(str(tmp_git_repo)))
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    rows = blocker.args[0]
    assert len(rows) == 1
    assert rows[0]["sha"] == "WIP"
    # Unborn HEAD → WIP has no parent commit.
    assert rows[0]["parents"] == []


@pytest.mark.parametrize("bad", ["", "not-a-sha"])
def test_select_commit_emits_whatever_it_is_given(qtbot, bad) -> None:
    """The ViewModel doesn't validate the SHA; the panel does.

    This documents the contract: a click on an unknown commit will
    just produce a ``commit_selected`` signal with the offending
    string. The detail panel turns that into a "no such commit"
    display.
    """
    _ensure_app()
    vm = GraphViewModel()
    with qtbot.waitSignal(vm.commit_selected, timeout=1000) as blocker:
        vm.select_commit(bad)
    assert blocker.args[0] == bad


# ----- stash nodes (Stage 7) -----------------------------------------


def test_stash_nodes_appear_in_graph(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Stash entries appear as synthetic nodes above WIP in the graph."""
    _ensure_app()
    from pathlib import Path

    (Path(committed_repo.path) / "hello.txt").write_text("stashed\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "graph-stash")

    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    rows = blocker.args[0]
    stash_nodes = [r for r in rows if r.get("kind") == "stash"]
    assert len(stash_nodes) == 1
    assert "Stash @{0}" in stash_nodes[0]["subject"]
    # The stash node carries the real commit OID, not a synthetic SHA.
    assert stash_nodes[0]["short_sha"] is not None
    assert stash_nodes[0]["short_sha"] != "stash@0"


def test_multiple_stash_nodes_appear_in_order(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Two stash entries → two synthetic nodes, STASH_1 before STASH_0."""
    _ensure_app()
    from pathlib import Path

    from src.core.operations import stash_push

    (Path(committed_repo.path) / "hello.txt").write_text("first\n")
    stash_push(committed_repo, "first stash")
    (Path(committed_repo.path) / "hello.txt").write_text("second\n")
    stash_push(committed_repo, "second stash")

    vm = GraphViewModel(committed_repo)
    with qtbot.waitSignal(vm.graph_updated, timeout=2000) as blocker:
        vm.refresh_graph()
    rows = blocker.args[0]
    stash_nodes = [r for r in rows if r.get("kind") == "stash"]
    assert len(stash_nodes) == 2
    # Most recent stash (index 0, created second) is at row 0 (top).
    assert "Stash @{0}" in stash_nodes[0]["subject"]
    assert "Stash @{1}" in stash_nodes[1]["subject"]
