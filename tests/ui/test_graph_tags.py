"""Create tags from graph menus without changing the selected branch."""
import pygit2
import pytest
from PySide6.QtWidgets import QInputDialog
from src.ui.main_window import MainWindow
from src.ui.widgets.graph_panel import GraphTableWidget
from src.viewmodels.graph_viewmodel import GraphViewModel


@pytest.fixture
def tag_window(qtbot, committed_repo):
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_repository(committed_repo)
    return window


@pytest.mark.parametrize("source", ["commit", "local", "remote", "current"])
def test_graph_menu_creates_tag_at_clicked_target(
    qtbot, monkeypatch, tag_window, committed_repo, source,
):
    repo = committed_repo.repo
    head = repo.head.target
    target = str(head) if source == "current" else committed_repo.head_commit.parents[0]
    graph = tag_window._graph_table
    # A different selection must not redirect the action to HEAD.
    graph.set_selected_sha(str(head))
    if source == "commit":
        menu = graph._build_node_menu(target, "commit")
        actions = menu.actions()
    else:
        full_name = "origin/topic" if source == "remote" else "topic"
        if source == "current":
            full_name = "main"
        else:
            prefix = "refs/remotes/" if source == "remote" else "refs/heads/"
            repo.create_reference(prefix + full_name, pygit2.Oid(hex=target))
        actions = graph._build_branch_menu_actions({
            "display": full_name.split("/")[-1],
            "full_name": full_name,
            "is_remote": source == "remote",
            "row_sha": target,
        })
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("  release/v1  ", True))
    action = next(a for a in actions if a.text() == "Create tag here…")
    assert action.isEnabled()

    action.trigger()

    assert repo.lookup_reference("refs/tags/release/v1").target == pygit2.Oid(hex=target)
    assert repo.head.target == head
    assert repo.head.shorthand == "main"
    assert tag_window._main_vm.branch_panel_view_model().tags()[0].name == "release/v1"
    assert "release/v1" in graph._row_by_sha(target)["refs"]

    # Use the actual toolbar actions and wait for any background work.
    tag_window._action_undo.trigger()
    qtbot.waitUntil(lambda: not tag_window._main_vm.is_busy(), timeout=5000)
    assert committed_repo.tags == []
    assert "release/v1" not in graph._row_by_sha(target)["refs"]
    tag_window._action_redo.trigger()
    qtbot.waitUntil(lambda: not tag_window._main_vm.is_busy(), timeout=5000)
    assert committed_repo.tags[0].target_sha == target
    assert "release/v1" in graph._row_by_sha(target)["refs"]


@pytest.mark.parametrize("answer", [("v1", False), ("", True), ("   ", True)])
def test_cancel_or_blank_tag_name_does_nothing(monkeypatch, tag_window, committed_repo, answer):
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: answer)
    tag_window._graph_table.create_tag_requested.emit(committed_repo.head_commit.sha)
    assert committed_repo.tags == []
    assert not tag_window._main_vm.command_processor().can_undo


def test_changing_repository_during_prompt_cancels_creation(
    monkeypatch, tag_window, committed_repo,
):
    def prompt(*args, **kwargs):
        tag_window.set_repository(None)
        return "v1", True

    monkeypatch.setattr(QInputDialog, "getText", prompt)
    tag_window._graph_table.create_tag_requested.emit(committed_repo.head_commit.sha)
    assert committed_repo.tags == []


@pytest.mark.parametrize("kind", ["stash", "wip"])
def test_non_commit_menu_has_no_create_tag(qtbot, kind):
    graph = GraphTableWidget(GraphViewModel())
    qtbot.addWidget(graph)
    menu = graph._build_node_menu("WIP" if kind == "wip" else "a" * 40, kind)
    assert "Create tag here…" not in [a.text() for a in menu.actions()]
