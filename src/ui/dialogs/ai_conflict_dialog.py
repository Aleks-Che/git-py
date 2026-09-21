"""Editable shared instruction and context for conflict resolution."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from src.utils.ai_config import AISettings


class AIConflictDialog(QDialog):
    def __init__(self, settings: AISettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Решить с помощью AI")
        self.resize(700, 540)
        layout = QVBoxLayout(self)
        label = QLabel(f"Модель: {settings.model or 'не выбрана'}\n"
                       "AI разрешит все участки текущего файла. "
                       "Результат можно проверить и изменить.")
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(QLabel(
            f"Общая инструкция · {settings.conflict_language} "
            "(Settings → AI → Edit prompts)"
        ))
        self.prompt_edit = QPlainTextEdit(settings.conflict_prompt)
        layout.addWidget(self.prompt_edit, 2)
        layout.addWidget(QLabel("Ваш контекст: что это за ветки и какие изменения нужно сохранить"))
        self.context_edit = QPlainTextEdit(settings.conflict_context)
        self.context_edit.setPlaceholderText(
            "Например: слева изменения backend, справа frontend. "
            "Сохранить записи обеих веток, убрать только дубликаты."
        )
        layout.addWidget(self.context_edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Решить конфликты")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
