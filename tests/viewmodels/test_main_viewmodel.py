"""Stage 0: verify the ViewModel stubs and the shared AppSignals singleton."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from src.utils.signals import app_signals


def test_viewmodel_stubs_importable() -> None:
    from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel
    from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
    from src.viewmodels.graph_viewmodel import GraphViewModel
    from src.viewmodels.main_viewmodel import MainViewModel

    assert MainViewModel() is not None
    assert GraphViewModel() is not None
    assert CommitPanelViewModel() is not None
    assert BranchPanelViewModel() is not None


def test_app_signals_singleton() -> None:
    QCoreApplication.instance() or QCoreApplication([])
    assert app_signals() is app_signals()


def test_app_signals_exposes_expected_signals() -> None:
    QCoreApplication.instance() or QCoreApplication([])
    signals = app_signals()
    assert hasattr(signals, "repository_changed")
    assert hasattr(signals, "operation_finished")
    assert hasattr(signals, "error_occurred")
