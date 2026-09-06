"""Asynchronous drafts must never overwrite a changed repository or newer user text."""

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QThread
from src.utils.ai_client import AIClient, AIError, CommitMessage
from src.utils.ai_config import AISettings
from src.utils.config import save_config
from src.viewmodels.ai_settings_viewmodel import AISettingsViewModel
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel


@pytest.fixture
def generation(qtbot, committed_repo, tmp_path, monkeypatch):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("staged generation input\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    config_path = tmp_path / "ai.json"
    save_config(
        config_path, {"ai": AISettings(base_url="http://localhost/v1", model="test").to_dict()}
    )
    vm = CommitPanelViewModel(config_path=config_path)
    vm.set_repository(committed_repo)
    entered, release = threading.Event(), threading.Event()
    captured = []

    def generate(client, diff, branch):
        captured.append((diff, branch, QThread.currentThread(), client.settings))
        entered.set()
        if not release.wait(5):
            raise AIError("Test worker was not released")
        return CommitMessage("feat: generated", "Generated description")

    monkeypatch.setattr(AIClient, "generate_commit_message", generate)
    try:
        yield vm, committed_repo, entered, release, captured, config_path
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm.is_generating, timeout=6000)


def test_generation_fills_both_fields_off_gui_thread_and_rejects_reentry(qtbot, generation):
    vm, _, entered, release, captured, _ = generation
    messages, busy = [], []
    vm.commit_message_changed.connect(messages.append)
    vm.generation_busy_changed.connect(busy.append)
    vm.generate_commit_message()
    qtbot.waitUntil(entered.is_set)
    vm.generate_commit_message()
    assert vm.is_generating and len(captured) == 1
    assert captured[0][2] != vm.thread()
    assert "+staged generation input" in captured[0][0]
    release.set()
    qtbot.waitUntil(lambda: not vm.is_generating)
    assert vm.commit_summary() == "feat: generated"
    assert vm.commit_description() == "Generated description"
    assert messages == ["feat: generated\n\nGenerated description"]
    assert busy == [True, False]


@pytest.mark.parametrize("change", ["summary", "description", "repository", "index", "branch"])
def test_stale_result_does_not_overwrite_current_input(qtbot, generation, change):
    vm, manager, entered, release, _, _ = generation
    vm.set_commit_summary("original")
    vm.set_commit_description("original body")
    vm.generate_commit_message()
    qtbot.waitUntil(entered.is_set)
    if change == "summary":
        vm.set_commit_summary("my newer title")
    elif change == "description":
        vm.set_commit_description("my newer body")
    elif change == "repository":
        # Even rebinding the same repo must invalidate the old draft.
        vm.set_repository(manager)
    elif change == "index":
        (Path(manager.path) / "hello.txt").write_text("another staged version\n")
        manager.repo.index.add("hello.txt")
        manager.repo.index.write()
    else:
        manager.repo.branches.local.create("topic", manager.repo[manager.repo.head.target])
        manager.repo.set_head("refs/heads/topic")
    expected = vm.combined_commit_message()
    release.set()
    qtbot.waitUntil(lambda: not vm.is_generating)
    assert vm.combined_commit_message() == expected


def test_generation_error_preserves_draft_and_allows_retry(qtbot, generation, monkeypatch):
    vm, _, _, _, _, _ = generation
    vm.set_commit_summary("keep me")
    vm.set_commit_description("keep body")

    def fail(*_args):
        raise AIError("Authentication failed")

    monkeypatch.setattr(AIClient, "generate_commit_message", fail)
    with qtbot.waitSignal(vm.error_occurred) as signal:
        vm.generate_commit_message()
    qtbot.waitUntil(lambda: not vm.is_generating)
    assert signal.args == ["Authentication failed"]
    assert vm.combined_commit_message() == "keep me\n\nkeep body"
    monkeypatch.setattr(AIClient, "generate_commit_message", lambda *_: CommitMessage("OK", "body"))
    vm.generate_commit_message()
    qtbot.waitUntil(lambda: not vm.is_generating)
    assert vm.commit_summary() == "OK"


def test_generation_reloads_saved_settings(qtbot, generation):
    vm, _, _, release, captured, config_path = generation
    save_config(
        config_path,
        {
            "ai": AISettings(
                base_url="http://localhost/v1",
                model="changed-model",
                language="Deutsch",
            ).to_dict()
        },
    )
    release.set()
    vm.generate_commit_message()
    qtbot.waitUntil(lambda: not vm.is_generating)
    assert captured[0][3].model == "changed-model"
    assert captured[0][3].language == "Deutsch"


def test_model_discovery_ignores_response_for_old_endpoint(qtbot, monkeypatch):
    vm = AISettingsViewModel()
    entered, release = threading.Event(), threading.Event()
    results, calls = [], []

    def models(client):
        calls.append(client.settings.base_url)
        entered.set()
        release.wait(5)
        return ["old-model"]

    monkeypatch.setattr(AIClient, "list_models", models)
    vm.models_ready.connect(results.append)
    settings = AISettings(base_url="http://localhost/v1")
    try:
        vm.load_models(settings)
        qtbot.waitUntil(entered.is_set)
        vm.load_models(settings)
        assert len(calls) == 1
        vm.invalidate_requests()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm.is_busy)
    assert results == []


def test_model_test_failure_resets_busy_and_does_not_report_success(qtbot, monkeypatch):
    vm = AISettingsViewModel()
    success = []
    vm.test_succeeded.connect(success.append)

    def fail(_client):
        raise AIError("No access to this model")

    monkeypatch.setattr(AIClient, "test_connection", fail)
    with qtbot.waitSignal(vm.error_occurred) as signal:
        vm.test_connection(AISettings(base_url="http://localhost/v1", model="test"))
    qtbot.waitUntil(lambda: not vm.is_busy)
    assert signal.args == ["No access to this model"]
    assert not success
