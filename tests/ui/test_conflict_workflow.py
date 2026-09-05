"""End-to-end conflict-workflow tests (review 2026-09-05, findings 5/8/9).

Covers the full path from a MainWindow merge action through the
conflict panel, per-file resolution and merge completion — plus the
Undo/Redo round-trip of a merge that was completed after a conflict
(finding 8) and a complete conflicted rebase cycle (finding 9).
"""
from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from PySide6.QtWidgets import QApplication
from src.core.operations import (
    checkout_branch,
    commit_changes,
    create_branch,
    is_merge_in_progress,
    is_rebase_in_progress,
)
from src.core.repository import RepositoryManager
from src.ui.dialogs.conflict_resolution_dialog import ConflictResolutionDialog
from src.ui.main_window import MainWindow
from src.ui.widgets.conflict_panel import ConflictPanel
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _build_conflict(mgr: RepositoryManager) -> None:
    """Fork ``feature``/``main`` with a conflicting ``hello.txt``.

    Leaves HEAD on ``main``; ``merge feature`` conflicts on
    ``hello.txt``.
    """
    create_branch(mgr, "feature")
    checkout_branch(mgr, "feature")
    (Path(mgr.path) / "hello.txt").write_text("feature hi\n")
    mgr.repo.index.add("hello.txt")
    mgr.repo.index.write()
    commit_changes(mgr, "feature: hi", stage_all=False)
    checkout_branch(mgr, "main")
    (Path(mgr.path) / "hello.txt").write_text("main hi\n")
    mgr.repo.index.add("hello.txt")
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


def test_conflict_resolve_dialog_routes_to_vm(
    qtbot,
    committed_repo: RepositoryManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-file dialog's ``resolved`` payload reaches the VM."""
    _build_conflict(committed_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    vm = window._main_vm
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    def _fake_exec(self: ConflictResolutionDialog) -> int:
        # The user picks "Accept Theirs"-style content and confirms.
        self.set_result_text("resolved via dialog\n")
        self._on_mark_resolved()
        return 1

    monkeypatch.setattr(ConflictResolutionDialog, "exec", _fake_exec)
    window._on_conflict_resolve("hello.txt")

    assert vm.conflict_state() is None
    assert not is_merge_in_progress(committed_repo)
    assert (Path(committed_repo.path) / "hello.txt").read_text() == (
        "resolved via dialog\n"
    )
    assert len(_head_commit(committed_repo).parent_ids) == 2
    window.close()


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
