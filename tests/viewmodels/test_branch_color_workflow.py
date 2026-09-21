"""TC-GRAPH-COLOR-01: creating redesign must preserve dev's existing history."""

from pathlib import Path

import pygit2
from src.core.graph_v2 import BRANCH_PALETTE, _pick_branch_color
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    CheckoutCommand,
    CommandProcessor,
    CommitCommand,
    CreateBranchCommand,
)
from src.viewmodels.graph_viewmodel import GraphViewModel


def test_redesign_creation_preserves_dev_history_through_branch_workflow(qtbot, tmp_git_repo):
    """Exercise real commands and graph refreshes, including a fresh repository reader."""
    manager = RepositoryManager(str(tmp_git_repo))
    manager.repo.set_head("refs/heads/dev")
    processor = CommandProcessor()
    dev_color = BRANCH_PALETTE[_pick_branch_color("dev")]
    redesign_color = BRANCH_PALETTE[_pick_branch_color("redesign")]
    assert dev_color != redesign_color
    commit_count = 0

    def commit_file(branch):
        nonlocal commit_count
        assert manager.repo.lookup_reference("HEAD").target == f"refs/heads/{branch}"
        commit_count += 1
        message = f"{branch} change {commit_count}"
        (Path(manager.path) / "tracked.txt").write_text(message + "\n", encoding="utf-8")
        manager.repo.index.add("tracked.txt")
        manager.repo.index.write()
        signature = pygit2.Signature("tester", "test@example.com", 1_700_000_000 + commit_count, 0)
        processor.execute(CommitCommand(manager, message, signature, signature))
        return manager.head_commit.sha

    # Given: dev already has a substantial, uniformly coloured history.
    expected_colors = {commit_file("dev"): dev_color for _ in range(10)}
    base_sha = manager.head_commit.sha
    vm = GraphViewModel(manager, history_limit=100)

    def assert_history(view_model, head_branch):
        with qtbot.waitSignal(view_model.graph_updated, timeout=2000) as signal:
            view_model.refresh_graph()
        rows = signal.args[0]
        assert rows, "The graph must be populated after each workflow step"
        assert not any(row["is_uncommitted"] for row in rows)
        assert {row["sha"]: row["color"] for row in rows} == expected_colors
        head_refs = [branch["name"] for row in rows for branch in row["branch_refs"]
                     if branch["is_head"]]
        assert head_refs == [head_branch]
        return {row["sha"]: row for row in rows}

    assert_history(vm, "dev")

    # When: create redesign at dev's tip, visit it, and return without commits.
    processor.execute(CreateBranchCommand(manager, "redesign"))
    rows = assert_history(vm, "dev")
    assert {branch["name"] for branch in rows[base_sha]["branch_refs"]} == {"dev", "redesign"}
    processor.execute(CheckoutCommand(manager, "redesign"))
    assert_history(vm, "redesign")
    processor.execute(CheckoutCommand(manager, "dev"))
    assert_history(vm, "dev")

    # Then: each new dev commit preserves EVERY old commit's colour, including
    # the commit now carrying only the redesign label. This caught the defect.
    for _ in range(2):
        dev_tip = commit_file("dev")
        expected_colors[dev_tip] = dev_color
        rows = assert_history(vm, "dev")
        assert [branch["name"] for branch in rows[base_sha]["branch_refs"]] == ["redesign"]
        assert str(manager.repo.lookup_branch("redesign").target) == base_sha

    # Reopening must reconstruct the same colours without the previous VM's state.
    reopened = RepositoryManager(manager.path)
    try:
        assert_history(GraphViewModel(reopened, history_limit=100), "dev")
    finally:
        reopened.close()

    # A real redesign commit gets redesign's own colour; the shared ancestry
    # remains dev-coloured even while redesign is checked out and newest.
    processor.execute(CheckoutCommand(manager, "redesign"))
    assert_history(vm, "redesign")
    redesign_tip = commit_file("redesign")
    expected_colors[redesign_tip] = redesign_color
    assert_history(vm, "redesign")
    processor.execute(CheckoutCommand(manager, "dev"))
    assert_history(vm, "dev")
    assert str(manager.repo.lookup_branch("dev").target) == dev_tip
    assert str(manager.repo.lookup_branch("redesign").target) == redesign_tip
    assert manager.get_status() == []
