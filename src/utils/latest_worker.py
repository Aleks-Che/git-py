"""Bound background preview work and deliver only the latest request."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from src.utils.async_worker import AsyncWorker


class LatestWorker(QObject):
    """Keep at most two reads running and replace the pending read on each click."""

    finished = Signal(object, object)
    failed = Signal(object, object)
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._revision = 0
        self._busy = False
        self._key = None
        self._pending = None
        self._workers: dict[QObject, tuple[AsyncWorker, int, object]] = {}

    def invalidate(self) -> None:
        """Discard queued work and ignore late results without waiting for a thread."""
        self._revision += 1
        self._pending = None
        self._key = None
        self._set_busy(False)

    def submit(self, key: object, work: Callable) -> None:
        if self._busy and key == self._key:
            return
        self._key = key
        self._revision += 1
        request = (self._revision, key, work)
        self._set_busy(True)
        if len(self._workers) < 2:
            self._start(request)
        else:
            self._pending = request

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.busy_changed.emit(busy)

    def _start(self, request: tuple) -> None:
        revision, key, work = request
        worker = AsyncWorker(work)
        self._workers[worker.signals] = (worker, revision, key)
        worker.signals.finished.connect(self._on_result)
        worker.signals.failed.connect(self._on_failure)
        worker.signals.lifespan_finished.connect(self._on_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _on_result(self, result: object) -> None:
        _, revision, key = self._workers[self.sender()]
        if revision == self._revision:
            self._set_busy(False)
            self.finished.emit(key, result)

    @Slot(object)
    def _on_failure(self, error: object) -> None:
        _, revision, key = self._workers[self.sender()]
        if revision == self._revision:
            self._set_busy(False)
            self.failed.emit(key, error)

    @Slot()
    def _on_finished(self) -> None:
        self._workers.pop(self.sender())
        if self._pending is not None:
            request, self._pending = self._pending, None
            self._start(request)
