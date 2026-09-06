"""Preview loading, retry, and bounded rendering behavior."""
from __future__ import annotations

from threading import Event

from src.core.exceptions import GitError
from src.ui.main_window import MainWindow
from src.ui.widgets.diff_view_widget import DiffViewMode, DiffViewWidget


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
