"""Preview loading, retry, and bounded rendering behavior."""
from __future__ import annotations

from pathlib import Path
from threading import Event

import pygit2
import pytest
from PySide6.QtCore import Qt
from src.core.exceptions import GitError
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow
from src.ui.widgets.diff_view_widget import DiffViewMode, DiffViewWidget


@pytest.mark.parametrize("mode", list(DiffViewMode))
def test_empty_diff_explanation_is_not_document_content(qtbot, mode):
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_view_mode(mode)
    view.set_diff_pair("", "")
    assert "No diff to display" in view._editor.placeholderText()
    assert "LF/CRLF" in view._editor.placeholderText()
    assert view.toPlainText() == ""
    assert view._editor._line_info == []

    view.set_loading(True)
    assert view._editor.placeholderText() == ""
    view.set_loading(False)
    view.set_diff_pair("@@ -1 +1 @@\n-old\n+new\n", "")
    assert view._editor.placeholderText() == ""
    assert "+new" in view.toPlainText()

    view.set_diff_pair("", "")
    view.clear()
    assert view._editor.placeholderText() == ""


@pytest.mark.parametrize("mode", list(DiffViewMode))
def test_line_endings_only_file_finishes_loading_with_explanation(
    qtbot, tmp_git_repo, tmp_path, mode,
):
    manager = RepositoryManager(str(tmp_git_repo))
    path = "backend/src/agents_ide/domain/graph_preflight.py"
    file = tmp_git_repo / path
    file.parent.mkdir(parents=True)
    file.write_bytes(b"first\nsecond\n")
    repo = manager.repo
    repo.index.add(path)
    repo.index.write()
    signature = pygit2.Signature("tester", "tester@example.com")
    head = repo.create_commit(
        "HEAD", signature, signature, "LF file", repo.index.write_tree(), [],
    )
    repo.config["core.autocrlf"] = True
    file.write_bytes(b"first\r\nsecond\r\n")
    index_path = Path(repo.path) / "index"
    index_before = index_path.read_bytes()
    window = MainWindow(config_path=tmp_path / "config.json")
    qtbot.addWidget(window)
    window.show()
    qtbot.wait(10)  # Finish deferred window restoration before binding the repository.
    window.set_repository(manager)
    main_vm = window.main_view_model()
    main_vm.stop_worktree_refresh()
    main_vm.select_commit("WIP")
    vm = main_vm.commit_panel_view_model()
    view = window._diff_view
    view.set_view_mode(mode)
    errors = []
    vm.error_occurred.connect(errors.append)
    try:
        assert path in vm.unstaged_paths()
        files = window._right_panel._commit_input._unstaged_list
        qtbot.wait(10)  # Lay out the newly visible WIP page before clicking its first row.
        index = files.model().index(0, 0)
        with qtbot.waitSignal(vm.diff_pair_ready):
            qtbot.mouseClick(
                files.viewport(), Qt.MouseButton.LeftButton, pos=files.visualRect(index).center(),
            )
        assert vm.selected_file() == path
        assert vm.current_diff() == ""
        assert window._graph_stack.currentWidget() is view
        assert "LF/CRLF" in view._editor.placeholderText()
        assert not view._loading
        assert not view._loading_panel.isVisible()
        assert view._editor.isEnabled()
        assert not window._requesting_full_document
        assert errors == []
        assert index_path.read_bytes() == index_before
        assert repo.head.target == head
        assert file.read_bytes() == b"first\r\nsecond\r\n"
    finally:
        window.close()
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)
        qtbot.waitUntil(lambda: not main_vm._active_workers)
        qtbot.waitUntil(lambda: main_vm._worktree_refresh_worker is None)
        manager.close()


def test_fast_loading_never_flashes_indicator(qtbot):
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.show()
    view.set_loading(True)
    assert not view._loading_panel.isVisible()
    view.set_loading(False)
    qtbot.wait(200)
    assert not view._loading_panel.isVisible()
    assert view._editor.isEnabled()


def test_slow_file_shows_animation_and_can_be_closed(qtbot, committed_repo, monkeypatch):
    release, started = Event(), Event()

    def read(*_args):
        started.set()
        assert release.wait(5)
        return '@@ -1 +1 @@\n-old\n+late result\n'

    monkeypatch.setattr('src.viewmodels.commit_panel_viewmodel.read_workdir_file_diff', read)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window.main_view_model().commit_panel_view_model()
    try:
        vm.select_file('hello.txt')
        qtbot.waitUntil(started.is_set)
        qtbot.waitUntil(window._diff_view._loading_panel.isVisible)
        progress = window._diff_view._loading_progress
        assert progress.minimum() == progress.maximum() == 0
        assert not window._diff_view._editor.isEnabled()
        window.close()
        assert not window._diff_view._loading_timer.isActive()
        release.set()
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)
        assert 'late result' not in window._diff_view.toPlainText()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)


def test_failed_full_document_stops_loading_and_can_retry(qtbot, committed_repo, monkeypatch):
    calls = []
    compact = '@@ -1 +1 @@\n-old\n+compact\n'

    def read(_repo, _path, _staged, context):
        calls.append(context)
        if context != 3 and calls.count(context) == 1:
            raise GitError('full document failed')
        return compact if context == 3 else compact + ' full context\n'

    monkeypatch.setattr('src.viewmodels.commit_panel_viewmodel.read_workdir_file_diff', read)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window.main_view_model().commit_panel_view_model()
    errors = []
    vm.error_occurred.connect(errors.append)
    vm.select_file('hello.txt')
    qtbot.waitUntil(window._diff_view.has_changes_only)
    window._diff_view.set_view_mode(DiffViewMode.FULL_DOCUMENT)
    qtbot.waitUntil(lambda: bool(errors))
    qtbot.waitUntil(lambda: not window._diff_view._loading)
    assert '+compact' in window._diff_view.toPlainText()
    assert calls == [3, 2**31 - 1]
    window._diff_view.set_view_mode(DiffViewMode.CHANGES_ONLY)
    window._diff_view.set_view_mode(DiffViewMode.FULL_DOCUMENT)
    qtbot.waitUntil(window._diff_view.has_full_document)
    assert 'full context' in window._diff_view.toPlainText()
    qtbot.waitUntil(lambda: not vm._diff_loader._workers)


def test_long_line_is_neutral_and_preserves_following_line_numbers(qtbot):
    view = DiffViewWidget()
    qtbot.addWidget(view)
    text = '@@ -0,0 +1,2 @@\n+' + 'x' * 20_000 + '\n+next\n'
    view.set_diff(text)
    assert 'line too long' in view.toPlainText()
    assert 'x' * 100 not in view.toPlainText()
    next_line = next(line for line in view._editor._line_info if line.text == '+next')
    assert next_line.new_line_number == 2
    banner = next(line for line in view._editor._line_info if 'line too long' in line.text)
    assert banner.old_line_number is None and banner.new_line_number is None


def test_megabyte_line_never_reaches_qt_document(qtbot):
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff('+' + 'x' * 2_000_000)
    assert len(view.toPlainText()) < 1000
    assert 'diff truncated' in view.toPlainText()
