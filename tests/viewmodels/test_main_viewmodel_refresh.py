"""Tests for :meth:`MainViewModel.refresh_state`.

The method is the engine behind the "refresh on app activation"
behaviour: the main window calls it whenever ``QApplication`` reports
``Qt.ApplicationActive``, so changes made in another Git client show up
in this UI without the user having to switch tabs.

Contract:

* No-op when no repository is open.
* No-op while a long-running async operation is in flight (the
  re-entrancy guard, the same one the toolbar buttons respect).
* Otherwise calls ``_refresh_all_views`` exactly once, which fans out
  to graph / commit panel / branch panel ``refresh`` calls and emits
  the corresponding ``*_updated`` / ``*_changed`` signals.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtWidgets import QApplication
from src.core.exceptions import GitError
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def test_refresh_state_noop_without_repo() -> None:
    """No bound repository → method must not touch the child ViewModels."""
    _ensure_app()
    vm = MainViewModel()
    vm._graph_view_model = MagicMock()
    vm._commit_panel_view_model = MagicMock()
    vm._branch_panel_view_model = MagicMock()

    vm.refresh_state()

    vm._graph_view_model.refresh_graph.assert_not_called()
    vm._commit_panel_view_model.refresh_status.assert_not_called()
    vm._branch_panel_view_model.refresh.assert_not_called()


def test_refresh_state_noop_while_busy(committed_repo: RepositoryManager) -> None:
    """A long-running async op must not be interrupted by a refresh."""
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm._is_busy = True
    vm._graph_view_model = MagicMock()
    vm._commit_panel_view_model = MagicMock()
    vm._branch_panel_view_model = MagicMock()

    vm.refresh_state()

    vm._graph_view_model.refresh_graph.assert_not_called()
    vm._commit_panel_view_model.refresh_status.assert_not_called()
    vm._branch_panel_view_model.refresh.assert_not_called()


def test_refresh_state_refreshes_all_panels(
    committed_repo: RepositoryManager,
) -> None:
    """With a repo and no busy op, all three child ViewModels are refreshed."""
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm._graph_view_model = MagicMock()
    vm._commit_panel_view_model = MagicMock()
    vm._branch_panel_view_model = MagicMock()

    vm.refresh_state()

    vm._graph_view_model.refresh_graph.assert_called_once()
    vm._commit_panel_view_model.refresh_status.assert_called_once()
    vm._branch_panel_view_model.refresh.assert_called_once()


def test_refresh_state_swallows_giterror(committed_repo: RepositoryManager) -> None:
    """A :class:`GitError` from a child VM is surfaced, not re-raised.

    In practice the child VMs already catch ``GitError`` and forward
    it through ``error_occurred`` — this test exercises the outer
    safety net for an exception that somehow escapes the children
    (e.g. a future refactor that forgets to wrap a Core call).
    """
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm._graph_view_model = MagicMock()
    vm._graph_view_model.refresh_graph.side_effect = GitError("boom")

    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.refresh_state()

    assert errors == ["Failed to refresh: boom"]
