"""ViewModel for the left references panel (branches, remotes, tags, stash).

Stage 3 carries only the scaffolding needed by :class:`MainViewModel`:
a ``QObject`` with ``set_repository`` and ``error_occurred``. The real
branch / tag / stash listing lands in Stage 4.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from src.core.repository import RepositoryManager


class BranchPanelViewModel(QObject):
    """Placeholder for the Stage 4 branch panel ViewModel."""

    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._repo: RepositoryManager | None = None

    def set_repository(self, manager: RepositoryManager | None) -> None:
        """Bind (or unbind) the repository. Stage 4 will refresh on change."""
        self._repo = manager

    def repository_manager(self) -> RepositoryManager | None:
        return self._repo


__all__ = ["BranchPanelViewModel"]
