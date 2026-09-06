"""Editable prompt for commit generation, with language and one-click style presets."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.utils.ai_config import COMMIT_LANGUAGES, PROMPT_PRESETS, AISettings


class AIPromptsDialog(QDialog):
    def __init__(self, settings: AISettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("AI Prompts — Commit message")
        self.resize(800, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Commit message · Summary + Description"))
        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("Message language:"))
        self._language = QComboBox()
        self._language.setEditable(True)
        self._language.addItems(COMMIT_LANGUAGES)
        self._language.setCurrentText(settings.language)
        language_row.addWidget(self._language, 1)
        layout.addLayout(language_row)
        layout.addWidget(QLabel("Style presets — click to apply; the prompt remains editable:"))
        presets_row = QHBoxLayout()
        self._preset_buttons: dict[str, QToolButton] = {}
        for key, (label, _prompt) in PROMPT_PRESETS.items():
            button = QToolButton()
            button.setObjectName("ai-prompt-preset")
            button.setText(label)
            button.setCheckable(True)
            button.setToolTip("Replace the prompt with this preset (Ctrl+Z to undo).")
            button.clicked.connect(lambda _checked=False, preset=key: self._apply_preset(preset))
            presets_row.addWidget(button)
            self._preset_buttons[key] = button
        layout.addLayout(presets_row)
        self._prompt = QPlainTextEdit()
        self._prompt.setPlainText(settings.commit_prompt)
        layout.addWidget(self._prompt, 1)
        hint = QLabel(
            "Language and the summary/description response format are applied automatically. "
            "Git Flow uses the branch name as a hint; the staged diff takes precedence.",
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._prompt.textChanged.connect(self._refresh_state)
        self._language.currentTextChanged.connect(self._refresh_state)
        self._refresh_state()

    def _apply_preset(self, key: str) -> None:
        self._prompt.selectAll()
        self._prompt.insertPlainText(PROMPT_PRESETS[key][1])

    def _refresh_state(self) -> None:
        text = self._prompt.toPlainText().strip()
        for key, button in self._preset_buttons.items():
            button.setChecked(text == PROMPT_PRESETS[key][1])
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(text and self._language.currentText().strip()),
        )

    def settings(self) -> AISettings:
        prompt = self._prompt.toPlainText().strip()
        preset = next(
            (key for key, (_, text) in PROMPT_PRESETS.items() if text == prompt), "custom"
        )
        return replace(
            self._settings,
            commit_prompt=prompt,
            preset=preset,
            language=self._language.currentText().strip(),
        )
