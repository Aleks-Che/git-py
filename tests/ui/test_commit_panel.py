"""UI tests for the redesigned :class:`CommitPanel` (right-panel commit-input view).

The panel now has a two-list layout (Unstaged + Staged) and a sticky
commit block at the bottom. Tests run under ``pytest-qt`` and drive
the widget through real signal delivery against a real
:class:`MainViewModel` bound to a real :class:`RepositoryManager`.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from src.core.repository import RepositoryManager
from src.ui.widgets.commit_panel import CommitPanel, FileListWidget
from src.viewmodels.main_viewmodel import MainViewModel


def _make_repo_with_change(path: Path) -> RepositoryManager:
    """Open a repo with one commit, then leave ``f.txt`` modified
    and ``untracked.txt`` as an untracked file."""
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])
    (path / "f.txt").write_text("v2\n")
    (path / "untracked.txt").write_text("u\n")
    return mgr


def _panel_paths(list_widget: FileListWidget) -> list[str]:
    """Return the path stored on every visible row in ``list_widget``."""
    result: list[str] = []
    for i in range(list_widget.count()):
        row_widget = list_widget.itemWidget(list_widget.item(i))
        if row_widget is None:
            continue
        result.append(row_widget._change.path)
    return result


def _wait_populate(qtbot, *lists: FileListWidget) -> None:
    """Wait for chunked population to finish on every list."""
    for lst in lists:
        if lst._pending_changes:
            with qtbot.waitSignal(lst.populate_finished, timeout=2000):
                pass


# ----- Unstaged / Staged list rendering --------------------------------


def test_panel_lists_unstaged_modified_and_untracked(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    with qtbot.waitSignal(panel._unstaged_list.populate_finished, timeout=2000):
        pass
    paths = set(_panel_paths(panel._unstaged_list))
    assert paths == {"f.txt", "untracked.txt"}


def test_panel_staged_list_is_empty_before_any_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert _panel_paths(panel._staged_list) == []


def test_panel_headers_show_counts(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert panel._unstaged_expander.text().strip() == "Unstaged Files (2)"
    assert panel._staged_expander.text().strip() == "Staged Files (0)"


def test_panel_headers_update_after_staging(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.stage_file("f.txt")
    assert panel._unstaged_expander.text().strip() == "Unstaged Files (1)"
    assert panel._staged_expander.text().strip() == "Staged Files (1)"


# ----- Stage All Changes -----------------------------------------------


def test_stage_all_button_disabled_when_nothing_unstaged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    # Stage every file before constructing the panel.
    vm.stage_file("f.txt")
    vm.stage_file("untracked.txt")

    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert not panel._stage_all_button.isEnabled()


def test_stage_all_button_moves_every_file_to_staged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    _wait_populate(qtbot, panel._unstaged_list)

    assert panel._stage_all_button.isEnabled()
    panel._stage_all_button.click()
    _wait_populate(qtbot, panel._unstaged_list, panel._staged_list)

    paths = set(_panel_paths(panel._staged_list))
    assert paths == {"f.txt", "untracked.txt"}
    assert _panel_paths(panel._unstaged_list) == []


# ----- Per-row Stage File button ---------------------------------------


def test_clicking_stage_file_button_stages_one_file(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    _wait_populate(qtbot, panel._unstaged_list)

    # Locate the row for f.txt and click its stage button.
    for i in range(panel._unstaged_list.count()):
        row_widget = panel._unstaged_list.itemWidget(panel._unstaged_list.item(i))
        if row_widget._change.path == "f.txt":
            row_widget._stage_button.click()
            break

    _wait_populate(qtbot, panel._unstaged_list, panel._staged_list)
    assert "f.txt" in vm.commit_panel_view_model().staged_files()
    assert "f.txt" not in _panel_paths(panel._unstaged_list)
    assert "f.txt" in _panel_paths(panel._staged_list)


# ----- Click on row = stage / unstage (accessibility shortcut) --------


def test_clicking_unstaged_row_selects_file(qtbot, tmp_git_repo: Path) -> None:
    """Clicking an unstaged file row selects it for diff preview (does NOT stage)."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    _wait_populate(qtbot, panel._unstaged_list)

    cp_vm = vm.commit_panel_view_model()
    assert cp_vm.selected_file() is None

    panel._unstaged_list.item(0).setSelected(True)
    panel._on_unstaged_item_clicked(panel._unstaged_list.item(0))

    # The file is selected (not staged).
    assert cp_vm.selected_file() == "f.txt"
    assert cp_vm.staged_files() == []


def test_clicking_same_unstaged_row_again_deselects(qtbot, tmp_git_repo: Path) -> None:
    """Clicking the same unstaged row again deselects the file."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    _wait_populate(qtbot, panel._unstaged_list)

    cp_vm = vm.commit_panel_view_model()
    item = panel._unstaged_list.item(0)

    panel._on_unstaged_item_clicked(item)
    assert cp_vm.selected_file() == "f.txt"

    panel._on_unstaged_item_clicked(item)
    assert cp_vm.selected_file() is None


def test_clicking_staged_row_selects_file(qtbot, tmp_git_repo: Path) -> None:
    """Clicking a staged row selects the file for diff view."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    _wait_populate(qtbot, panel._staged_list)

    cp_vm = vm.commit_panel_view_model()
    assert cp_vm.selected_file() is None

    panel._staged_list.item(0).setSelected(True)
    panel._on_staged_item_clicked(panel._staged_list.item(0))

    assert cp_vm.selected_file() == "f.txt"
    assert cp_vm._selected_file_staged is True


def test_clicking_same_staged_row_again_deselects(qtbot, tmp_git_repo: Path) -> None:
    """Clicking the same staged row a second time deselects the file."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    _wait_populate(qtbot, panel._staged_list)

    cp_vm = vm.commit_panel_view_model()
    item = panel._staged_list.item(0)

    panel._on_staged_item_clicked(item)
    assert cp_vm.selected_file() == "f.txt"

    panel._on_staged_item_clicked(item)
    assert cp_vm.selected_file() is None


# ----- Commit block (Summary / Description / button) ------------------


def test_commit_button_disabled_without_input_or_staged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert not panel._commit_button.isEnabled()


def test_commit_button_enabled_with_input_and_staged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("hello")
    assert panel._commit_button.isEnabled()


def test_commit_button_label_singular_and_plural(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # 0 staged → plural (no files).
    assert panel._commit_button.text() == "Commit Changes to 0 Files"
    panel._summary.setText("hi")
    # Still 0 staged → button stays disabled.
    assert not panel._commit_button.isEnabled()

    # 1 staged → singular.
    vm.stage_file("f.txt")
    assert "1 File" in panel._commit_button.text()
    assert "1 Files" not in panel._commit_button.text()

    # 2 staged → plural again.
    vm.stage_file("untracked.txt")
    assert "2 Files" in panel._commit_button.text()


def test_commit_button_creates_a_real_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("subject")
    panel._description.setPlainText("body line one\nbody line two")
    head_before = str(mgr.repo.head.target)
    panel._commit_button.click()
    head_after = str(mgr.repo.head.target)

    assert head_after != head_before
    # The message should be "summary\n\ndescription" per the design.
    assert mgr.repo[head_after].message.strip() == "subject\n\nbody line one\nbody line two"


def test_commit_button_enabled_by_description_alone(
    qtbot, tmp_git_repo: Path,
) -> None:
    """At least one of summary / description is sufficient."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert not panel._commit_button.isEnabled()
    panel._description.setPlainText("just a body")
    assert panel._commit_button.isEnabled()


def test_summary_routes_through_viewmodel(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("a new subject")
    assert vm.commit_panel_view_model().commit_summary() == "a new subject"


def test_description_routes_through_viewmodel(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._description.setPlainText("multi\nline\nbody")
    assert vm.commit_panel_view_model().commit_description() == "multi\nline\nbody"


def test_inputs_clear_after_commit(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("subject")
    panel._description.setPlainText("body")
    panel._commit_button.click()

    assert panel._summary.text() == ""
    assert panel._description.toPlainText() == ""
    assert vm.commit_panel_view_model().commit_summary() == ""
    assert vm.commit_panel_view_model().commit_description() == ""


# ----- Collapsible expanders ------------------------------------------


def test_unstaged_expander_toggles_list_visibility(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert panel._unstaged_list.isVisible()
    panel._unstaged_expander.toggle()
    assert not panel._unstaged_list.isVisible()
    panel._unstaged_expander.toggle()
    assert panel._unstaged_list.isVisible()
