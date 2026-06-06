"""Tests for the Stash toolbar buttons on :class:`MainWindow`.

The stash toolbar (Stage 7) lives next to the existing Remote
toolbar and shares its re-entrancy guard via ``busy_changed``.
Two actions are exposed:

* **Stash Changes** (``Ctrl+Shift+S``) — pushes the current WIP.
* **Stash Pop** (``Ctrl+Shift+O``) — pops the most recent entry
  (index 0). The button is *disabled* when the stash list is empty.

The tests verify the enablement contract and the click → VM wiring
(``Ctrl+Shift+S`` must end up calling :meth:`MainViewModel.stash_push`).
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QToolBar
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _find_toolbar(window: MainWindow, object_name: str) -> QToolBar:
    for tb in window.findChildren(QToolBar):
        if tb.objectName() == object_name:
            return tb
    raise AssertionError(f"Toolbar {object_name!r} not found")


def _make_dirty(repo: RepositoryManager, text: str = "wip\n") -> None:
    from pathlib import Path

    assert repo.path is not None
    (Path(repo.path) / "hello.txt").write_text(text)


# ----- toolbar presence and shortcuts ---------------------------------


def test_stash_toolbar_is_present(qtbot, tmp_path) -> None:
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    _find_toolbar(win, "stash-toolbar")


def test_stash_push_action_shortcut(qtbot, tmp_path) -> None:
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    assert win._action_stash_push.shortcut() == QKeySequence("Ctrl+Shift+S")


def test_stash_pop_action_shortcut(qtbot, tmp_path) -> None:
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    assert win._action_stash_pop.shortcut() == QKeySequence("Ctrl+Shift+O")


# ----- enablement -----------------------------------------------------


def test_stash_actions_disabled_without_repo(qtbot, tmp_path) -> None:
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    assert not win._action_stash_push.isEnabled()
    assert not win._action_stash_pop.isEnabled()


def test_stash_push_enabled_with_repo(qtbot, committed_repo) -> None:
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    win.set_repository(committed_repo)
    assert win._action_stash_push.isEnabled()


def test_stash_pop_disabled_when_stash_empty(qtbot, committed_repo) -> None:
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    win.set_repository(committed_repo)
    assert not win._action_stash_pop.isEnabled()


def test_stash_pop_enabled_when_stash_present(qtbot, committed_repo) -> None:
    _ensure_app()
    from src.core.operations import stash_push

    _make_dirty(committed_repo)
    stash_push(committed_repo, "for the toolbar")
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    win.set_repository(committed_repo)
    assert win._action_stash_pop.isEnabled()


# ----- click → VM wiring ----------------------------------------------


def test_stash_push_action_invokes_vm(qtbot, committed_repo, monkeypatch) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    win.set_repository(committed_repo)

    captured: list[str] = []
    monkeypatch.setattr(
        win._main_vm, "stash_push",
        lambda message="WIP": captured.append(message),
    )
    win._action_stash_push.trigger()
    assert captured == ["WIP"]


def test_stash_pop_action_invokes_vm(qtbot, committed_repo, monkeypatch) -> None:
    _ensure_app()
    from src.core.operations import stash_push

    _make_dirty(committed_repo)
    stash_push(committed_repo, "pop me")
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    win.set_repository(committed_repo)

    captured: list[int] = []
    monkeypatch.setattr(
        win._main_vm, "stash_pop",
        lambda index=0: captured.append(index) or True,
    )
    win._action_stash_pop.trigger()
    assert captured == [0]


# ----- busy guard -----------------------------------------------------


def test_stash_actions_disabled_when_busy(qtbot, committed_repo) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    from src.core.operations import stash_push

    stash_push(committed_repo, "busy test")
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    win.set_repository(committed_repo)
    assert win._action_stash_push.isEnabled()
    assert win._action_stash_pop.isEnabled()

    # Simulate a long-running op starting.
    win._main_vm._is_busy = True
    win._on_busy_changed(True)
    assert not win._action_stash_push.isEnabled()
    assert not win._action_stash_pop.isEnabled()

    # Op ends — the actions re-evaluate against the live repo state.
    win._main_vm._is_busy = False
    win._on_busy_changed(False)
    assert win._action_stash_push.isEnabled()
    assert win._action_stash_pop.isEnabled()
