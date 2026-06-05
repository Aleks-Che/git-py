"""Tests for the redesigned right panel and the selection VM contract.

These cover the behaviour the design spec calls out explicitly:

* The right panel is hidden until a commit is selected.
* Clicking the WIP node opens the commit-input view.
* Clicking a real commit opens the commit-detail view.
* Clicking the *same* commit again toggles selection off
  (the panel disappears).
* After a successful commit the newly created commit is auto-selected
  and the panel switches to the commit-detail view for that SHA.

Most of these exercise :class:`MainViewModel` and
:class:`RightPanel` together — the panel is a thin shell over the VM
signals, so the assertions are mostly about *what the VM emits* and
*what the panel shows*.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow
from src.ui.widgets.right_panel import RightPanel
from src.viewmodels.graph_viewmodel import WIP_SHA
from src.viewmodels.main_viewmodel import MainViewModel


def _make_committed_repo(path: Path) -> RepositoryManager:
    """A repo with one commit on ``main`` (a tracked ``f.txt``)."""
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])
    return mgr


def _make_dirty_repo(path: Path) -> RepositoryManager:
    """A repo with one commit plus a working-tree modification."""
    mgr = _make_committed_repo(path)
    (path / "f.txt").write_text("v2\n")
    return mgr


# ----- selection_changed signal contract ---------------------------------


def test_selection_starts_as_none(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    assert vm.selected_commit_sha() is None


def test_selecting_wip_emits_wip_sha(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(WIP_SHA)
    assert blocker.args[0] == WIP_SHA
    assert vm.selected_commit_sha() == WIP_SHA


def test_selecting_real_commit_emits_its_sha(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(head_sha)
    assert blocker.args[0] == head_sha


def test_selecting_same_commit_toggles_off(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)

    vm.select_commit(WIP_SHA)
    assert vm.selected_commit_sha() == WIP_SHA

    # Re-selecting the same SHA clears the selection.
    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(WIP_SHA)
    assert blocker.args[0] is None
    assert vm.selected_commit_sha() is None


def test_selecting_different_commit_replaces_selection(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)

    vm.select_commit(WIP_SHA)
    assert vm.selected_commit_sha() == WIP_SHA

    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(head_sha)
    assert blocker.args[0] == head_sha
    assert vm.selected_commit_sha() == head_sha


def test_set_repository_clears_selection(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    assert vm.selected_commit_sha() == WIP_SHA

    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.set_repository(None)
    assert blocker.args[0] is None
    assert vm.selected_commit_sha() is None


# ----- commit_changes auto-selects the new commit ----------------------


def test_commit_auto_selects_new_commit(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    vm.commit_panel_view_model().set_commit_summary("new commit")

    head_before = str(mgr.repo.head.target)
    with qtbot.waitSignal(vm.selection_changed, timeout=2000) as blocker:
        vm.commit_changes("new commit")
    head_after = str(mgr.repo.head.target)

    assert head_after != head_before
    assert blocker.args[0] == head_after
    # And the right panel / VM still consider the new commit the
    # current selection.
    assert vm.selected_commit_sha() == head_after


# ----- right-panel visibility / mode switching --------------------------


def test_right_panel_hidden_when_no_selection(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    # The panel starts hidden (no commit selected) — the
    # ``__init__`` calls ``_on_selection_changed(None)`` which sets
    # ``setVisible(False)`` so a freshly-opened window does not show
    # the panel until the user picks a commit.
    assert panel.isHidden()


def test_right_panel_visible_after_selecting_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    # ``isVisibleTo`` is the parent-aware variant — the panel only
    # needs to flag itself as not-hidden for the splitter to give it
    # real estate.
    assert not panel.isHidden()


def test_right_panel_hides_after_toggle_off(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    assert not panel.isHidden()

    vm.select_commit(head_sha)
    assert panel.isHidden()


def test_right_panel_wip_view_shows_commit_input(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(WIP_SHA)
    # Index 0 of the stack is the commit-input view.
    assert panel._stack.currentIndex() == 0
    assert panel._commit_input.isVisible()


def test_right_panel_detail_view_for_real_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    # Index 1 of the stack is the commit-detail view.
    assert panel._stack.currentIndex() == 1
    assert panel._commit_detail.isVisible()


# ----- commit-detail view: structure (message + info + file list) ------


def test_commit_detail_panel_renders_message_and_files(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    detail = panel._commit_detail

    # Subject of the test commit is "first".
    assert "first" in detail._message.text()
    # The file we committed in the fixture ("f.txt") shows up in the
    # changed-files list. pygit2 reports a freshly-tracked file as
    # an *add* in the diff, so the badge is "A".
    paths = [
        detail._files.item(i).text()  # noqa: SLF001
        for i in range(detail._files.count())  # noqa: SLF001
    ]
    assert any("f.txt" in p for p in paths)
    assert any("[A]" in p for p in paths)


# ----- stage_all_unstaged verb ----------------------------------------


def test_stage_all_unstaged_moves_every_file(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    (tmp_git_repo / "new.txt").write_text("n\n")
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    assert set(vm.commit_panel_view_model().unstaged_paths()) == {"f.txt", "new.txt"}

    vm.stage_all_unstaged()
    assert vm.commit_panel_view_model().unstaged_paths() == []
    assert set(vm.commit_panel_view_model().staged_files()) == {"f.txt", "new.txt"}


def test_unstage_all_staged_moves_all_files_back(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    (tmp_git_repo / "new.txt").write_text("n\n")
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    vm.stage_all_unstaged()
    assert set(vm.commit_panel_view_model().staged_files()) == {"f.txt", "new.txt"}

    vm.unstage_all_staged()
    assert vm.commit_panel_view_model().staged_files() == []
    assert set(vm.commit_panel_view_model().unstaged_paths()) == {"f.txt", "new.txt"}


# ----- diff view (graph replacement on file selection) -----------------


def test_selecting_file_switches_graph_stack(qtbot, tmp_git_repo: Path) -> None:
    """Selecting an unstaged file shows the diff view in place of the graph."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    # Initially graph is visible, diff is hidden.
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()

    cp_vm.select_file("f.txt")
    assert window._graph_stack.currentIndex() == 1
    assert window._diff_view.isVisible()
    # The diff text should be non-empty (the file is modified).
    assert len(window._diff_view.toPlainText()) > 0


def test_deselecting_file_returns_graph(qtbot, tmp_git_repo: Path) -> None:
    """Deselecting a file returns the graph view."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    assert window._graph_stack.currentIndex() == 1

    cp_vm.select_file(None)
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()


def test_selecting_staged_file_shows_diff(qtbot, tmp_git_repo: Path) -> None:
    """Selecting a staged file shows the diff view (staged diff)."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)
    window._main_vm.stage_file("f.txt")

    cp_vm = window._main_vm.commit_panel_view_model()
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()

    cp_vm.select_file("f.txt", staged=True)
    assert window._graph_stack.currentIndex() == 1
    assert window._diff_view.isVisible()
    assert len(window._diff_view.toPlainText()) > 0


def test_deselecting_staged_file_returns_graph(qtbot, tmp_git_repo: Path) -> None:
    """Deselecting a staged file returns the graph view."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)
    window._main_vm.stage_file("f.txt")

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt", staged=True)
    assert window._graph_stack.currentIndex() == 1

    cp_vm.select_file(None)
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()


# ----- integration with MainWindow ------------------------------------


def test_main_window_right_panel_in_place(qtbot, tmp_git_repo: Path) -> None:
    """The :class:`RightPanel` replaces the old right_vertical splitter
    and is owned by :class:`MainWindow` as ``_right_panel``."""
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert hasattr(window, "_right_panel")
    assert isinstance(window._right_panel, RightPanel)
    # The old attributes are gone.
    assert not hasattr(window, "_right_splitter")
    assert not hasattr(window, "_commit_panel")
    assert not hasattr(window, "_detail_panel")


def test_main_window_graph_click_drives_selection(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The graph widget's commit_selected signal routes through
    :meth:`MainViewModel.select_commit` so the right panel reacts
    to clicks."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    right = window._right_panel
    assert not right.isVisible()

    # Emulate a click on the head commit by emitting the graph
    # widget's commit_selected signal.
    window._graph_table.commit_selected.emit(head_sha)

    assert window._main_vm.selected_commit_sha() == head_sha
    assert right.isVisible()


def test_main_window_re_click_toggles_panel_off(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    right = window._right_panel

    window._graph_table.commit_selected.emit(head_sha)
    assert right.isVisible()

    # Clicking the same commit again clears the selection.
    window._graph_table.commit_selected.emit(head_sha)
    assert not right.isVisible()
    assert window._main_vm.selected_commit_sha() is None


def test_main_window_close_repository_hides_right_panel(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._graph_table.commit_selected.emit(head_sha)
    right = window._right_panel
    assert right.isVisible()

    window.set_repository(None)
    assert not right.isVisible()
