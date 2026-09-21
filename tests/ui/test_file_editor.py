"""Editing staged/unstaged files through the actual window controls and shortcuts."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from src.ui.main_window import MainWindow


@pytest.fixture
def editing_window(qtbot, committed_repo, tmp_path):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_bytes(b"staged version\r\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    (root / "hello.txt").write_bytes(b"working version\r\n")
    (root / "other.txt").write_bytes(b"other\n")
    window = MainWindow(config_path=tmp_path / "config.json")
    window.show()
    window.set_repository(committed_repo)
    vm = window.main_view_model()
    vm.stop_worktree_refresh()
    vm.select_commit("WIP")
    yield window
    vm.commit_panel_view_model().cancel_file_editing()
    window.close()
    qtbot.waitUntil(lambda: not vm._active_workers, timeout=10000)
    qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None, timeout=10000)
    qtbot.waitUntil(lambda: not vm.commit_panel_view_model()._diff_loader._workers, timeout=10000)
    qtbot.waitUntil(lambda: not vm.commit_panel_view_model().file_editor._loader._workers)
    window.deleteLater()


def _open_editor(qtbot, window, staged=False):
    panel = window.main_view_model().commit_panel_view_model()
    panel.select_file("hello.txt", staged=staged)
    view = window._diff_view
    qtbot.waitUntil(view.has_changes_only, timeout=5000)
    view._edit_button.click()
    qtbot.waitUntil(lambda: panel.file_editor.active and not panel.file_editor.loading)
    return view, panel


def _replace_text(editor, text):
    editor.selectAll()
    editor.insertPlainText(text)


@pytest.mark.parametrize("staged", [False, True])
def test_save_cancel_and_toolbar_history(qtbot, editing_window, staged):
    window = editing_window
    vm = window.main_view_model()
    root = Path(vm.repository_manager().path)
    index_path = Path(vm.repository_manager().repo.path) / "index"
    index_before = index_path.read_bytes()
    view, panel = _open_editor(qtbot, window, staged)
    assert view._file_editor.toPlainText() == "working version\n"
    assert view._file_editor.isVisible()
    assert not view._editor.isVisible()
    assert not view._save_button.isEnabled()
    assert view._cancel_button.isVisible()
    assert not window._action_undo.isEnabled()

    _replace_text(view._file_editor, "saved text\n")
    assert view._save_button.isEnabled()
    window._action_undo.trigger()
    assert view._file_editor.toPlainText() == "working version\n"
    assert not view._save_button.isEnabled()
    assert window._action_redo.isEnabled()
    window._action_redo.trigger()
    assert view._file_editor.toPlainText() == "saved text\n"
    view._save_button.click()
    assert (root / "hello.txt").read_bytes() == b"saved text\r\n"
    assert not view._save_button.isEnabled()
    assert view.is_editing()
    assert "hello.txt" in panel.staged_files()
    assert "hello.txt" in panel.unstaged_paths()

    window._action_undo.trigger()
    assert view._save_button.isEnabled()
    assert (root / "hello.txt").read_bytes() == b"saved text\r\n"
    window._action_redo.trigger()
    assert not view._save_button.isEnabled()
    _replace_text(view._file_editor, "discard this draft\n")
    view._cancel_button.click()
    assert not view.is_editing()
    assert view._editor.isVisible()
    assert (root / "hello.txt").read_bytes() == b"saved text\r\n"
    assert window._action_undo.isEnabled()
    window._action_undo.trigger()
    assert (root / "hello.txt").read_bytes() == b"working version\r\n"
    window._action_redo.trigger()
    assert (root / "hello.txt").read_bytes() == b"saved text\r\n"
    assert index_path.read_bytes() == index_before


def test_ctrl_s_is_scoped_to_editor_and_text_undo_stays_local(qtbot, editing_window):
    window = editing_window
    view, panel = _open_editor(qtbot, window)
    root = Path(window.main_view_model().repository_manager().path)
    window.activateWindow()
    view._file_editor.setFocus()
    _replace_text(view._file_editor, "shortcut save\n")
    qtbot.keyClick(view._file_editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert (root / "hello.txt").read_bytes() == b"shortcut save\r\n"
    assert not panel.file_editor.dirty
    qtbot.keyClick(view._file_editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert panel.file_editor.dirty
    assert (root / "hello.txt").read_bytes() == b"shortcut save\r\n"
    qtbot.keyClick(view._file_editor, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert not panel.file_editor.dirty
    _replace_text(view._file_editor, "unsaved\n")
    summary = window._right_panel._commit_input._summary
    summary.setFocus()
    qtbot.keyClick(summary, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert (root / "hello.txt").read_bytes() == b"shortcut save\r\n"


@pytest.mark.parametrize("destination", ["file", "staged", "changes", "document", "edit", "commit"])
def test_navigation_autosaves_and_returns_to_diff(qtbot, editing_window, destination):
    window = editing_window
    view, panel = _open_editor(qtbot, window)
    _replace_text(view._file_editor, "autosaved\n")
    if destination == "file":
        panel.select_file("other.txt")
    elif destination == "staged":
        panel.select_file("hello.txt", staged=True)
    elif destination == "commit":
        vm = window.main_view_model()
        vm.select_commit(str(vm.repository_manager().repo.head.target))
    else:
        getattr(view, f"_{destination}_button").click()
    assert not view.is_editing()
    assert not view._save_button.isVisible()
    root = Path(window.main_view_model().repository_manager().path)
    assert (root / "hello.txt").read_bytes() == b"autosaved\r\n"
    assert window.main_view_model().command_processor().can_undo


def test_refresh_does_not_replace_edit_buffer(qtbot, editing_window):
    window = editing_window
    view, panel = _open_editor(qtbot, window)
    _replace_text(view._file_editor, "draft\n")
    cursor = view._file_editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    view._file_editor.setTextCursor(cursor)
    panel.refresh_status()
    panel.recompute_selected_diff()
    window.main_view_model().refresh_state()
    qtbot.waitUntil(lambda: not window.main_view_model().is_busy(), timeout=5000)
    assert view.is_editing()
    assert view._file_editor.toPlainText() == "draft\n"
    assert view._file_editor.textCursor().position() == 0


def test_cancel_without_saving_keeps_disk_and_history(qtbot, editing_window):
    window = editing_window
    view, panel = _open_editor(qtbot, window)
    _replace_text(view._file_editor, "discard\n")
    view._cancel_button.click()
    root = Path(window.main_view_model().repository_manager().path)
    assert (root / "hello.txt").read_bytes() == b"working version\r\n"
    assert not panel.file_editor.active
    assert not window.main_view_model().command_processor().can_undo


def test_failed_save_keeps_editor_and_cancel_does_not_overwrite_external_file(
    qtbot, editing_window,
):
    window = editing_window
    view, panel = _open_editor(qtbot, window)
    root = Path(window.main_view_model().repository_manager().path)
    _replace_text(view._file_editor, "draft\n")
    (root / "hello.txt").write_bytes(b"external\n")
    errors = []
    window.main_view_model().error_occurred.connect(errors.append)
    view._save_button.click()
    assert panel.file_editor.dirty
    view._document_button.click()
    assert view.is_editing()
    assert view._edit_button.isChecked()
    assert not view._document_button.isChecked()
    window.close()
    assert window.isVisible()
    assert view._file_editor.toPlainText() == "draft\n"
    assert errors
    view._cancel_button.click()
    assert (root / "hello.txt").read_bytes() == b"external\n"
    assert not view.is_editing()


def test_close_autosaves(qtbot, editing_window):
    window = editing_window
    view, panel = _open_editor(qtbot, window)
    root = Path(window.main_view_model().repository_manager().path)
    _replace_text(view._file_editor, "saved on close\n")
    window.close()
    assert not panel.file_editor.active
    assert (root / "hello.txt").read_bytes() == b"saved on close\r\n"


@pytest.mark.parametrize("contents", [b"binary\0file", b"\x80invalid utf8"])
def test_non_text_file_stays_in_diff(qtbot, editing_window, contents):
    window = editing_window
    vm = window.main_view_model()
    panel = vm.commit_panel_view_model()
    root = Path(vm.repository_manager().path)
    (root / "other.txt").write_bytes(contents)
    panel.select_file("other.txt")
    errors = []
    vm.error_occurred.connect(errors.append)
    window._diff_view._edit_button.click()
    qtbot.waitUntil(lambda: not panel.file_editor.loading)
    assert errors
    assert not window._diff_view.is_editing()
    assert (root / "other.txt").read_bytes() == contents


def test_leaving_during_file_read_ignores_late_editor_result(qtbot, editing_window, monkeypatch):
    import threading

    import src.viewmodels.file_editor_viewmodel as module

    window = editing_window
    panel = window.main_view_model().commit_panel_view_model()
    panel.select_file("hello.txt")
    read = module.read_text_file
    started = threading.Event()
    release = threading.Event()

    def slow_read(*args):
        result = read(*args)
        started.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(module, "read_text_file", slow_read)
    view = window._diff_view
    view._edit_button.click()
    try:
        qtbot.waitUntil(started.is_set)
        assert view._file_editor.isReadOnly()
        panel.select_file("other.txt")
        assert not view.is_editing()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not panel.file_editor._loader._workers)
    assert not view.is_editing()
    assert panel.selected_file() == "other.txt"
