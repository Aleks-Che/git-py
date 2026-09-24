"""Actual context-menu actions remove caches from Git but preserve local files."""
from pathlib import Path

import pytest
from src.core.models import FileStatus
from src.core.operations import commit_changes
from src.ui.widgets.commit_panel import CommitPanel
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def tracking_panel(qtbot, committed_repo, tmp_path, request):
    root = Path(committed_repo.path)
    paths = [
        "wiki-doc/wiki-doc/scripts/__pycache__/artifact_schema.cpython-312.pyc",
        "wiki-doc/wiki-doc/scripts/__pycache__/ddl.cpython-312.pyc",
    ]
    for path in paths:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(b"original cache")
        committed_repo.repo.index.add(path)
    (root / ".gitignore").write_text(
        "wiki-doc/wiki-doc/scripts/__pycache__/\n", encoding="utf-8",
    )
    committed_repo.repo.index.add(".gitignore")
    committed_repo.repo.index.write()
    commit_changes(committed_repo, "tracked caches", stage_all=False)
    for path in paths:
        (root / path).write_bytes(b"regenerated cache")
    vm = MainViewModel(config_path=tmp_path / "config.json", async_enabled=request.param)
    vm.set_repository(committed_repo)
    vm._worktree_refresh_timer.stop()
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    return vm, panel, paths, root


def _changes(view):
    model = view.model()
    return {model.change_at(i).path: model.change_at(i).status for i in range(model.rowCount())}


def _action(menu):
    return next((a for a in menu.actions() if a.text().startswith("Stop Tracking")), None)


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("batch", [False, True])
def test_menu_stop_tracking_updates_lists_and_undo_redo(qtbot, tracking_panel, staged, batch):
    vm, panel, paths, root = tracking_panel
    selected = paths if batch else paths[:1]
    errors = []
    vm.error_occurred.connect(errors.append)
    if staged:
        for path in selected:
            vm.stage_file(path)
    view = panel._staged_list if staged else panel._unstaged_list
    before = (_changes(panel._unstaged_list), _changes(panel._staged_list))
    ignore_before = (root / ".gitignore").read_bytes()
    vm.commit_panel_view_model().select_file(selected[0], staged=staged)
    action = _action(view._build_context_menu(selected))
    assert action is not None
    assert "Keep Local" in action.text()

    action.trigger()
    qtbot.waitUntil(lambda: not vm.is_busy())

    for path in selected:
        assert path not in _changes(panel._unstaged_list)
        assert _changes(panel._staged_list)[path] == FileStatus.DELETED
        assert (root / path).read_bytes() == b"regenerated cache"
        assert _action(panel._staged_list._build_context_menu([path])) is None
    assert len(vm.command_processor().undo_stack_snapshot()) == 1
    assert (root / ".gitignore").read_bytes() == ignore_before
    vm.undo()
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert (_changes(panel._unstaged_list), _changes(panel._staged_list)) == before
    vm.redo()
    qtbot.waitUntil(lambda: not vm.is_busy())
    for path in selected:
        assert path not in _changes(panel._unstaged_list)
        assert _changes(panel._staged_list)[path] == FileStatus.DELETED
        assert (root / path).read_bytes() == b"regenerated cache"
    assert errors == []


def test_menu_only_offers_tracking_for_ignored_tracked_selection(tracking_panel):
    vm, panel, paths, root = tracking_panel
    (root / "hello.txt").write_text("changed\n", encoding="utf-8")
    (root / "untracked.txt").write_text("new\n", encoding="utf-8")
    vm.commit_panel_view_model().refresh_status()
    view = panel._unstaged_list
    assert _action(view._build_context_menu(paths)) is not None
    for selected in (["hello.txt"], ["untracked.txt"], paths + ["hello.txt"]):
        assert _action(view._build_context_menu(selected)) is None
    vm.ignore_pattern("hello.txt")
    assert _action(view._build_context_menu(["hello.txt"])) is not None


def test_rule_removed_while_menu_open_reports_error_without_removing_file(qtbot, tracking_panel):
    vm, panel, paths, root = tracking_panel
    errors = []
    vm.error_occurred.connect(errors.append)
    action = _action(panel._unstaged_list._build_context_menu(paths))
    (root / ".gitignore").write_text("", encoding="utf-8")
    action.trigger()
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert errors and "ignore rule" in errors[-1]
    assert not vm.command_processor().can_undo
    assert all(path in vm.repository_manager().repo.index for path in paths)
    assert all((root / path).read_bytes() == b"regenerated cache" for path in paths)
