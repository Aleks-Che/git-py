"""ViewModel for the repository tab bar.

Manages a list of open repository paths and the active tab index.
The :class:`RepoBarWidget` in ``src/ui/widgets/`` consumes this VM.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal


class RepoTabViewModel(QObject):
    """Holds the list of open repositories and the active index.

    Signals
    -------
    active_tab_changed(int)
        Emitted when the active tab index changes (or -1 when none).
    tabs_changed(list[str])
        Emitted when the tab list itself changes (add / remove / reorder).
    """

    active_tab_changed = Signal(int)   # index or -1
    tabs_changed = Signal(object)      # list[str]

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tabs: list[str] = []
        self._active_index: int = -1

    # ----- properties ---------------------------------------------------

    @property
    def tabs(self) -> list[str]:
        return list(self._tabs)

    @property
    def active_index(self) -> int:
        return self._active_index

    @property
    def active_path(self) -> str | None:
        if 0 <= self._active_index < len(self._tabs):
            return self._tabs[self._active_index]
        return None

    @property
    def count(self) -> int:
        return len(self._tabs)

    # ----- mutations ----------------------------------------------------

    def add_tab(self, path: str) -> int:
        """Add a new repository tab and activate it.

        If a tab for *path* already exists it is just activated.
        Returns the index of the (possibly pre-existing) tab.
        """
        path = Path(path).resolve().as_posix()
        existing = self._index_of(path)
        if existing >= 0:
            self.set_active_tab(existing)
            return existing
        self._tabs.append(path)
        self._tabs_changed()
        self.set_active_tab(len(self._tabs) - 1)
        return len(self._tabs) - 1

    def remove_tab(self, index: int) -> None:
        """Remove the tab at *index* (the repo itself is untouched)."""
        if index < 0 or index >= len(self._tabs):
            return
        self._tabs.pop(index)
        # Adjust active index *before* notifying the view, so the
        # widget rebuild sees a consistent state.
        if self._active_index >= len(self._tabs):
            self._active_index = len(self._tabs) - 1
        elif self._active_index == index:
            self._active_index = min(index, len(self._tabs) - 1)
        self._tabs_changed()
        self.active_tab_changed.emit(self._active_index)

    def set_active_tab(self, index: int) -> None:
        """Switch the active tab (does nothing if out of range)."""
        if index < 0 or index >= len(self._tabs):
            if self._active_index != -1:
                self._active_index = -1
                self.active_tab_changed.emit(-1)
            return
        if index != self._active_index:
            self._active_index = index
            self.active_tab_changed.emit(index)

    def clear(self) -> None:
        """Remove all tabs."""
        self._tabs.clear()
        self._active_index = -1
        self._tabs_changed()
        self.active_tab_changed.emit(-1)

    # ----- config persistence -------------------------------------------

    def load_from_state(self, paths: list[str], active_path: str | None) -> None:
        """Restore tab state from config data."""
        self._tabs = [Path(p).resolve().as_posix() for p in paths]
        self._tabs_changed()
        if active_path is not None:
            idx = self._index_of(active_path)
            self._active_index = idx if idx >= 0 else (len(self._tabs) - 1 if self._tabs else -1)
        else:
            self._active_index = len(self._tabs) - 1 if self._tabs else -1
        self.active_tab_changed.emit(self._active_index)

    def save_to_state(self) -> dict[str, Any]:
        """Return a serialisable dict for config persistence."""
        return {
            "paths": list(self._tabs),
            "active_path": self.active_path,
        }

    # ----- internals ----------------------------------------------------

    def _index_of(self, path: str) -> int:
        norm = Path(path).resolve().as_posix()
        for i, p in enumerate(self._tabs):
            if Path(p).resolve().as_posix() == norm:
                return i
        return -1

    def _tabs_changed(self) -> None:
        self.tabs_changed.emit(list(self._tabs))


__all__ = ["RepoTabViewModel"]
