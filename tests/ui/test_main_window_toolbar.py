"""Tests for the Remote toolbar Push / Pull / Fetch buttons on :class:`MainWindow`.

Exercises the actions through the public :class:`MainViewModel` surface
(``fetch_changes`` / ``pull_changes`` / ``push_changes``) so the test
does not need to trigger the actual :class:`QAction` menu items. The
actions' enabled state is what we are most interested in: it must
track the bound repository (no repo → disabled) and the busy state
(async op in flight → disabled).
"""
from __future__ import annotations

import time

import pygit2
from PySide6.QtCore import QCoreApplication
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "t@example.com", int(time.time()), 0)


# ----- enabled / disabled -------------------------------------------------


def test_remote_actions_disabled_without_repo(qtbot) -> None:
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert not window._action_fetch.isEnabled()  # noqa: SLF001
    assert not window._action_pull.isEnabled()  # noqa: SLF001
    assert not window._action_push.isEnabled()  # noqa: SLF001
    assert not window._action_manage_remotes.isEnabled()  # noqa: SLF001


def test_remote_actions_enabled_after_open(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    assert window._action_fetch.isEnabled()  # noqa: SLF001
    assert window._action_pull.isEnabled()  # noqa: SLF001
    assert window._action_push.isEnabled()  # noqa: SLF001
    assert window._action_manage_remotes.isEnabled()  # noqa: SLF001
    window.close()


def test_remote_actions_disabled_after_close(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    assert window._action_fetch.isEnabled()  # noqa: SLF001
    window.set_repository(None)
    assert not window._action_fetch.isEnabled()  # noqa: SLF001
    assert not window._action_pull.isEnabled()  # noqa: SLF001
    assert not window._action_push.isEnabled()  # noqa: SLF001


def test_remote_actions_disabled_while_busy(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    # Simulate an in-flight async op.
    window._main_vm._is_busy = True  # noqa: SLF001
    window._update_remote_actions()  # noqa: SLF001
    assert not window._action_fetch.isEnabled()  # noqa: SLF001
    assert not window._action_pull.isEnabled()  # noqa: SLF001
    assert not window._action_push.isEnabled()  # noqa: SLF001


def test_remote_actions_reenabled_after_busy_clears(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    window._main_vm._is_busy = True  # noqa: SLF001
    window._update_remote_actions()  # noqa: SLF001
    assert not window._action_fetch.isEnabled()  # noqa: SLF001
    window._main_vm._is_busy = False  # noqa: SLF001
    window._update_remote_actions()  # noqa: SLF001
    assert window._action_fetch.isEnabled()  # noqa: SLF001


# ----- toolbar wiring -----------------------------------------------------


def test_remote_toolbar_attached(qtbot) -> None:
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    assert hasattr(window, "_remote_toolbar")  # noqa: SLF001
    toolbar = window._remote_toolbar  # noqa: SLF001
    # The toolbar has the three remote actions.
    actions = [a.text() for a in toolbar.actions()]
    assert any("Fetch" in t for t in actions)
    assert any("Pull" in t for t in actions)
    assert any("Push" in t for t in actions)


# ----- menu / file wiring -------------------------------------------------


def test_clone_action_is_wired(qtbot) -> None:
    """The ``File > Clone…`` action triggers the clone dialog, not a stub."""
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    # Replace ``_open_clone_dialog`` with a sentinel to verify it's called.
    calls: list[bool] = []
    window._open_clone_dialog = lambda: calls.append(True)  # type: ignore[method-assign]  # noqa: SLF001
    # Trigger the menu action (does the same thing as clicking the menu).
    window._action_clone.trigger()  # noqa: SLF001
    assert calls


def test_init_action_is_wired(qtbot) -> None:
    """The ``File > Init New Repository…`` action triggers the init dialog."""
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    calls: list[bool] = []
    window._open_init_dialog = lambda: calls.append(True)  # type: ignore[method-assign]  # noqa: SLF001
    window._action_init.trigger()  # noqa: SLF001
    assert calls


def test_manage_remotes_action_is_wired(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(committed_repo)
    calls: list[bool] = []
    window._open_remote_manage_dialog = lambda: calls.append(True)  # type: ignore[method-assign]  # noqa: SLF001
    window._action_manage_remotes.trigger()  # noqa: SLF001
    assert calls
