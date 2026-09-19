"""A text-editing session: draft, disk baseline, save/cancel, and stale-read guards."""
from __future__ import annotations

from dataclasses import replace
from functools import partial

from PySide6.QtCore import QObject, Signal

from src.core.exceptions import GitError
from src.core.file_edit import TextFileSnapshot, read_text_file
from src.utils.latest_worker import LatestWorker
from src.viewmodels.commands import CommandProcessor, SaveFileCommand


class FileEditorViewModel(QObject):
    state_changed = Signal()
    text_loaded = Signal(str)
    saved = Signal()
    editing_finished = Signal()
    error_occurred = Signal(str)

    def __init__(
        self, processor: CommandProcessor, parent: QObject, *, async_enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._processor = processor
        self._async_enabled = async_enabled
        self._loader = LatestWorker(self)
        self._loader.finished.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self.active = False
        self.loading = False
        self.blocked = False
        self.text = ""
        self._snapshot: TextFileSnapshot | None = None

    @property
    def dirty(self) -> bool:
        return self._snapshot is not None and self.text != self._snapshot.text

    def begin_editing(self, root: str, path: str, max_bytes: int) -> None:
        if self.active or self.blocked:
            return
        self.active = True
        self.loading = True
        self._snapshot = None
        self.text = ""
        self.text_loaded.emit("")
        self.state_changed.emit()
        read = partial(read_text_file, root, path, max_bytes)
        if self._async_enabled:
            self._loader.submit((root, path), read)
        else:
            try:
                self._on_loaded(None, read())
            except GitError as exc:
                self._on_failed(None, exc)

    def _on_loaded(self, key, snapshot: TextFileSnapshot) -> None:
        self._snapshot = snapshot
        self.text = snapshot.text
        self.loading = False
        self.text_loaded.emit(self.text)
        self.state_changed.emit()

    def _on_failed(self, key, error) -> None:
        self.cancel_editing()
        self.error_occurred.emit(str(error))

    def set_text(self, text: str) -> None:
        if not self.active or self.loading:
            return
        was_dirty = self.dirty
        self.text = text
        if self.dirty != was_dirty:
            self.state_changed.emit()

    def set_blocked(self, blocked: bool) -> None:
        self.blocked = blocked
        self.state_changed.emit()

    def save_file(self) -> bool:
        if not self.dirty:
            return True
        if self.blocked:
            self.error_occurred.emit("Wait for the current operation before saving the file.")
            return False
        try:
            command = SaveFileCommand(self._snapshot, self.text)
            self._processor.execute(command)
        except (GitError, UnicodeError) as exc:
            self.error_occurred.emit(str(exc))
            return False
        self._snapshot = replace(
            self._snapshot, data=self._snapshot.encode(self.text), text=self.text,
        )
        self.state_changed.emit()
        self.saved.emit()
        return True

    def finish_editing(self) -> bool:
        """Autosave before navigation; a failed save keeps the editor and draft open."""
        if not self.active:
            return True
        if not self.save_file():
            return False
        self.cancel_editing()
        return True

    def cancel_editing(self) -> None:
        """Discard only the unsaved draft; previously saved content stays on disk."""
        was_active = self.active
        self._loader.invalidate()
        self.active = False
        self.loading = False
        self._snapshot = None
        self.text = ""
        self.state_changed.emit()
        if was_active:
            self.editing_finished.emit()
