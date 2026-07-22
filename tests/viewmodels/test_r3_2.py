"""Stage R3.2 — UI-thread blocking regression tests.

Covers:
* P3 — Sync ``fetch_and_checkout_remote_branch`` /
  ``reset_local_branch_to_remote`` no longer call
  ``QApplication.processEvents`` (R3.2 L).
* P4 — Diff text is computed eagerly only for changes-only; the
  full-document variant is built lazily via
  :meth:`CommitPanelViewModel.request_full_document` /
  :meth:`CommitDetailPanel.request_full_document` (R3.2 M).
* P5 — ``stage_all_unstaged`` / ``unstage_all_staged`` issue a
  single trailing ``refresh_status`` (one ``file_changes_changed``
  emission) regardless of the number of files (R3.2 M).
* P7 — :class:`GraphViewModel` precomputes the branch-priority
  cache on ``refresh_graph``; the widget reads from the cache
  instead of walking HEAD's parent chain on paint (R3.2 M).
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QEventLoop
from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.graph_viewmodel import GraphViewModel
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "t@example.com", int(time.time()), 0)


@pytest.fixture
def origin_and_clone(tmp_path: Path) -> tuple[RepositoryManager, RepositoryManager]:
    """Bare origin with one commit + a working clone (branch on HEAD)."""
    origin_path = tmp_path / "origin.git"
    clone_path = tmp_path / "clone"
    pygit2.init_repository(str(origin_path), bare=True)
    pygit2.clone_repository(str(origin_path), str(clone_path))
    sig = _sig()
    (clone_path / "f.txt").write_text("x\n")
    repo = pygit2.Repository(str(clone_path))
    repo.index.add("f.txt")
    repo.index.write()
    tree = repo.index.write_tree()
    repo.create_commit("HEAD", sig, sig, "init", tree, [])
    from src.core.operations import push as core_push

    branch = repo.head.shorthand
    core_push(repo, "origin", f"refs/heads/{branch}")
    origin = RepositoryManager(str(origin_path))
    clone = RepositoryManager(str(clone_path))
    return origin, clone


# ---------------------------------------------------------------------------
# P3 — fetch_and_checkout_remote_branch / reset_local_branch_to_remote must
#      not pump QApplication.processEvents from the VM.
# ---------------------------------------------------------------------------


def test_fetch_and_checkout_remote_branch_does_not_process_events(
    monkeypatch, origin_and_clone,
) -> None:
    """R3.2 P3: the sync fetch path must not pump the Qt event loop.

    Spying on ``QApplication.processEvents`` records any call the VM
    makes while dispatching the fetch.  The pre-R3.2 implementation
    called ``QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)``
    to drain queued events; R3.2 removed that call so the UI thread
    cannot be pumped in the middle of a long network round-trip.
    """
    _ensure_app()
    _origin, clone = origin_and_clone
    branch = next(b.name for b in clone.branches if b.is_head)

    calls: list[QEventLoop.ProcessEventsFlag] = []
    original = QApplication.processEvents

    def spy(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("flags", -1))
        return original(*args, **kwargs)

    monkeypatch.setattr(QApplication, "processEvents", staticmethod(spy))

    vm = MainViewModel(async_enabled=False)
    vm.set_repository(clone)
    calls.clear()
    vm.fetch_and_checkout_remote_branch(f"origin/{branch}")
    assert calls == [], (
        f"R3.2 P3: VM called processEvents during sync fetch: {calls}"
    )


def test_reset_local_branch_to_remote_does_not_process_events(
    monkeypatch, origin_and_clone,
) -> None:
    """R3.2 P3: same contract for the destructive reset verb."""
    _ensure_app()
    _origin, clone = origin_and_clone
    branch = next(b.name for b in clone.branches if b.is_head)

    calls: list[QEventLoop.ProcessEventsFlag] = []
    original = QApplication.processEvents

    def spy(*args, **kwargs):
        calls.append(args[0] if args else kwargs.get("flags", -1))
        return original(*args, **kwargs)

    monkeypatch.setattr(QApplication, "processEvents", staticmethod(spy))

    vm = MainViewModel(async_enabled=False)
    vm.set_repository(clone)
    calls.clear()
    vm.reset_local_branch_to_remote(f"origin/{branch}")
    assert calls == [], (
        f"R3.2 P3: VM called processEvents during sync reset: {calls}"
    )


# ---------------------------------------------------------------------------
# P4 — diff is computed lazily for the full-document variant.
# ---------------------------------------------------------------------------


def test_select_file_does_not_emit_full_document_eagerly(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """R3.2 P4: the eager ``diff_pair_ready`` carries an empty
    ``full_document``.  The expensive 2^31-context variant is only
    computed on an explicit :meth:`request_full_document` call.
    """
    (Path(committed_repo.path) / "hello.txt").write_text("changed\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    with qtbot.waitSignal(vm.diff_pair_ready, timeout=500) as blocker:
        vm.select_file("hello.txt")
    changes_only, full_document_initial = blocker.args
    assert changes_only
    assert full_document_initial == ""

    with qtbot.waitSignal(vm.diff_pair_ready, timeout=2000) as blocker:
        vm.request_full_document()
    changes_only2, full_document = blocker.args
    assert changes_only2 == changes_only
    assert full_document
    assert len(full_document) >= len(changes_only)


# ---------------------------------------------------------------------------
# P5 — stage_all_unstaged / unstage_all_staged batch the refresh.
# ---------------------------------------------------------------------------


def test_stage_all_unstaged_emits_single_file_changes_changed(
    qtbot, tmp_git_repo: Path,
) -> None:
    """R3.2 P5: the batch verb issues exactly one
    ``file_changes_changed`` emission regardless of the file count.
    """
    from PySide6.QtWidgets import QApplication as _App

    _ensure_app()
    # Three untracked files in the worktree.
    for i in range(3):
        (tmp_git_repo / f"f{i}.txt").write_text(f"content {i}\n")
    vm = MainViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))
    cp_vm = vm.commit_panel_view_model()

    emissions: list[None] = []
    cp_vm.file_changes_changed.connect(lambda: emissions.append(None))
    vm.stage_all_unstaged()
    _App.processEvents()
    assert len(emissions) == 1, (
        f"R3.2 P5: expected exactly one file_changes_changed emission; "
        f"got {len(emissions)}"
    )


def test_unstage_all_staged_emits_single_file_changes_changed(
    qtbot, tmp_git_repo: Path,
) -> None:
    """R3.2 P5: same contract for the reverse batch."""
    from PySide6.QtWidgets import QApplication as _App

    _ensure_app()
    # Three tracked, modified files staged first.
    for i in range(3):
        (tmp_git_repo / f"f{i}.txt").write_text(f"initial {i}\n")
    repo = pygit2.Repository(str(tmp_git_repo))
    sig = _sig()
    repo.index.add_all(["f0.txt", "f1.txt", "f2.txt"])
    repo.index.write()
    tree = repo.index.write_tree()
    repo.create_commit("refs/heads/main", sig, sig, "init", tree, [])
    mgr = RepositoryManager(str(tmp_git_repo))
    for i in range(3):
        (tmp_git_repo / f"f{i}.txt").write_text(f"v2 {i}\n")
        mgr.repo.index.add(f"f{i}.txt")
    mgr.repo.index.write()

    vm = MainViewModel()
    vm.set_repository(mgr)
    cp_vm = vm.commit_panel_view_model()

    emissions: list[None] = []
    cp_vm.file_changes_changed.connect(lambda: emissions.append(None))
    vm.unstage_all_staged()
    _App.processEvents()
    assert len(emissions) == 1, (
        f"R3.2 P5: expected exactly one file_changes_changed emission; "
        f"got {len(emissions)}"
    )


# ---------------------------------------------------------------------------
# P7 — branch-priority cache lives on the VM.
# ---------------------------------------------------------------------------


def test_branch_priority_cache_populated_on_refresh_graph(
    committed_repo: RepositoryManager,
) -> None:
    """R3.2 P7: ``branch_priority_for`` is populated after ``refresh_graph``."""
    _ensure_app()
    vm = GraphViewModel(history_limit=100)
    vm.set_repository(committed_repo)
    # After ``refresh_graph`` (invoked by ``set_repository``) the cache
    # must contain the HEAD branch at bucket 0.
    priority = vm.branch_priority_for("main")
    assert priority[0] == 0  # HEAD wins


def test_branch_priority_cache_returns_safe_default_for_unknown() -> None:
    """R3.2 P7: unknown branches get ``(3, name)`` rather than raising."""
    _ensure_app()
    vm = GraphViewModel(history_limit=100)
    # No repository bound — cache is empty.
    assert vm.branch_priority_for("anything") == (3, "anything")
    assert vm.branch_priority_for("") == (3, "")
