"""Graph context-menu wiring and the visible revert conflict controls."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from src.core.operations import commit_changes
from src.ui.main_window import MainWindow
from src.ui.widgets.graph_panel import GraphTableWidget
from src.viewmodels.graph_viewmodel import GraphViewModel


def _menu(qtbot, mgr, sha=None, kind="commit"):
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    vm.refresh_graph()
    menu = widget._build_node_menu(sha or mgr.head_commit.sha, kind)
    return widget, menu


def test_revert_menu_emits_selected_older_commit(qtbot, committed_repo):
    selected = committed_repo.head_commit.parents[0]
    widget, menu = _menu(qtbot, committed_repo, sha=selected)
    action = next(a for a in menu.actions() if a.text() == "Revert commit")
    assert action.isEnabled()
    with qtbot.waitSignal(widget.revert_commit_requested) as signal:
        action.trigger()
    assert signal.args == [selected, 0]


@pytest.mark.parametrize("kind", ["stash", "wip"])
def test_revert_absent_for_non_commit_rows(qtbot, committed_repo, kind):
    _, menu = _menu(qtbot, committed_repo, kind=kind)
    assert "Revert commit" not in [a.text() for a in menu.actions()]


def test_revert_disabled_on_detached_head(qtbot, committed_repo):
    committed_repo.repo.set_head(committed_repo.repo.head.target)
    _, menu = _menu(qtbot, committed_repo)
    action = next(a for a in menu.actions() if a.text() == "Revert commit")
    assert not action.isEnabled()


def test_revert_merge_menu_selects_mainline(qtbot, committed_repo):
    repo = committed_repo.repo
    # A real commit with two parents is sufficient to exercise the menu.
    head = repo.head.peel()
    oid = repo.create_commit(
        "HEAD", head.author, head.committer, "merge", head.tree_id,
        [head.id, head.parent_ids[0]],
    )
    widget, menu = _menu(qtbot, committed_repo)
    action = next(a for a in menu.actions() if a.text() == "Revert commit")
    assert action.isEnabled()
    assert len(action.menu().actions()) == 2
    with qtbot.waitSignal(widget.revert_commit_requested) as signal:
        action.menu().actions()[1].trigger()
    assert signal.args == [str(oid), 2]


def _window(qtbot, mgr, tmp_path):
    window = MainWindow(config_path=tmp_path / "settings.json")
    qtbot.addWidget(window)
    window.set_repository(mgr)
    window._main_vm._worktree_refresh_timer.stop()
    return window


def test_graph_menu_creates_revert_and_refreshes_graph(qtbot, committed_repo, tmp_path):
    window = _window(qtbot, committed_repo, tmp_path)
    vm = window._main_vm
    target = committed_repo.head_commit.sha
    menu = window._graph_table._build_node_menu(target, "commit")
    next(a for a in menu.actions() if a.text() == "Revert commit").trigger()
    qtbot.waitUntil(lambda: not vm.is_busy())
    head = committed_repo.head_commit
    assert head.parents == [target]
    assert head.message.startswith('Revert "greet the world"')
    assert any(row.get("commit", {}).get("sha") == head.sha
               for row in window._graph_table._rows)
    assert window._action_undo.isEnabled()
    assert not vm.commit_panel_view_model().staged_files()


@pytest.mark.parametrize("finish", ["continue", "abort"])
def test_revert_conflict_buttons_finish_operation(qtbot, committed_repo, tmp_path, finish):
    target = committed_repo.head_commit.sha
    path = Path(committed_repo.path) / "hello.txt"
    path.write_text("later\n")
    tip = commit_changes(committed_repo, "later").sha
    window = _window(qtbot, committed_repo, tmp_path)
    window.show()
    vm = window._main_vm
    vm.revert_commit(target)
    qtbot.waitUntil(lambda: not vm.is_busy())
    panel = window._conflict_panel
    assert not panel.isHidden()
    assert panel.operation() == "revert"
    assert panel._files.count() == 1
    assert panel._continue_btn.isEnabled()
    assert panel._abort_btn.isEnabled()
    if finish == "continue":
        path.write_text("resolved\n")
        committed_repo.repo.index.add("hello.txt")
        committed_repo.repo.index.write()
        qtbot.mouseClick(panel._continue_btn, Qt.MouseButton.LeftButton)
    else:
        qtbot.mouseClick(panel._abort_btn, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert panel.isHidden()
    assert not committed_repo.repo.status()
    if finish == "continue":
        assert committed_repo.head_commit.parents == [tip]
        assert path.read_text() == "resolved\n"
    else:
        assert committed_repo.head_commit.sha == tip
        assert path.read_text() == "later\n"
