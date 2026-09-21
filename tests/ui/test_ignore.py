"""Real Ignore menu actions update the file list immediately, including Undo/Redo."""
from pathlib import Path

import pytest
from src.ui.widgets.commit_panel import CommitPanel
from src.viewmodels.main_viewmodel import MainViewModel


def _paths(view):
    model = view.model()
    return {model.change_at(row).path for row in range(model.rowCount())}


@pytest.mark.parametrize("ending", [b"", b"\n", b"\r\n"])
@pytest.mark.parametrize("directory", ["cache/nested/", "cache/"])
def test_ignore_directory_menu_updates_unstaged_immediately(
    qtbot, committed_repo, tmp_path, ending, directory,
):
    root = Path(committed_repo.path)
    (root / ".gitignore").write_bytes(b"*.log" + ending)
    files = {
        "cache/nested/first.txt", "cache/nested/deeper/second.txt",
        "cache/sibling.txt", "cache-other/keep.txt", "outside.txt",
    }
    for path in files:
        full_path = root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(b"untracked\n")
    index_before = (Path(committed_repo.repo.path) / "index").read_bytes()
    vm = MainViewModel(config_path=tmp_path / "config.json")
    vm.set_repository(committed_repo)
    panel_vm = vm.commit_panel_view_model()
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    errors = []
    vm.error_occurred.connect(errors.append)
    before = files | {".gitignore"}
    assert _paths(panel._unstaged_list) == before
    selected = "cache/nested/first.txt"
    panel_vm.select_file(selected)
    menu = panel._unstaged_list._build_context_menu([selected])
    ignore_action = next(action for action in menu.actions() if action.text() == "Ignore")
    ignore_menu = ignore_action.menu()
    action = next(
        action for action in ignore_menu.actions() if action.text() == f"Ignore /{directory}"
    )

    action.trigger()

    expected = {path for path in before if not path.startswith(directory)}
    assert _paths(panel._unstaged_list) == expected
    assert set(panel_vm.unstaged_paths()) == expected
    assert panel._unstaged_expander.text().strip() == f"Unstaged Files ({len(expected)})"
    assert panel_vm.selected_file() is None
    assert (root / ".gitignore").read_text(encoding="utf-8").splitlines() == ["*.log", directory]

    vm.undo()
    assert _paths(panel._unstaged_list) == before
    vm.redo()
    assert _paths(panel._unstaged_list) == expected
    assert all((root / path).read_bytes() == b"untracked\n" for path in files)
    assert (Path(committed_repo.repo.path) / "index").read_bytes() == index_before
    assert errors == []


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("separator", ["/", "\\"])
@pytest.mark.parametrize(
    ("action", "expected"), [("ignore_dir", "cache/nested/"), ("ignore_parent_dir", "cache/")],
)
def test_ignore_menu_directory_routing(
    qtbot, tmp_path, monkeypatch, staged, separator, action, expected,
):
    vm = MainViewModel(config_path=tmp_path / "config.json")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    patterns = []
    monkeypatch.setattr(vm, "ignore_pattern", patterns.append)
    view = panel._staged_list if staged else panel._unstaged_list
    view.context_action_requested.emit(action, separator.join(["cache", "nested", "first.txt"]))
    assert patterns == [expected]
