"""AI settings, prompt editing and commit-panel integration without external services."""

import threading
from dataclasses import replace
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit
from src.ui.dialogs.ai_prompts_dialog import AIPromptsDialog
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.ui.widgets.commit_panel import CommitPanel
from src.utils.ai_client import AIClient, AIError, CommitMessage
from src.utils.ai_config import PROMPT_PRESETS, AISettings
from src.utils.config import load_config, save_config
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.mark.parametrize("generate_message", [False, True])
@pytest.mark.parametrize("stage_in_client", [False, True])
def test_commit_after_external_commit_and_background_refresh(
    qtbot, committed_repo, tmp_path, monkeypatch, generate_message, stage_in_client,
):
    root = Path(committed_repo.path)
    config_path = tmp_path / "settings.json"
    save_config(config_path, {
        "use_default_git_credentials": False,
        "author_name": "Actual User",
        "author_email": "user@example.com",
        "ai": AISettings(base_url="http://localhost/v1", model="test").to_dict(),
    })
    vm = MainViewModel(config_path=config_path, async_enabled=True)
    vm.set_repository(committed_repo)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    errors = []
    vm.error_occurred.connect(errors.append)
    old_tree = committed_repo.repo.index.write_tree()

    # GitKraken/CLI advances HEAD while the application keeps its old index.
    external = pygit2.Repository(str(root))
    signature = pygit2.Signature("External User", "external@example.com")
    (root / "external.txt").write_bytes(b"keep the external commit\n")
    (root / "deleted.txt").write_bytes(b"delete in the next commit\n")
    external.index.add_all()
    external.index.write()
    external_head = external.create_commit(
        "HEAD", signature, signature, "external commit", external.index.write_tree(),
        [external.head.target],
    )
    (root / "hello.txt").write_bytes(b"next version\n")
    (root / "added.txt").write_bytes(b"new file\n")
    (root / "deleted.txt").unlink()
    expected_index = pygit2.Repository(str(root)).index
    expected_index.add_all()
    expected_tree = expected_index.write_tree()
    if not stage_in_client:
        expected_index.write()

    # The real background refresh updates displayed data via its own handle.
    vm.load_repository_data()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=5000)
    if stage_in_client:
        assert panel._stage_all_button.isEnabled()
        panel._stage_all_button.click()
    assert not vm.commit_panel_view_model().unstaged_paths()
    assert set(vm.commit_panel_view_model().staged_files()) == {
        "hello.txt", "added.txt", "deleted.txt",
    }
    index_path = Path(external.path) / "index"
    index_before_generation = index_path.read_bytes()
    head_before_generation = external.head.target
    messages = []

    def generate(_client, diff, _branch):
        messages.append(diff)
        return CommitMessage("feat: next changes", "Generated from the staged files")

    monkeypatch.setattr(AIClient, "generate_commit_message", generate)
    if generate_message:
        panel._generate_action.trigger()
        qtbot.waitUntil(lambda: not vm.commit_panel_view_model().is_generating, timeout=5000)
        assert len(messages) == 1
        assert 'Added: "added.txt"' in messages[0]
        assert 'Deleted: "deleted.txt"' in messages[0]
        assert "external.txt" not in messages[0]
    else:
        panel._summary.setText("Manual message")
    assert index_path.read_bytes() == index_before_generation
    assert external.head.target == head_before_generation
    assert not vm.command_processor().can_undo
    assert panel._commit_button.isEnabled()
    panel._commit_button.click()

    fresh = pygit2.Repository(str(root))
    commit = fresh.head.peel()
    assert not errors
    assert commit.parent_ids == [external_head]
    assert commit.tree_id == expected_tree != old_tree
    assert fresh.status() == {}
    assert commit.author == commit.committer
    assert commit.committer.email == "user@example.com"
    assert vm.commit_panel_view_model().file_changes() == []
    assert vm.command_processor().can_undo


def test_settings_round_trip_preserves_unrelated_keys_and_masks_key(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    save_config(path, {"unrelated": 7, "ai": {"extension": True}})
    dialog = SettingsDialog(str(path))
    qtbot.addWidget(dialog)
    ai = dialog._ai_panel
    ai._provider.setCurrentText("Custom provider")
    ai._url.setText("https://proxy.example/api/v1")
    ai._api_key.setText("secret")
    ai._model.setCurrentText("manual-model")
    ai._prompt_settings = replace(ai._prompt_settings, language="English", commit_prompt="Custom")
    assert ai._api_key.echoMode() == QLineEdit.EchoMode.Password
    dialog._on_accept()
    config = load_config(path)
    assert config["unrelated"] == 7 and config["ai"]["extension"] is True
    reopened = SettingsDialog(str(path))
    qtbot.addWidget(reopened)
    assert reopened._ai_panel.settings() == ai.settings()


def test_cancel_discards_ai_changes_including_prompt_edits(qtbot, tmp_path):
    path = tmp_path / "settings.json"
    settings = AISettings(base_url="https://saved.example/v1", commit_prompt="Saved prompt")
    save_config(path, {"ai": settings.to_dict()})
    dialog = SettingsDialog(str(path))
    qtbot.addWidget(dialog)
    dialog._ai_panel._url.setText("https://new.example/v1")
    dialog._ai_panel._prompt_settings = replace(settings, commit_prompt="Discard this")
    dialog.reject()
    assert AISettings.from_config(load_config(path)) == settings


def test_click_model_field_loads_dropdown_and_preserves_manual_name(qtbot, tmp_path, monkeypatch):
    dialog = SettingsDialog(str(tmp_path / "settings.json"))
    qtbot.addWidget(dialog)
    dialog._tabs.setCurrentIndex(1)
    dialog.show()
    ai = dialog._ai_panel
    ai._url.setText("http://localhost/v1")
    ai._model.setCurrentText("saved-model")
    calls = []

    def models(client):
        calls.append(client.settings.base_url)
        return ["chat-a", "chat-b"]

    monkeypatch.setattr(AIClient, "list_models", models)
    qtbot.mouseClick(ai._model.lineEdit(), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not ai._vm.is_busy)
    assert calls == ["http://localhost/v1"]
    assert [ai._model.itemText(i) for i in range(ai._model.count())] == ["chat-a", "chat-b"]
    assert ai._model.currentText() == "saved-model"
    assert ai._model.view().isVisible()
    index = ai._model.model().index(1, 0)
    qtbot.mouseClick(
        ai._model.view().viewport(),
        Qt.MouseButton.LeftButton,
        pos=ai._model.view().visualRect(index).center(),
    )
    assert ai._model.currentText() == "chat-b"


def test_failed_model_discovery_keeps_manual_entry_usable(qtbot, tmp_path, monkeypatch):
    dialog = SettingsDialog(str(tmp_path / "settings.json"))
    qtbot.addWidget(dialog)
    ai = dialog._ai_panel
    ai._url.setText("http://localhost/v1")
    ai._model.setCurrentText("manual-model")

    def fail(_client):
        raise AIError("Models endpoint unavailable")

    monkeypatch.setattr(AIClient, "list_models", fail)
    ai._load_models()
    qtbot.waitUntil(lambda: not ai._vm.is_busy)
    assert ai._model.currentText() == "manual-model"
    assert "unavailable" in ai._status.text()
    assert "Loading" not in ai._model.itemText(0)
    assert ai._test_button.isEnabled()


def test_test_button_uses_unsaved_form_settings(qtbot, tmp_path, monkeypatch):
    dialog = SettingsDialog(str(tmp_path / "settings.json"))
    qtbot.addWidget(dialog)
    ai = dialog._ai_panel
    ai._url.setText("http://localhost/custom")
    ai._model.setCurrentText("new-model")
    captured = []

    def test(client):
        captured.append(client.settings)
        return "Model responded"

    monkeypatch.setattr(AIClient, "test_connection", test)
    ai._test_button.click()
    qtbot.waitUntil(lambda: not ai._vm.is_busy)
    assert captured[0].base_url == "http://localhost/custom"
    assert captured[0].model == "new-model"
    assert ai._status.text() == "Model responded"
    assert not (tmp_path / "settings.json").exists()


def test_prompts_presets_language_and_custom_text(qtbot):
    dialog = AIPromptsDialog(AISettings(commit_prompt="Original custom text"))
    qtbot.addWidget(dialog)
    for key in PROMPT_PRESETS:
        dialog._preset_buttons[key].click()
        assert dialog.settings().preset == key
        assert dialog.settings().commit_prompt == PROMPT_PRESETS[key][1]
    dialog._language.setCurrentText("English")
    dialog._prompt.setPlainText("My own team instructions")
    assert dialog.settings().preset == "custom"
    assert dialog.settings().language == "English"
    assert dialog.settings().commit_prompt == "My own team instructions"


def test_edit_prompts_button_applies_only_accepted_dialog(qtbot, tmp_path, monkeypatch):
    dialog = SettingsDialog(str(tmp_path / "settings.json"))
    qtbot.addWidget(dialog)

    def accept(editor):
        editor._language.setCurrentText("Deutsch")
        editor._preset_buttons["git_flow"].click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AIPromptsDialog, "exec", accept)
    dialog._ai_panel._prompts_button.click()
    assert dialog._ai_panel.settings().language == "Deutsch"
    assert dialog._ai_panel.settings().preset == "git_flow"


def test_sparkles_generate_both_fields_and_enable_commit(
    qtbot, committed_repo, tmp_path, monkeypatch
):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("staged change\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    path = tmp_path / "settings.json"
    save_config(path, {"ai": AISettings(base_url="http://localhost/v1", model="test").to_dict()})
    vm = MainViewModel(config_path=path)
    vm.set_repository(committed_repo)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    entered, release = threading.Event(), threading.Event()

    def generate(*_args):
        entered.set()
        release.wait(5)
        return CommitMessage("feat: staged change", "Details of the staged change")

    monkeypatch.setattr(AIClient, "generate_commit_message", generate)
    assert panel._generate_action in panel._summary.actions()
    assert panel._generate_action.isEnabled()
    assert not panel._commit_button.isEnabled()
    head = committed_repo.repo.head.target
    try:
        panel._generate_action.trigger()
        qtbot.waitUntil(entered.is_set)
        assert not panel._generate_action.isEnabled()
        assert panel._generation_timer.isActive()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm.commit_panel_view_model().is_generating)
    assert panel._summary.text() == "feat: staged change"
    assert panel._description.toPlainText() == "Details of the staged change"
    assert panel._commit_button.isEnabled()
    assert panel._generate_action.isEnabled()
    assert not panel._generation_timer.isActive()
    assert committed_repo.repo.head.target == head
    panel._commit_button.click()
    assert committed_repo.repo.head.peel().message == (
        "feat: staged change\n\nDetails of the staged change"
    )
