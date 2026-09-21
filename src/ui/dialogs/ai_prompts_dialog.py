"""Commit and conflict prompts, language presets and branch-name placement."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.utils.ai_config import COMMIT_LANGUAGES, CONFLICT_PROMPTS, PROMPT_PRESETS, AISettings


class AIPromptsDialog(QDialog):
    def __init__(self, settings: AISettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("AI Prompts")
        self.resize(820, 650)
        layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        layout.addWidget(self._tabs, 1)
        self._build_commit_tab(settings)
        self._build_conflict_tab(settings)
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._prompt.textChanged.connect(self._refresh_state)
        self._language.currentTextChanged.connect(self._refresh_state)
        self._conflict_prompt.textChanged.connect(self._refresh_state)
        self._conflict_language.currentTextChanged.connect(self._change_conflict_language)
        self._include_branch.toggled.connect(self._update_branch_options)
        self._branch_summary.toggled.connect(self._refresh_state)
        self._branch_description.toggled.connect(self._refresh_state)
        self._update_branch_options()

    def _build_commit_tab(self, settings: AISettings) -> None:
        page = QWidget()
        self._tabs.addTab(page, "Сообщение коммита")
        layout = QVBoxLayout(page)
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
        self._include_branch = QCheckBox("Добавлять название ветки")
        self._include_branch.setChecked(settings.include_branch_name)
        layout.addWidget(self._include_branch)
        self._branch_targets = QWidget()
        branch_layout = QHBoxLayout(self._branch_targets)
        branch_layout.setContentsMargins(24, 0, 0, 0)
        self._branch_summary = QCheckBox("В summary")
        self._branch_summary.setChecked(settings.branch_in_summary)
        self._branch_description = QCheckBox("В description")
        self._branch_description.setChecked(settings.branch_in_description)
        branch_layout.addWidget(self._branch_summary)
        branch_layout.addWidget(self._branch_description)
        branch_layout.addStretch()
        layout.addWidget(self._branch_targets)
        self._branch_hint = QLabel(
            "Имя ветки добавляется как [feature/name] в конце summary "
            "и/или отдельным абзацем в конце description. Выберите хотя бы одно поле."
        )
        self._branch_hint.setWordWrap(True)
        layout.addWidget(self._branch_hint)
        hint = QLabel(
            "Language and the summary/description response format are applied automatically. "
            "Git Flow uses the branch name as a hint; the staged diff takes precedence.",
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _build_conflict_tab(self, settings: AISettings) -> None:
        page = QWidget()
        self._tabs.addTab(page, "Разрешение конфликтов")
        layout = QVBoxLayout(page)
        language_row = QHBoxLayout()
        language_row.addWidget(QLabel("Язык промпта:"))
        self._conflict_language = QComboBox()
        self._conflict_language.addItems(CONFLICT_PROMPTS)
        language = (settings.conflict_language if settings.conflict_language in CONFLICT_PROMPTS
                    else "Русский")
        self._conflict_language.setCurrentText(language)
        self._current_conflict_language = language
        self._conflict_drafts = {language: settings.conflict_prompt}
        language_row.addWidget(self._conflict_language, 1)
        self._conflict_reset = QPushButton("Промпт по умолчанию")
        self._conflict_reset.clicked.connect(self._reset_conflict_prompt)
        language_row.addWidget(self._conflict_reset)
        layout.addLayout(language_row)
        self._conflict_prompt = QPlainTextEdit(settings.conflict_prompt)
        layout.addWidget(self._conflict_prompt, 1)
        hint = QLabel(
            "При выборе языка подставляется готовый промпт на этом языке. "
            "Его можно изменить; при переключении вкладок и языков правки остаются в диалоге. "
            "Сохранённый промпт подхватывается кнопкой «Решить с помощью AI». "
            "Язык промпта не меняет язык исходного файла."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

    def _update_branch_options(self) -> None:
        enabled = self._include_branch.isChecked()
        self._branch_targets.setVisible(enabled)
        self._branch_hint.setVisible(enabled)
        self._refresh_state()

    def _change_conflict_language(self, language: str) -> None:
        self._conflict_drafts[self._current_conflict_language] = self._conflict_prompt.toPlainText()
        self._current_conflict_language = language
        self._conflict_prompt.selectAll()
        self._conflict_prompt.insertPlainText(
            self._conflict_drafts.get(language, CONFLICT_PROMPTS[language]),
        )
        self._refresh_state()

    def _reset_conflict_prompt(self) -> None:
        self._conflict_prompt.selectAll()
        self._conflict_prompt.insertPlainText(CONFLICT_PROMPTS[self._conflict_language.currentText()])
        self._refresh_state()

    def _apply_preset(self, key: str) -> None:
        self._prompt.selectAll()
        self._prompt.insertPlainText(PROMPT_PRESETS[key][1])

    def _refresh_state(self) -> None:
        text = self._prompt.toPlainText().strip()
        for key, button in self._preset_buttons.items():
            button.setChecked(text == PROMPT_PRESETS[key][1])
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(text and self._language.currentText().strip()
                 and self._conflict_prompt.toPlainText().strip())
            and (not self._include_branch.isChecked()
                 or self._branch_summary.isChecked() or self._branch_description.isChecked()),
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
            conflict_prompt=self._conflict_prompt.toPlainText().strip(),
            conflict_language=self._conflict_language.currentText(),
            include_branch_name=self._include_branch.isChecked(),
            branch_in_summary=self._branch_summary.isChecked(),
            branch_in_description=self._branch_description.isChecked(),
        )
