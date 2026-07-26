"""Tests for the asynchronous commit-detail loading API (R4 perf).

``MainViewModel.request_commit_detail`` / ``request_commit_file_diff``
feed the right panel without blocking the UI thread:

* a synchronous VM (``async_enabled=False``, the test default) serves
  results inline — the old synchronous contract is preserved;
* an async VM serves them from a worker thread, toggling
  ``activity_changed`` (the status-bar spinner) around the flight;
* repeat requests are served from the LRU caches and never re-run
  the expensive git work.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _head_sha(mgr: RepositoryManager) -> str:
    return mgr.head_commit.sha


# ----- synchronous VM (test default) ----------------------------------------


def test_request_commit_detail_sync_emits_inline(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    sha = _head_sha(committed_repo)

    with qtbot.waitSignal(vm.commit_detail_ready, timeout=500) as blocker:
        vm.request_commit_detail(sha)

    got_sha, info, changes = blocker.args
    assert got_sha == sha
    assert info is not None and info.sha == sha
    assert [c.path for c in changes] == ["hello.txt"]


def test_request_commit_detail_unknown_sha_emits_none_info(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    with qtbot.waitSignal(vm.commit_detail_ready, timeout=500) as blocker:
        vm.request_commit_detail("0" * 40)

    _sha, info, changes = blocker.args
    assert info is None
    assert changes == []


def test_request_commit_file_diff_sync_emits_text(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    sha = _head_sha(committed_repo)

    with qtbot.waitSignal(vm.commit_file_diff_ready, timeout=500) as blocker:
        vm.request_commit_file_diff(sha, "hello.txt")

    got_sha, path, text, ctx = blocker.args
    assert got_sha == sha
    assert path == "hello.txt"
    assert ctx == 3
    assert "+hello, world" in text


def test_commit_detail_cache_serves_repeats(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """The second request for the same SHA must not recompute."""
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    sha = _head_sha(committed_repo)

    calls = []
    original = MainViewModel._compute_commit_detail

    def spy(mgr, s):
        calls.append(s)
        return original(mgr, s)

    monkeypatch.setattr(MainViewModel, "_compute_commit_detail", staticmethod(spy))

    vm.request_commit_detail(sha)
    vm.request_commit_detail(sha)
    assert calls == [sha]
    # The cache also warmed the branch-attribution memo.
    assert sha in vm._branch_of_commit_cache


def test_file_diff_cache_serves_repeats(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    sha = _head_sha(committed_repo)

    calls = []
    original = MainViewModel._compute_file_diff

    def spy(mgr, s, p, c):
        calls.append((s, p, c))
        return original(mgr, s, p, c)

    monkeypatch.setattr(MainViewModel, "_compute_file_diff", staticmethod(spy))

    vm.request_commit_file_diff(sha, "hello.txt")
    vm.request_commit_file_diff(sha, "hello.txt")
    assert calls == [(sha, "hello.txt", 3)]


# ----- asynchronous VM (production mode) -------------------------------------


def test_request_commit_detail_async_delivers_via_worker(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """With ``async_enabled=True`` nothing is emitted inline; the result
    arrives via the worker and the spinner activity toggles around it."""
    _ensure_app()
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    sha = _head_sha(committed_repo)

    activity: list[bool] = []
    vm.activity_changed.connect(activity.append)

    vm.request_commit_detail(sha)
    # Inline: only the activity start may have fired — no result yet.
    assert activity == [True]

    with qtbot.waitSignal(vm.commit_detail_ready, timeout=5000) as blocker:
        pass
    got_sha, info, changes = blocker.args
    assert got_sha == sha
    assert info is not None and info.sha == sha
    assert [c.path for c in changes] == ["hello.txt"]
    # Spinner switched back off after delivery.
    qtbot.waitUntil(lambda: activity == [True, False], timeout=2000)


def test_request_commit_file_diff_async_delivers_via_worker(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    sha = _head_sha(committed_repo)

    activity: list[bool] = []
    vm.activity_changed.connect(activity.append)

    vm.request_commit_file_diff(sha, "hello.txt")
    assert activity == [True]

    with qtbot.waitSignal(vm.commit_file_diff_ready, timeout=5000) as blocker:
        pass
    got_sha, path, text, ctx = blocker.args
    assert got_sha == sha
    assert path == "hello.txt"
    assert ctx == 3
    assert "+hello, world" in text
    qtbot.waitUntil(lambda: activity == [True, False], timeout=2000)


def test_activity_count_nests_concurrent_requests(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Two overlapping requests must not flip the spinner off early."""
    _ensure_app()
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    sha = _head_sha(committed_repo)

    activity: list[bool] = []
    vm.activity_changed.connect(activity.append)

    vm.request_commit_detail(sha)
    vm.request_commit_file_diff(sha, "hello.txt")
    assert activity == [True]  # nested — still one "on"

    qtbot.waitUntil(lambda: activity == [True, False], timeout=5000)
    assert vm._activity_count == 0


def test_refresh_state_async_routes_to_worker(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Production ``refresh_state`` (app activation) must not run the
    heavy refresh on the UI thread — it delegates to the async loader."""
    _ensure_app()
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)

    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)

    vm.refresh_state()

    # The worker's completion emits ``busy_changed(False)`` (twice —
    # once by the load-finished hook, once by the worker-lifespan
    # hook), so assert on the first/last values rather than equality.
    qtbot.waitUntil(lambda: len(busy) >= 2, timeout=5000)
    assert busy[0] is True
    assert busy[-1] is False
    assert not vm.is_busy()
