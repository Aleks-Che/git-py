"""Exercise Repair through the actual context menu and async command path."""
from pathlib import Path

import pytest
from src.ui.widgets.commit_panel import CommitPanel
from src.viewmodels.main_viewmodel import MainViewModel


def _paths(view):
    return {view.model().change_at(i).path for i in range(view.model().rowCount())}


@pytest.mark.parametrize("async_enabled", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_repair_menu_clears_phantom_changes_and_selected_diff(
    qtbot, committed_repo, make_stale_index, tmp_path, async_enabled, batch,
):
    paths = make_stale_index(committed_repo)
    vm = MainViewModel(config_path=tmp_path / "config.json", async_enabled=async_enabled)
    vm.set_repository(committed_repo)
    vm._worktree_refresh_timer.stop()
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    errors, busy = [], []
    vm.error_occurred.connect(errors.append)
    vm.busy_changed.connect(busy.append)
    selected = paths if batch else paths[:1]
    cp = vm.commit_panel_view_model()
    cp.set_commit_summary("Keep my draft")
    cp.select_file(selected[0])
    assert set(paths) <= _paths(panel._unstaged_list)
    menu = panel._unstaged_list._build_context_menu(selected)
    action = next(a for a in menu.actions() if a.text().startswith("Repair"))
    assert "actual changes" in action.toolTip()
    action.trigger()
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert not (set(selected) & _paths(panel._unstaged_list))
    assert _paths(panel._staged_list) == set()
    assert cp.selected_file() is None
    assert cp.commit_summary() == "Keep my draft"
    assert not vm.command_processor().can_undo
    assert errors == []
    if async_enabled:
        assert busy == [True, False]
    assert all((Path(committed_repo.path) / p).read_bytes() == b"first\nsecond\n" for p in paths)
    assert not any(
        a.text().startswith("Repair")
        for a in panel._staged_list._build_context_menu(paths).actions()
    )


@pytest.mark.parametrize("async_enabled", [False, True])
def test_repair_menu_reports_real_edits_without_staging(
    qtbot, committed_repo, make_stale_index, tmp_path, async_enabled,
):
    paths = make_stale_index(committed_repo)
    vm = MainViewModel(config_path=tmp_path / "config.json", async_enabled=async_enabled)
    vm.set_repository(committed_repo)
    vm._worktree_refresh_timer.stop()
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    errors = []
    vm.error_occurred.connect(errors.append)
    menu = panel._unstaged_list._build_context_menu(paths)
    (Path(committed_repo.path) / paths[1]).write_bytes(b"actual edits\n")
    next(a for a in menu.actions() if a.text().startswith("Repair")).trigger()
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert errors and "real unstaged changes" in errors[-1]
    assert _paths(panel._unstaged_list) == set(paths)
    assert _paths(panel._staged_list) == set()
    assert not vm.command_processor().can_undo
