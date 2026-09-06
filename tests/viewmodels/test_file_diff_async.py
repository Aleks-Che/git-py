"""Slow preview reads must leave Qt responsive and never replace a newer selection."""
from __future__ import annotations

from threading import Event, get_ident

import pytest
from PySide6.QtCore import QTimer
from src.core.exceptions import GitError
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.mark.parametrize("staged", [False, True])
def test_wip_diff_and_full_document_run_off_gui(qtbot, committed_repo, monkeypatch, staged):
    vm = CommitPanelViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    release = Event()
    calls = []
    gui_thread = get_ident()
    delivered = []
    vm.diff_pair_ready.connect(lambda *_: delivered.append(get_ident()))

    def read(repo_path, path, side, context):
        calls.append((get_ident(), repo_path, path, side, context))
        assert release.wait(5)
        return f"@@ -1 +1 @@\n-old\n+new {context}\n"

    monkeypatch.setattr('src.viewmodels.commit_panel_viewmodel.read_workdir_file_diff', read)
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        vm.select_file('hello.txt', staged=staged)
        qtbot.waitUntil(lambda: len(ticks) >= 3)
        assert vm.current_diff() == ''
        release.set()
        qtbot.waitUntil(lambda: bool(vm.current_diff()))
        vm.request_full_document()
        qtbot.waitUntil(lambda: len(delivered) == 2)
        assert all(call[0] != gui_thread for call in calls)
        assert [call[3:] for call in calls] == [(staged, 3), (staged, 2**31 - 1)]
        assert delivered == [gui_thread, gui_thread]
    finally:
        timer.stop()
        release.set()
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)


@pytest.mark.parametrize('stale_error', [False, True])
def test_latest_wip_selection_wins_and_pending_clicks_are_coalesced(
    qtbot, committed_repo, monkeypatch, stale_error,
):
    vm = CommitPanelViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    release = Event()
    calls, results, errors = [], [], []
    vm.diff_ready.connect(results.append)
    vm.error_occurred.connect(errors.append)

    def read(_repo, path, _staged, _context):
        calls.append(path)
        if path in ('first', 'second'):
            assert release.wait(5)
            if stale_error:
                raise GitError('obsolete read failed')
        return path

    monkeypatch.setattr('src.viewmodels.commit_panel_viewmodel.read_workdir_file_diff', read)
    try:
        vm.select_file('first')
        vm.select_file('second')
        qtbot.waitUntil(lambda: len(calls) == 2)
        for number in range(20):
            vm.select_file(f'pending-{number}')
        assert len(vm._diff_loader._workers) == 2
        release.set()
        qtbot.waitUntil(lambda: vm.current_diff() == 'pending-19')
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)
        assert sorted(calls) == ['first', 'pending-19', 'second']
        assert results == ['pending-19']
        assert errors == []
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)


@pytest.mark.parametrize('cancel', ['deselect', 'repository', 'close'])
def test_wip_cancel_drops_late_result(qtbot, committed_repo, monkeypatch, cancel):
    vm = CommitPanelViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    started, release = Event(), Event()
    results, busy = [], []
    vm.diff_ready.connect(results.append)
    vm.diff_loading_changed.connect(busy.append)

    def read(*_args):
        started.set()
        assert release.wait(5)
        return 'obsolete'

    monkeypatch.setattr('src.viewmodels.commit_panel_viewmodel.read_workdir_file_diff', read)
    try:
        vm.select_file('hello.txt')
        qtbot.waitUntil(started.is_set)
        if cancel == 'deselect':
            vm.select_file(None)
        elif cancel == 'repository':
            vm.set_repository(None)
        else:
            vm.cancel_diff_loading()
        assert busy[-1] is False
        release.set()
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)
        assert 'obsolete' not in results
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm._diff_loader._workers)


def test_commit_diff_deduplicates_and_cancels_inflight_reads(qtbot, committed_repo, monkeypatch):
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    release = Event()
    calls, results = [], []
    vm.commit_file_diff_ready.connect(lambda *args: results.append(args))

    def read(manager, sha, path, context):
        assert manager is not committed_repo
        calls.append(path)
        if path == 'slow':
            assert release.wait(5)
        return path

    monkeypatch.setattr(MainViewModel, '_compute_file_diff', staticmethod(read))
    try:
        vm.request_commit_file_diff('sha', 'slow')
        vm.request_commit_file_diff('sha', 'slow')
        qtbot.waitUntil(lambda: len(calls) == 1)
        vm.request_commit_file_diff('sha', 'new')
        qtbot.waitUntil(lambda: len(results) == 1)
        assert results[0][2] == 'new'
        release.set()
        qtbot.waitUntil(lambda: not vm._commit_diff_loader._workers)
        assert len(results) == 1
        assert vm._activity_count == 0
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm._commit_diff_loader._workers)
