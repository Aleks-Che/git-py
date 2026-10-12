"""End-to-end conflict-workflow tests (review 2026-09-05, findings 5/8/9).

Covers the full path from a MainWindow merge action through the
conflict panel, per-file resolution and merge completion — plus the
Undo/Redo round-trip of a merge that was completed after a conflict
(finding 8) and a complete conflicted rebase cycle (finding 9).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from threading import Event

import pygit2
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.core.exceptions import GitError
from src.core.operations import (
    checkout_branch,
    commit_changes,
    complete_merge,
    create_branch,
    is_merge_in_progress,
    is_rebase_in_progress,
)
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow
from src.ui.widgets.conflict_panel import ConflictPanel
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _build_conflict(
    mgr: RepositoryManager, paths: tuple[str, ...] = ("hello.txt",),
) -> None:
    """Fork ``feature``/``main`` with conflicting files (default: ``hello.txt``).

    Leaves HEAD on ``main``; ``merge feature`` conflicts on
    each requested path.
    """
    create_branch(mgr, "feature")
    checkout_branch(mgr, "feature")
    for path in paths:
        (Path(mgr.path) / path).write_text("feature hi\n")
        mgr.repo.index.add(path)
    mgr.repo.index.write()
    commit_changes(mgr, "feature: hi", stage_all=False)
    checkout_branch(mgr, "main")
    for path in paths:
        (Path(mgr.path) / path).write_text("main hi\n")
        mgr.repo.index.add(path)
    mgr.repo.index.write()
    commit_changes(mgr, "main: hi", stage_all=False)


def _head_commit(mgr: RepositoryManager) -> pygit2.Commit:
    return mgr.repo[mgr.repo.head.target]


# ----- finding 5: the conflict UI is wired end to end ------------------------


def test_conflict_panel_shows_files_and_merge_completes(
    qtbot,
    committed_repo: RepositoryManager,
) -> None:
    """MainWindow merge → conflict panel → resolve → completed merge."""
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm

    vm.merge_branch("feature")

    panel = window._conflict_panel
    assert not panel.isHidden()
    assert panel.operation() == "merge"
    assert panel._files.count() == 1
    assert panel._files.item(0).text() == "hello.txt"

    vm.resolve_conflict("hello.txt", "resolved\n")

    assert vm.conflict_state() is None
    assert panel.isHidden()
    assert not is_merge_in_progress(committed_repo)
    head = _head_commit(committed_repo)
    assert len(head.parent_ids) == 2  # real merge commit
    assert dict(committed_repo.repo.status()) == {}
    window.close()


def test_conflict_editor_routes_to_vm(
    qtbot,
    committed_repo: RepositoryManager,
) -> None:
    """A click on the right opens an embedded editor and saves through the VM."""
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    panel = window._conflict_panel
    qtbot.mouseClick(panel._files.viewport(), Qt.MouseButton.LeftButton,
                     pos=panel._files.visualItemRect(panel._files.item(0)).center())
    editor = window._conflict_editor
    assert window._graph_stack.currentWidget() is editor
    assert not editor.isWindow()
    assert QApplication.activeModalWidget() is None
    editor.set_result_text("resolved via editor\n")
    editor._on_mark_resolved()

    assert vm.conflict_state() is None
    assert not is_merge_in_progress(committed_repo)
    assert (Path(committed_repo.path) / "hello.txt").read_text() == (
        "resolved via editor\n"
    )
    assert len(_head_commit(committed_repo).parent_ids) == 2
    window.close()


@pytest.mark.parametrize("selected_row", [None, 1])
def test_single_click_opens_editor_with_multiple_conflicts(
    qtbot, committed_repo, selected_row,
) -> None:
    """Every conflicting file is directly accessible from the right panel."""
    paths = ("hello.txt", "second.txt", "third.txt")
    _build_conflict(committed_repo, paths)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    window._main_vm.merge_branch("feature")
    panel = window._conflict_panel
    assert panel._files.count() == 3
    if selected_row is not None:
        panel._files.setCurrentRow(selected_row)
    expected_path = panel._files.item(selected_row or 0).text()
    item = panel._files.item(selected_row or 0)
    qtbot.mouseClick(panel._files.viewport(), Qt.MouseButton.LeftButton,
                     pos=panel._files.visualItemRect(item).center())
    assert window._conflict_editor.viewmodel.snapshot.path == expected_path
    assert window._graph_stack.currentWidget() is window._conflict_editor
    assert window._right_panel._stack.currentWidget() is panel
    assert QApplication.activeModalWidget() is None
    assert is_merge_in_progress(committed_repo)
    assert window._main_vm.conflict_state()["conflicting_paths"] == list(paths)
    window.close()


@pytest.mark.parametrize("change", ["worktree", "index", "head"])
def test_resolution_rejects_external_changes(committed_repo, change):
    from src.core.conflict_resolution import load_conflict

    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    snapshot = load_conflict(committed_repo, "hello.txt")
    target = Path(committed_repo.path) / "hello.txt"
    if change == "worktree":
        target.write_bytes(b"external edit\n")
    elif change == "index":
        committed_repo.repo.index.add("hello.txt")
        committed_repo.repo.index.write()
    else:
        create_branch(committed_repo, "other")
        committed_repo.repo.set_head("refs/heads/other")
    expected = target.read_bytes()
    errors = []
    vm.error_occurred.connect(errors.append)
    assert not vm.resolve_conflict_bytes("hello.txt", b"AI draft\n", snapshot=snapshot)
    assert errors
    assert target.read_bytes() == expected


def test_resolution_routes_through_command_processor_and_preserves_crlf(
    committed_repo, monkeypatch,
):
    from src.core.conflict_resolution import load_conflict
    from src.viewmodels.commands import ResolveConflictCommand

    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    snapshot = load_conflict(committed_repo, "hello.txt")
    commands = []
    execute = vm.command_processor().execute

    def record(command):
        commands.append(command)
        return execute(command)

    monkeypatch.setattr(vm.command_processor(), "execute", record)
    assert vm.resolve_conflict_bytes("hello.txt", b"result\r\n", snapshot=snapshot)
    assert isinstance(commands[0], ResolveConflictCommand)
    assert (Path(committed_repo.path) / "hello.txt").read_bytes() == b"result\r\n"
    assert vm.conflict_state() is None


def test_conflict_abort_rolls_back_merge(
    qtbot,
    committed_repo: RepositoryManager,
) -> None:
    _build_conflict(committed_repo)
    pre_merge_sha = committed_repo.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    window._on_conflict_abort("merge")

    assert vm.conflict_state() is None
    assert not is_merge_in_progress(committed_repo)
    assert committed_repo.head_commit.sha == pre_merge_sha
    assert dict(committed_repo.repo.status()) == {}
    assert window._conflict_panel.isHidden()
    window.close()


# ----- finding 8: a merge completed after conflict is undoable ----------------


def test_resolved_merge_undo_redo_roundtrip(
    committed_repo: RepositoryManager,
) -> None:
    """execute → conflict → resolve → complete → Undo ×2 → Redo.

    Finding 8: undoing a merge that was finished via conflict
    resolution used to be a no-op.  Now the completion is its own
    command on the stack, so Undo rewinds the merge commit and Redo
    re-attempts the merge (landing back in the conflict UI).
    """
    _ensure_app()
    _build_conflict(committed_repo)
    pre_merge_sha = committed_repo.head_commit.sha

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    vm.resolve_conflict("hello.txt", "resolved\n")
    assert vm.conflict_state() is None
    merge_sha = committed_repo.head_commit.sha
    assert merge_sha != pre_merge_sha
    assert len(_head_commit(committed_repo).parent_ids) == 2

    # Undo #1: the completion command — merge commit rewound.
    vm.undo()
    assert committed_repo.head_commit.sha == pre_merge_sha
    assert not is_merge_in_progress(committed_repo)
    assert dict(committed_repo.repo.status()) == {}

    # Undo #2: the original merge command — nothing left to abort
    # (the merge is already gone), HEAD stays consistent.
    vm.undo()
    assert committed_repo.head_commit.sha == pre_merge_sha

    # Redo re-attempts the merge, which conflicts again: the VM must
    # resurface the conflict state (not pretend success).
    vm.redo()
    state = vm.conflict_state()
    assert state is not None
    assert state["operation"] == "merge"
    assert "hello.txt" in state["conflicting_paths"]

    # Resolving again completes the merge; the stale redo entry for
    # the old completion is gone (execute clears the redo stack).
    vm.resolve_conflict("hello.txt", "resolved again\n")
    assert vm.conflict_state() is None
    assert len(_head_commit(committed_repo).parent_ids) == 2
    assert not is_merge_in_progress(committed_repo)


# ----- finding 9: a finished rebase leaves the conflict state ----------------


def test_rebase_conflict_full_cycle_clears_state(
    committed_repo: RepositoryManager,
) -> None:
    """conflict → resolve → continue → done; VM state matches Git.

    Finding 9: ``complete_rebase_continue`` used to return ``True``
    for "finished", which the VM read as "more conflicts" — the
    conflict state stayed up with an empty file list after Git had
    already finished the rebase.  The entry paths also used to report
    an empty conflict list instead of the real index conflicts.
    """
    _ensure_app()
    _build_conflict(committed_repo)
    checkout_branch(committed_repo, "feature")
    feature_before = committed_repo.head_commit.sha

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.rebase_branch("main")

    state = vm.conflict_state()
    assert state is not None
    assert state["operation"] == "rebase"
    # Real conflicting paths are reported (was: always empty).
    assert state["conflicting_paths"] == ["hello.txt"]

    vm.resolve_conflict("hello.txt", "resolved\n")

    assert vm.conflict_state() is None
    assert not is_rebase_in_progress(committed_repo)
    assert committed_repo.repo.head.shorthand == "feature"
    history = [c.message.strip() for c in committed_repo.get_history(max_count=10)]
    assert "main: hi" in history  # rebased on top of main
    assert committed_repo.head_commit.sha != feature_before
    assert dict(committed_repo.repo.status()) == {}


# ----- continue_operation: externally resolved files --------------------------


def test_continue_operation_completes_externally_resolved_merge(
    committed_repo: RepositoryManager,
) -> None:
    """User resolves with an external editor, then presses Continue."""
    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    # External resolution: edit + ``git add`` outside the app.
    (Path(committed_repo.path) / "hello.txt").write_text("external\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()

    vm.continue_operation()

    assert vm.conflict_state() is None
    assert not is_merge_in_progress(committed_repo)
    assert len(_head_commit(committed_repo).parent_ids) == 2


def test_continue_operation_reports_remaining_conflicts(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")

    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.continue_operation()

    assert errors
    state = vm.conflict_state()
    assert state is not None
    assert state["conflicting_paths"] == ["hello.txt"]
    assert is_merge_in_progress(committed_repo)


# ----- Continue during the application-activation refresh ---------------------


@pytest.mark.parametrize(
    "outcome",
    [
        "completed", "repeat_refresh", "rebase", "read_error", "switched",
        "external_commit", "unresolved",
    ],
)
def test_continue_click_during_repository_refresh(
    qtbot, committed_repo, monkeypatch, outcome,
) -> None:
    """An activation refresh must not swallow Continue or run it twice."""
    _build_conflict(committed_repo)
    if outcome == "rebase":
        checkout_branch(committed_repo, "feature")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm
    if outcome == "rebase":
        vm.rebase_branch("main")
        qtbot.waitUntil(lambda: not vm.is_busy() and not vm._active_workers, timeout=6000)
        assert vm.conflict_state()["operation"] == "rebase"
    else:
        vm.merge_branch("feature")
    vm.stop_worktree_refresh()
    qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)
    panel = window._conflict_panel
    original_head = committed_repo.head_commit.sha
    history_size = len(vm.command_processor().undo_stack_snapshot())
    if outcome != "unresolved":
        (Path(committed_repo.path) / "hello.txt").write_text("external resolution\n")
        committed_repo.repo.index.add("hello.txt")
        committed_repo.repo.index.write()

    started, release = Event(), Event()
    original_read = CommitPanelViewModel._compute_status_data

    def slow_read(manager):
        started.set()
        assert release.wait(5)
        if outcome == "read_error":
            raise GitError("refresh failed for test")
        return original_read(manager)

    monkeypatch.setattr(CommitPanelViewModel, "_compute_status_data", staticmethod(slow_read))
    errors = []
    vm.error_occurred.connect(errors.append)
    try:
        vm.refresh_state()
        qtbot.waitUntil(started.is_set)
        assert vm.is_busy()
        for _ in range(3):
            qtbot.mouseClick(panel._continue_btn, Qt.MouseButton.LeftButton)
        assert errors == []
        assert committed_repo.head_commit.sha == original_head
        assert (
            is_rebase_in_progress(committed_repo) if outcome == "rebase"
            else is_merge_in_progress(committed_repo)
        )
        if outcome == "switched":
            vm.set_repository(None, force=True)
        elif outcome == "repeat_refresh":
            vm.refresh_state()
        elif outcome == "external_commit":
            complete_merge(committed_repo, "feature")
            external_head = committed_repo.head_commit.sha
        release.set()
        qtbot.waitUntil(
            lambda: not vm._active_workers and not vm.is_busy() and not vm._refresh_pending,
            timeout=6000,
        )

        if outcome in ("completed", "repeat_refresh", "rebase"):
            assert errors == []
            assert vm.conflict_state() is None
            assert panel.isHidden()
            assert not is_merge_in_progress(committed_repo)
            if outcome == "rebase":
                assert not is_rebase_in_progress(committed_repo)
                assert committed_repo.repo.head.shorthand == "feature"
            else:
                assert len(_head_commit(committed_repo).parent_ids) == 2
            assert len(vm.command_processor().undo_stack_snapshot()) == history_size + 1
        elif outcome == "external_commit":
            assert errors == []
            assert committed_repo.head_commit.sha == external_head
            assert vm.conflict_state() is None
            assert panel.isHidden()
            assert len(vm.command_processor().undo_stack_snapshot()) == history_size
        else:
            assert committed_repo.head_commit.sha == original_head
            assert is_merge_in_progress(committed_repo)
            if outcome == "read_error":
                assert errors == ["Failed to load repository data: refresh failed for test"]
                # A later successful refresh must not replay the cancelled click.
                monkeypatch.setattr(
                    CommitPanelViewModel, "_compute_status_data", staticmethod(original_read),
                )
                vm.refresh_state()
                qtbot.waitUntil(lambda: not vm._active_workers and not vm.is_busy(), timeout=6000)
                assert committed_repo.head_commit.sha == original_head
                assert is_merge_in_progress(committed_repo)
                panel._continue_btn.click()
                assert vm.conflict_state() is None
                assert panel.isHidden()
            elif outcome == "unresolved":
                assert len(errors) == 1 and "still need resolution" in errors[0]
                assert vm.conflict_state()["conflicting_paths"] == ["hello.txt"]
            else:
                assert errors == []
                assert vm.repository_manager() is None
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm._active_workers and not vm.is_busy(), timeout=6000)
        window.close()


def test_continue_button_during_mutating_operation_remains_guarded(qtbot, committed_repo):
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.set_repository(committed_repo)
    vm = window._main_vm
    vm.merge_branch("feature")
    vm.stop_worktree_refresh()
    qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)
    original_head = committed_repo.head_commit.sha
    errors = []
    vm.error_occurred.connect(errors.append)
    vm._is_busy = True
    try:
        window._conflict_panel._continue_btn.click()
        assert len(errors) == 1 and "Another operation is in progress" in errors[0]
        assert committed_repo.head_commit.sha == original_head
        assert is_merge_in_progress(committed_repo)
    finally:
        vm._is_busy = False
        window.close()


# ----- ConflictPanel unit behaviour -------------------------------------------


def test_conflict_panel_state_rendering(qtbot) -> None:
    _ensure_app()
    panel = ConflictPanel()
    qtbot.addWidget(panel)
    assert panel.isHidden()

    panel.set_state(
        {
            "in_progress": True,
            "operation": "merge",
            "conflicting_paths": ["a.txt", "b.txt"],
            "source": "feature",
            "target": "main",
        },
    )
    assert not panel.isHidden()
    assert panel._files.count() == 2
    assert panel._continue_btn.isEnabled()
    assert panel._abort_btn.isEnabled()
    assert "feature" in panel._subtitle.text()

    # Cherry-pick: continue/abort are not offered (commit panel flow).
    panel.set_state(
        {
            "in_progress": True,
            "operation": "cherry-pick",
            "conflicting_paths": ["c.txt"],
        },
    )
    assert not panel._continue_btn.isEnabled()
    assert not panel._abort_btn.isEnabled()

    panel.set_state({"in_progress": False, "conflicting_paths": [], "operation": None})
    assert panel.isHidden()


def test_conflict_panel_emits_resolve_and_abort(qtbot) -> None:
    _ensure_app()
    panel = ConflictPanel()
    qtbot.addWidget(panel)
    panel.set_state(
        {
            "in_progress": True,
            "operation": "merge",
            "conflicting_paths": ["a.txt"],
        },
    )
    resolved: list[str] = []
    aborted: list[str] = []
    continued: list[bool] = []
    panel.resolve_requested.connect(resolved.append)
    panel.abort_requested.connect(aborted.append)
    panel.continue_requested.connect(lambda: continued.append(True))

    panel._on_item_activated(panel._files.item(0))
    assert resolved == ["a.txt"]

    panel._on_abort_clicked()
    assert aborted == ["merge"]

    panel._continue_btn.click()
    assert continued == [True]


@pytest.mark.parametrize("paths", [["a.txt"], ["a.txt", "b.txt", "c.txt"]])
def test_conflict_panel_single_click_resolves_first_file(qtbot, paths) -> None:
    panel = ConflictPanel()
    qtbot.addWidget(panel)
    panel.set_state({"in_progress": True, "operation": "merge", "conflicting_paths": paths})
    resolved = []
    panel.resolve_requested.connect(resolved.append)

    qtbot.mouseClick(panel._files.viewport(), Qt.MouseButton.LeftButton,
                     pos=panel._files.visualItemRect(panel._files.item(0)).center())

    assert resolved == [paths[0]]
    assert panel._files.currentItem().text() == paths[0]


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["a.txt", "b.txt", "c.txt"], "b.txt"),
        (["c.txt", "a.txt", "b.txt"], "b.txt"),
        (["a.txt", "c.txt"], "a.txt"),
        ([], None),
    ],
)
def test_conflict_panel_refresh_preserves_selection(qtbot, paths, expected) -> None:
    panel = ConflictPanel()
    qtbot.addWidget(panel)
    state = {
        "in_progress": True, "operation": "merge",
        "conflicting_paths": ["a.txt", "b.txt", "c.txt"],
    }
    panel.set_state(state)
    panel._files.setCurrentRow(1)
    resolved = []
    panel.resolve_requested.connect(resolved.append)

    panel.set_state({**state, "conflicting_paths": paths})
    if expected:
        item = panel._files.currentItem()
        qtbot.mouseClick(panel._files.viewport(), Qt.MouseButton.LeftButton,
                         pos=panel._files.visualItemRect(item).center())

    assert resolved == ([expected] if expected else [])
    item = panel._files.currentItem()
    assert (item.text() if item else None) == expected


def _external_git(manager, *args):
    manager.release_handles()
    return subprocess.run(
        ["git", "-C", manager.path, "-c", "user.name=Tester",
         "-c", "user.email=tester@example.com", *args],
        capture_output=True, timeout=15,
    )


@pytest.mark.parametrize("detection", ["open", "focus", "monitor", "sync"])
def test_external_merge_is_detected_and_resolved_inline(qtbot, committed_repo, detection):
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    vm = window._main_vm
    if detection != "open":
        window.set_repository(committed_repo)
        if detection != "monitor":
            vm.stop_worktree_refresh()
            qtbot.waitUntil(lambda: vm._worktree_refresh_worker is None)
        else:
            vm._worktree_refresh_timer.setInterval(50)
    result = _external_git(committed_repo, "merge", "--no-ff", "feature")
    assert result.returncode == 1, result.stderr
    assert is_merge_in_progress(committed_repo)
    if detection == "open":
        window.set_repository(committed_repo)
    elif detection == "focus":
        window._on_app_state_changed(Qt.ApplicationState.ApplicationActive)
    elif detection == "sync":
        vm._async_enabled = False
        vm.refresh_state()
    qtbot.waitUntil(lambda: vm.conflict_state() is not None and not vm.is_busy())
    panel = window._conflict_panel
    assert panel.isVisible() and window._right_panel.isVisible()
    assert panel._files.item(0).text() == "hello.txt"
    assert window._graph_stack.currentWidget() is window._graph_table
    qtbot.mouseClick(panel._files.viewport(), Qt.MouseButton.LeftButton,
                     pos=panel._files.visualItemRect(panel._files.item(0)).center())
    editor = window._conflict_editor
    assert editor.isVisible() and not editor.isWindow()
    assert QApplication.activeModalWidget() is None
    editor.theirs_check.click()
    assert editor.result_text() == "feature hi\n"
    editor._on_mark_resolved()
    assert vm.conflict_state() is None
    assert not is_merge_in_progress(committed_repo)
    assert len(committed_repo.repo.head.peel().parent_ids) == 2
    assert window._graph_stack.currentWidget() is window._graph_table
    assert not panel.isVisible()
    window.close()


def test_inline_drafts_survive_file_switch_refresh_and_back(qtbot, committed_repo):
    _build_conflict(committed_repo, ("hello.txt", "second.txt"))
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm
    vm.merge_branch("feature")
    window._on_conflict_resolve("hello.txt")
    editor = window._conflict_editor
    first_vm = editor.viewmodel
    editor.ours_check.click()
    editor.set_result_text("first draft\n")
    window._on_conflict_resolve("second.txt")
    editor.theirs_check.click()
    assert editor.result_text() == "feature hi\n"
    window._on_conflict_resolve("hello.txt")
    assert editor.viewmodel is first_vm
    assert editor.result_text() == "first draft\n"
    vm.refresh_state()
    qtbot.waitUntil(lambda: not vm.is_busy())
    assert editor.viewmodel is first_vm and editor.result_text() == "first draft\n"
    assert editor.isVisible()
    editor.request_close()
    assert window._graph_stack.currentWidget() is window._graph_table
    window._on_conflict_resolve("hello.txt")
    assert editor.result_text() == "first draft\n"
    editor._on_mark_resolved()
    assert vm.conflict_state()["conflicting_paths"] == ["second.txt"]
    assert window._conflict_panel._files.count() == 1
    window._on_conflict_resolve("second.txt")
    assert editor.result_text() == "feature hi\n"
    editor._on_mark_resolved()
    assert vm.conflict_state() is None and not vm._conflict_editors
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "first draft\n"
    assert (Path(committed_repo.path) / "second.txt").read_text() == "feature hi\n"
    window.close()


@pytest.mark.parametrize("external_action", ["abort", "resolve", "switch"])
def test_inline_editor_closes_when_operation_disappears(qtbot, committed_repo, external_action):
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm
    vm.merge_branch("feature")
    window._on_conflict_resolve("hello.txt")
    window._conflict_editor.set_result_text("draft\n")
    if external_action == "switch":
        vm.set_repository(None)
    else:
        if external_action == "abort":
            assert _external_git(committed_repo, "merge", "--abort").returncode == 0
        else:
            (Path(committed_repo.path) / "hello.txt").write_text("external\n")
            assert _external_git(committed_repo, "add", "hello.txt").returncode == 0
        vm.refresh_state()
        qtbot.waitUntil(lambda: not vm.is_busy())
    assert window._graph_stack.currentWidget() is window._graph_table
    assert not vm._conflict_editors
    if external_action == "resolve":
        assert window._conflict_panel._files.count() == 0
        window._conflict_panel._continue_btn.click()
    assert vm.conflict_state() is None
    window.close()


def test_inline_failed_save_preserves_draft_and_external_file(qtbot, committed_repo):
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm
    vm.merge_branch("feature")
    window._on_conflict_resolve("hello.txt")
    editor = window._conflict_editor
    editor.set_result_text("my draft\n")
    path = Path(committed_repo.path) / "hello.txt"
    path.write_bytes(b"external edit\n")
    editor._on_mark_resolved()
    assert editor.isVisible() and editor.result_text() == "my draft\n"
    assert not editor._message.isHidden()
    assert path.read_bytes() == b"external edit\n"
    assert vm.conflict_state()["conflicting_paths"] == ["hello.txt"]
    window.close()


def test_inline_load_error_is_visible_and_repository_switch_clears_it(
    qtbot, committed_repo, monkeypatch,
):
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    window._main_vm.merge_branch("feature")

    def fail_load(*args):
        raise GitError("This conflict requires an external editor")

    monkeypatch.setattr("src.viewmodels.conflict_editor_viewmodel.load_conflict", fail_load)
    window._on_conflict_resolve("hello.txt")
    editor = window._conflict_editor
    assert editor.isVisible() and not editor._message.isHidden()
    assert "external editor" in editor._message.text()
    assert not editor.viewmodel.can_resolve
    window._main_vm.set_repository(None)
    qtbot.wait(1)
    assert window._graph_stack.currentWidget() is window._graph_table
    window.close()


def test_binary_conflict_can_be_resolved_inline(qtbot, committed_repo):
    create_branch(committed_repo, "feature")
    for branch in ("feature", "main"):
        checkout_branch(committed_repo, branch)
        (Path(committed_repo.path) / "hello.txt").write_bytes(b"\x00" + branch.encode())
        committed_repo.repo.index.add("hello.txt")
        committed_repo.repo.index.write()
        commit_changes(committed_repo, branch, stage_all=False)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    window._main_vm.merge_branch("feature")
    window._on_conflict_resolve("hello.txt")
    editor = window._conflict_editor
    assert editor.is_binary()
    editor._accept_theirs_btn.click()
    editor._on_mark_resolved()
    assert window._main_vm.conflict_state() is None
    assert (Path(committed_repo.path) / "hello.txt").read_bytes() == b"\x00feature"
    window.close()
