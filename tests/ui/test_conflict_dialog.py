"""UI tests for :class:`ConflictResolutionDialog`.

The dialog is exercised under ``pytest-qt`` (``QT_QPA_PLATFORM=offscreen``
on headless CI). Tests use real ``pygit2`` repositories with an
in-progress conflict so the index's conflict list is non-empty.
"""
from __future__ import annotations

from pathlib import Path

import pygit2
from src.core.repository import RepositoryManager
from src.ui.dialogs.conflict_resolution_dialog import (
    ConflictResolutionDialog,
    ConflictResolver,
)


def _build_conflict_repo(tmp_path: Path) -> RepositoryManager:
    """Create a repo on ``main`` with a 2-way conflict on ``hello.txt``."""
    import time

    from src.core.operations import checkout_branch, create_branch

    repo_path = tmp_path / "repo"
    pygit2.init_repository(str(repo_path), initial_head="main")
    mgr = RepositoryManager(str(repo_path))
    sig = pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)
    (repo_path / "hello.txt").write_text("common\n")
    mgr.repo.index.add("hello.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c1 = mgr.repo.create_commit("HEAD", sig, sig, "base", tree, [])
    create_branch(mgr, "feature", target_sha=str(c1))
    checkout_branch(mgr, "feature")
    (repo_path / "hello.txt").write_text("feature says hi\n")
    mgr.repo.index.add("hello.txt")
    mgr.repo.index.write()
    feat_tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("HEAD", sig, sig, "feature", feat_tree, [c1])
    checkout_branch(mgr, "main")
    (repo_path / "hello.txt").write_text("main says hi\n")
    mgr.repo.index.add("hello.txt")
    mgr.repo.index.write()
    main_tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("HEAD", sig, sig, "main", main_tree, [c1])
    return mgr


def _trigger_conflict(mgr: RepositoryManager) -> None:
    """Run a 3-way merge of ``feature`` onto ``main`` (no auto-commit)."""
    from src.core.exceptions import MergeConflictError
    from src.core.operations import merge_branch

    try:
        merge_branch(mgr, "feature")
    except MergeConflictError:
        pass


# ----- widget construction ------------------------------------------------


def test_dialog_initializes_with_no_conflict(qtbot) -> None:
    dialog = ConflictResolutionDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    # No conflict loaded yet — all panels empty.
    assert dialog.ours_view.toPlainText() == ""  # noqa: SLF001
    assert dialog.base_view.toPlainText() == ""  # noqa: SLF001
    assert dialog.theirs_view.toPlainText() == ""  # noqa: SLF001
    assert dialog.result_text() == ""


def test_dialog_loads_three_sides_from_index(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog(mgr, "hello.txt")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.ours_view.toPlainText() == "main says hi\n"  # noqa: SLF001
    assert dialog.base_view.toPlainText() == "common\n"  # noqa: SLF001
    assert dialog.theirs_view.toPlainText() == "feature says hi\n"  # noqa: SLF001


def test_dialog_set_conflict_via_method(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.set_conflict(mgr, "hello.txt")
    assert dialog.ours_view.toPlainText() == "main says hi\n"  # noqa: SLF001
    assert dialog.theirs_view.toPlainText() == "feature says hi\n"  # noqa: SLF001


def test_dialog_path_label_updates(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog(mgr, "hello.txt")
    qtbot.addWidget(dialog)
    assert dialog._path_label.text() == "hello.txt"  # noqa: SLF001


# ----- action buttons -----------------------------------------------------


def test_accept_ours_copies_to_result(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog(mgr, "hello.txt")
    qtbot.addWidget(dialog)
    dialog._accept_ours_btn.click()  # noqa: SLF001
    assert dialog.result_text() == "main says hi\n"


def test_accept_theirs_copies_to_result(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog(mgr, "hello.txt")
    qtbot.addWidget(dialog)
    dialog._accept_theirs_btn.click()  # noqa: SLF001
    assert dialog.result_text() == "feature says hi\n"


def test_accept_both_concatenates(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog(mgr, "hello.txt")
    qtbot.addWidget(dialog)
    dialog._accept_both_btn.click()  # noqa: SLF001
    assert dialog.result_text() == "main says hi\n\nfeature says hi\n"


def test_result_view_is_editable(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog(mgr, "hello.txt")
    qtbot.addWidget(dialog)
    dialog.set_result_text("custom merge result\n")
    assert dialog.result_text() == "custom merge result\n"


# ----- signals ------------------------------------------------------------


def test_mark_resolved_emits_resolved_signal(qtbot, tmp_path: Path) -> None:
    mgr = _build_conflict_repo(tmp_path)
    _trigger_conflict(mgr)
    dialog = ConflictResolutionDialog(mgr, "hello.txt")
    qtbot.addWidget(dialog)
    dialog.set_result_text("final result\n")

    captured: list[str] = []
    dialog.resolved.connect(captured.append)
    ok_button = dialog._button_box.button(  # noqa: SLF001
        dialog._button_box.StandardButton.Ok,  # noqa: SLF001
    )
    ok_button.click()
    assert captured == ["final result\n"]


def test_cancel_does_not_emit_resolved(qtbot) -> None:
    dialog = ConflictResolutionDialog()
    qtbot.addWidget(dialog)
    captured: list[str] = []
    dialog.resolved.connect(captured.append)
    dialog.reject()
    assert captured == []


# ----- extension point ----------------------------------------------------


def test_conflict_resolver_abc_raises_on_call() -> None:
    """The base class is abstract — calling ``resolve`` raises."""
    resolver = ConflictResolver()
    try:
        resolver.resolve("base", "ours", "theirs")
    except NotImplementedError:
        return
    raise AssertionError("ConflictResolver.resolve() should raise NotImplementedError")
