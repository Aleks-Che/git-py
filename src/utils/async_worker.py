"""``QRunnable`` wrapper for long-running Git operations on a thread pool.

Per ``docs/DEVELOPMENT_RULES.md`` (section 3), network or long-running
ops (push, pull, fetch, clone, rebase, large merges) MUST run off the
UI thread. Callers wire up :attr:`AsyncWorker.signals` to update the UI
on completion; the work callable itself must not touch widgets.

Usage::

    worker = AsyncWorker(some_long_call, arg1, kw=value)
    worker.signals.result.connect(on_done)
    worker.signals.failed.connect(on_error)
    QThreadPool.globalInstance().start(worker)
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal


class _WorkerSignals(QObject):
    started = Signal()
    finished = Signal()
    failed = Signal(str)
    result = Signal(object)


class AsyncWorker(QRunnable):
    """Run ``fn(*args, **kwargs)`` on the global ``QThreadPool``."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _WorkerSignals()

    def run(self) -> None:
        self.signals.started.emit()
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - boundary; we surface the message via signal
            self.signals.failed.emit(str(exc))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
