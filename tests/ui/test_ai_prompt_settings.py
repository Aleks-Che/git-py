"""Prompt tabs, per-language drafts and settings-to-conflict integration."""
import json

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox
from src.core.conflict_resolution import ConflictSnapshot
from src.ui.dialogs.ai_conflict_dialog import AIConflictDialog
from src.ui.dialogs.ai_prompts_dialog import AIPromptsDialog
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.utils.ai_client import AIClient
from src.utils.ai_config import COMMIT_LANGUAGES, CONFLICT_PROMPTS, AISettings
from src.utils.config import load_config, save_config
from src.viewmodels.conflict_editor_viewmodel import ConflictEditorViewModel


def test_tabs_and_branch_targets_visibility(qtbot):
    dialog = AIPromptsDialog(AISettings())
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog._tabs.count() == 2
    assert dialog._tabs.tabText(0) == "Сообщение коммита"
    assert dialog._tabs.tabText(1) == "Разрешение конфликтов"
    assert dialog._branch_targets.isHidden()
    dialog._include_branch.click()
    assert dialog._branch_targets.isVisible()
    assert dialog.settings().include_branch_name
    dialog._branch_summary.setChecked(False)
    assert not dialog._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    dialog._branch_description.click()
    assert dialog._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
    settings = dialog.settings()
    assert not settings.branch_in_summary and settings.branch_in_description
    dialog._include_branch.click()
    assert dialog._branch_targets.isHidden()
    assert not dialog.settings().include_branch_name
    dialog._include_branch.click()
    assert dialog._branch_description.isChecked()  # preserve the user's targets


@pytest.mark.parametrize("language", COMMIT_LANGUAGES)
def test_conflict_language_inserts_localized_default(qtbot, language):
    dialog = AIPromptsDialog(AISettings())
    qtbot.addWidget(dialog)
    dialog._tabs.setCurrentIndex(1)
    dialog._conflict_language.setCurrentText(language)
    assert dialog._conflict_prompt.toPlainText() == CONFLICT_PROMPTS[language]
    assert dialog.settings().conflict_language == language
    assert dialog.settings().language == "Русский"  # commit language is independent
    assert dialog.settings().commit_prompt == AISettings().commit_prompt


def test_language_switch_and_reset_keep_editing_reversible(qtbot):
    dialog = AIPromptsDialog(AISettings(conflict_prompt="Custom Russian prompt"))
    qtbot.addWidget(dialog)
    dialog._conflict_language.setCurrentText("English")
    dialog._conflict_prompt.setPlainText("Custom English prompt")
    dialog._conflict_language.setCurrentText("Русский")
    assert dialog._conflict_prompt.toPlainText() == "Custom Russian prompt"
    dialog._conflict_language.setCurrentText("English")
    assert dialog._conflict_prompt.toPlainText() == "Custom English prompt"
    dialog._conflict_reset.click()
    assert dialog._conflict_prompt.toPlainText() == CONFLICT_PROMPTS["English"]
    dialog._conflict_prompt.undo()
    assert dialog._conflict_prompt.toPlainText() == "Custom English prompt"
    dialog._conflict_prompt.clear()
    assert not dialog._buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()


def test_settings_save_reopen_and_conflict_request_use_the_same_prompt(
    qtbot, tmp_path, monkeypatch,
):
    path = tmp_path / "config.json"
    save_config(path, {"ai": AISettings(base_url="http://localhost/v1", model="test",
                                       conflict_context="keep both").to_dict(), "other": 7})
    settings = SettingsDialog(str(path))
    qtbot.addWidget(settings)

    def configure(dialog):
        dialog._conflict_language.setCurrentText("Deutsch")
        dialog._conflict_prompt.insertPlainText("Custom German instruction. ")
        dialog._include_branch.setChecked(True)
        dialog._branch_description.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AIPromptsDialog, "exec", configure)
    settings._ai_panel._prompts_button.click()
    settings._on_accept()
    reopened = SettingsDialog(str(path))
    qtbot.addWidget(reopened)
    saved = reopened._ai_panel.settings()
    assert saved == settings._ai_panel.settings()
    assert saved.conflict_language == "Deutsch"
    assert saved.include_branch_name and saved.branch_in_summary and saved.branch_in_description
    assert saved.conflict_context == "keep both"
    assert load_config(path)["other"] == 7

    vm = ConflictEditorViewModel(config_path=path)
    vm.set_snapshot(ConflictSnapshot("f", b"old\n", b"left\n", b"right\n"))
    prompt_dialog = AIConflictDialog(vm.ai_settings())
    qtbot.addWidget(prompt_dialog)
    assert prompt_dialog.prompt_edit.toPlainText() == saved.conflict_prompt
    calls = []

    def complete(client, messages):
        calls.append((client.settings, messages[0]["content"]))
        return json.dumps({"resolutions": [{"id": 0, "content": "resolved\n"}]})

    monkeypatch.setattr(AIClient, "complete", complete)
    with qtbot.waitSignal(vm.ai_finished):
        vm.resolve_using_ai(prompt_dialog.prompt_edit.toPlainText(), "keep both")
    qtbot.waitUntil(lambda: not vm._ai._workers)
    assert calls[0][0].conflict_language == "Deutsch"
    assert calls[0][1].startswith(saved.conflict_prompt)
    assert vm.result_text() == "resolved\n"


@pytest.mark.parametrize("accept_prompts", [False, True])
def test_cancel_preserves_saved_prompts_and_branch_flags(
    qtbot, tmp_path, monkeypatch, accept_prompts,
):
    path = tmp_path / "config.json"
    original = AISettings(conflict_prompt="Saved custom prompt")
    save_config(path, {"ai": original.to_dict()})
    settings = SettingsDialog(str(path))
    qtbot.addWidget(settings)

    def edit(dialog):
        dialog._conflict_language.setCurrentText("Français")
        dialog._include_branch.setChecked(True)
        return QDialog.DialogCode.Accepted if accept_prompts else QDialog.DialogCode.Rejected

    monkeypatch.setattr(AIPromptsDialog, "exec", edit)
    settings._ai_panel._prompts_button.click()
    if accept_prompts:
        settings.reject()  # outer Cancel still discards an accepted inner dialog
    else:
        assert settings._ai_panel.settings() == original
        settings._on_accept()
    assert AISettings.from_config(load_config(path)) == original
