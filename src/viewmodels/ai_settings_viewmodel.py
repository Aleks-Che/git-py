"""Asynchronous model discovery and connection tests for the AI settings tab."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThreadPool, Signal, Slot

from src.utils.ai_client import AIClient, AIError
from src.utils.ai_config import AISettings
from src.utils.async_worker import AsyncWorker


class AISettingsViewModel(QObject):
    busy_changed = Signal(bool)
    models_ready = Signal(list)
    test_succeeded = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._worker: AsyncWorker | None = None
        self._revision = 0
        self._request_revision = 0
        self._operation = ""

    @property
    def is_busy(self) -> bool:
        return self._worker is not None

    def invalidate_requests(self) -> None:
        self._revision += 1

    def invalidate_test(self) -> None:
        if self._operation == "test":
            self.invalidate_requests()

    def load_models(self, settings: AISettings) -> None:
        self._start(settings, "models")

    def test_connection(self, settings: AISettings) -> None:
        self._start(settings, "test")

    def _start(self, settings: AISettings, operation: str) -> None:
        if self.is_busy:
            return
        try:
            client = AIClient(settings)
            if operation == "test" and not settings.model.strip():
                raise AIError("Select or enter a model first.")
        except AIError as exc:
            self.error_occurred.emit(str(exc))
            return
        self._operation = operation
        self._request_revision = self._revision
        worker = AsyncWorker(
            client.list_models if operation == "models" else client.test_connection
        )
        self._worker = worker
        worker.signals.finished.connect(self._on_result)
        worker.signals.failed.connect(self._on_error)
        worker.signals.lifespan_finished.connect(self._on_finished)
        self.busy_changed.emit(True)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _on_result(self, result: object) -> None:
        if self._request_revision != self._revision:
            return
        if self._operation == "models":
            self.models_ready.emit(result)
        else:
            self.test_succeeded.emit(str(result))

    @Slot(object)
    def _on_error(self, error: Exception) -> None:
        if self._request_revision == self._revision:
            message = (
                str(error) if isinstance(error, AIError) else "Unexpected LLM request failure."
            )
            self.error_occurred.emit(message)

    @Slot()
    def _on_finished(self) -> None:
        self._worker = None
        self.busy_changed.emit(False)
