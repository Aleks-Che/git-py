"""Application-wide Qt signals.

A single :class:`AppSignals` instance is shared across ViewModels and
widgets so any layer can react to ``repository_changed``,
``operation_finished`` and ``error_occurred`` without each consumer
holding its own reference to every emitter.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class AppSignals(QObject):
    """Process-wide event bus for repository/operation/error events."""

    repository_changed = Signal()
    """Emitted after a repository is opened, initialised, cloned, or closed."""

    operation_finished = Signal(str)
    """Emitted when a long-running Git operation completes; payload is the operation name."""

    error_occurred = Signal(str)
    """Emitted instead of raising; payload is the human-readable error message."""


_instance: AppSignals | None = None


def app_signals() -> AppSignals:
    """Return the shared :class:`AppSignals` singleton (created on first call)."""
    global _instance
    if _instance is None:
        _instance = AppSignals()
    return _instance
