"""``QRunnable`` wrapper for long-running Git operations on a thread pool.

Per ``docs/DEVELOPMENT_RULES.md`` (section 3), network or long-running
ops (push, pull, fetch, clone, rebase, large merges) MUST run off the
UI thread. Callers wire up the worker's signals to update the UI on
completion; the work callable itself must not touch widgets.

The ``failed`` signal carries the **exception object** raised by the
work callable, so the UI can route on the actual type
(``MergeConflictError`` vs generic ``GitError`` vs ``AuthError``)
rather than guessing from a stringified message (R2.2 C7/M8/M25).

``setAutoDelete(False)`` keeps the runnable alive while the UI holds
a strong reference through :attr:`_active_workers` — without this,
the runnable can be deleted before the queued result signal is
delivered, causing Qt to fire ``RuntimeError: wrapped C/C++ object
of type AsyncWorker has been deleted`` during teardown.

Usage::

    worker = AsyncWorker(some_long_call, arg1, kw=value)
    worker.signals.finished.connect(on_done)
    worker.signals.failed.connect(on_error)
    worker.signals.lifespan_finished.connect(on_cleanup)  # no payload
    QThreadPool.globalInstance().start(worker)
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class _WorkerSignals(QObject):
    """Signals that must originate from a :class:`QObject` parent.

    ``started`` and the no-payload ``lifespan_finished`` fire from
    the worker thread; ``finished`` and ``failed`` carry payloads
    and are also delivered to the UI thread via Qt's queued
    connections.  All four live on the same :class:`QObject` so they
    share one set of cross-thread metadata.
    """

    started = Signal()
    finished = Signal(object)  # carries the result of fn(...)
    failed = Signal(object)    # carries the exception object raised by fn
    lifespan_finished = Signal()  # unconditional lifecycle hook (no payload)


class AsyncWorker(QRunnable):
    """Run ``fn(*args, **kwargs)`` on the global ``QThreadPool``."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # ``setAutoDelete(False)`` so the instance survives until the
        # owning ``MainViewModel`` explicitly drops its reference in
        # ``_on_async_finished``.  Pending signals still queued
        # across the thread boundary will then be delivered against
        # a live QObject, fixing the teardown crash (R2.2).
        self.setAutoDelete(False)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        """Execute ``fn`` on a worker thread, surfacing results via signals."""
        self.signals.started.emit()
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - boundary; UI routes by type
            # Carry the exception object itself (not ``str(exc)``)
            # so the slot can detect the actual class.
            self.signals.failed.emit(exc)
        else:
            self.signals.finished.emit(result)
        finally:
            # Unconditional lifecycle hook — separate from
            # :attr:`finished` so cleanup logic does not depend on
            # the caller's argument shape.
            self.signals.lifespan_finished.emit()
