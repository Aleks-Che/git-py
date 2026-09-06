"""Passive settings form for OpenAI-compatible model servers."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.dialogs.ai_prompts_dialog import AIPromptsDialog
from src.utils.ai_config import PROMPT_PRESETS, PROVIDER_URLS, AISettings
from src.viewmodels.ai_settings_viewmodel import AISettingsViewModel


class ModelComboWidget(QComboBox):
    popup_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().installEventFilter(self)

    def showPopup(self) -> None:  # noqa: N802
        self.popup_requested.emit()
        super().showPopup()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self.showPopup()
        return super().eventFilter(watched, event)

    def show_loaded_models(self) -> None:
        if self.view().isVisible():
            super().showPopup()


class AISettingsPanel(QWidget):
    def __init__(self, settings: AISettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prompt_settings = settings
        self._vm = AISettingsViewModel(self)
        layout = QVBoxLayout(self)
        intro = QLabel("Generate a commit summary and description from staged changes.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self._provider = QComboBox()
        self._provider.setEditable(True)
        self._provider.addItems(PROVIDER_URLS)
        self._provider.setCurrentText(settings.provider)
        form.addRow("Provider:", self._provider)
        self._url = QLineEdit(settings.base_url)
        self._url.setPlaceholderText("https://your-provider.example/v1")
        self._url.setToolTip("API base URL, including /v1 or the provider's API prefix.")
        form.addRow("OpenAI-compatible URL:", self._url)
        self._api_key = QLineEdit(settings.api_key)
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText("API key (optional for local servers)")
        self._api_key.setToolTip("Saved in the local app configuration file when you click OK.")
        form.addRow("API key:", self._api_key)
        self._model = ModelComboWidget()
        self._model.setCurrentText(settings.model)
        self._model.lineEdit().setPlaceholderText("Click to load models, or type a model ID")
        form.addRow("Model:", self._model)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self._test_button = QPushButton("Test connection")
        self._test_button.clicked.connect(lambda: self._vm.test_connection(self.settings()))
        buttons.addWidget(self._test_button)
        self._prompts_button = QPushButton("Edit prompts…")
        self._prompts_button.clicked.connect(self._edit_prompts)
        buttons.addWidget(self._prompts_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self._prompt_label = QLabel()
        self._prompt_label.setWordWrap(True)
        layout.addWidget(self._prompt_label)
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        layout.addWidget(self._progress)
        self._status = QLabel(
            "Enter the API URL, then click the model field to load available models."
        )
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._status)
        layout.addStretch()
        note = QLabel(
            "Generation sends staged diffs and the branch name to this server. "
            "Test connection sends only a short test message. Settings are saved with OK.",
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self._model.popup_requested.connect(self._load_models)
        self._provider.currentTextChanged.connect(self._provider_changed)
        self._url.textChanged.connect(self._connection_changed)
        self._api_key.textChanged.connect(self._connection_changed)
        self._model.editTextChanged.connect(self._vm.invalidate_test)
        self._vm.models_ready.connect(self._show_models)
        self._vm.test_succeeded.connect(self._status.setText)
        self._vm.error_occurred.connect(self._show_error)
        self._vm.busy_changed.connect(self._show_busy)
        self._refresh_prompt_label()

    def settings(self) -> AISettings:
        return replace(
            self._prompt_settings,
            provider=self._provider.currentText().strip(),
            base_url=self._url.text().strip(),
            api_key=self._api_key.text().strip(),
            model=self._model.currentText().strip(),
        )

    def _provider_changed(self, provider: str) -> None:
        # Prefill standard URLs; preserve any user-supplied proxy or custom API prefix.
        current = self._url.text().strip().rstrip("/")
        if provider in PROVIDER_URLS and current in PROVIDER_URLS.values():
            self._url.setText(PROVIDER_URLS[provider])
        self._connection_changed()

    def _connection_changed(self) -> None:
        self._vm.invalidate_requests()
        current = self._model.currentText()
        self._model.blockSignals(True)
        self._model.clear()
        self._model.setCurrentText(current)
        self._model.blockSignals(False)
        self._status.setText("Connection settings changed. Click the model field to reload models.")

    def _load_models(self) -> None:
        if self._vm.is_busy:
            return
        # A disabled placeholder keeps the dropdown open while the first request runs.
        current = self._model.currentText()
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItem("Loading models…")
        self._model.model().item(0).setEnabled(False)
        self._model.setCurrentIndex(-1)
        self._model.setCurrentText(current)
        self._model.blockSignals(False)
        self._vm.load_models(self.settings())

    def _show_models(self, models: list[str]) -> None:
        current = self._model.currentText()
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItems(models)
        self._model.setCurrentIndex(-1)
        self._model.setCurrentText(current)
        self._model.blockSignals(False)
        self._model.show_loaded_models()
        self._status.setText(f"{len(models)} models available. Select a chat model, then test it.")

    def _show_busy(self, busy: bool) -> None:
        self._progress.setVisible(busy)
        self._test_button.setEnabled(not busy)
        if busy:
            self._status.setText("Contacting the model server…")

    def _show_error(self, message: str) -> None:
        if self._model.count() == 1 and not self._model.model().item(0).isEnabled():
            self._model.setItemText(0, "No models loaded — enter a model ID manually")
        self._status.setText(message)

    def _edit_prompts(self) -> None:
        dialog = AIPromptsDialog(self.settings(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._prompt_settings = dialog.settings()
            self._refresh_prompt_label()

    def _refresh_prompt_label(self) -> None:
        style = PROMPT_PRESETS.get(self._prompt_settings.preset, ("Custom", ""))[0]
        self._prompt_label.setText(
            f"Commit message language: {self._prompt_settings.language} · Style: {style}",
        )
