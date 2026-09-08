"""Configured SSH identity reaches synchronous and background network operations."""
from __future__ import annotations

import shlex
import subprocess

import pygit2
import pytest
from src.core import operations
from src.utils.config import load_config, save_config, save_ssh_key_paths
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.mark.parametrize("async_enabled", [False, True])
@pytest.mark.parametrize("action", ["push", "fetch", "pull", "checkout", "reset", "clone"])
def test_network_operations_reload_selected_key(
    qtbot, committed_repo, tmp_path, monkeypatch, action, async_enabled,
):
    config_path = tmp_path / "settings.json"
    save_config(config_path, {"ssh_private_key": "old-key", "custom_setting": "keep"})
    vm = MainViewModel(config_path=config_path, async_enabled=async_enabled)
    repo = committed_repo.repo
    url = "git@example.invalid:team/repo.git"
    repo.remotes.create("origin", url)
    repo.references.create("refs/remotes/origin/main", repo.head.target)
    repo.branches.local["main"].upstream = repo.branches.remote["origin/main"]
    vm.set_repository(committed_repo)
    errors = []
    vm.error_occurred.connect(errors.append)
    calls = []
    original_run = subprocess.run

    def run(args, **kwargs):
        if args[1] not in {"push", "fetch", "clone"}:
            return original_run(args, **kwargs)
        calls.append(shlex.split(kwargs["env"]["GIT_SSH_COMMAND"]))
        if args[1] == "clone":
            pygit2.init_repository(args[-1], initial_head="main")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(operations.subprocess, "run", run)
    # Change settings twice with the same live ViewModel. The second update
    # represents Settings or an external config edit, not a VM cache update.
    for index in range(2):
        key = tmp_path / f"selected key {index}"
        key.write_text("test private key", encoding="utf-8")
        if index == 0:
            vm.configure_ssh_key(str(key), str(key) + ".pub")
        else:
            save_ssh_key_paths(config_path, str(key), str(key) + ".pub")
        if action == "clone":
            vm.clone_repository(url, str(tmp_path / f"cloned-{index}"))
        elif action == "checkout":
            vm.fetch_and_checkout_remote_branch("origin/main")
        elif action == "reset":
            vm.reset_local_branch_to_remote("origin/main")
        else:
            getattr(vm, f"{action}_changes")()
        qtbot.waitUntil(lambda: not vm.is_busy(), timeout=5000)
        assert not errors
        assert len(calls) == index + 1
        assert calls[-1][calls[-1].index("-i") + 1] == key.as_posix()
    assert load_config(config_path)["custom_setting"] == "keep"
    vm.close_repository()


def test_configure_ssh_key_reports_save_failure(qtbot, tmp_path, monkeypatch):
    vm = MainViewModel(config_path=tmp_path / "settings.json")
    errors = []
    vm.error_occurred.connect(errors.append)

    def fail(*args):
        raise PermissionError("settings read-only")

    monkeypatch.setattr("src.viewmodels.main_viewmodel.save_ssh_key_paths", fail)
    vm.configure_ssh_key("private", "public")
    assert errors and "settings read-only" in errors[0]


@pytest.mark.parametrize("async_enabled", [False, True])
def test_push_reloads_timeout_and_can_retry_after_timeout(
    qtbot, committed_repo, tmp_path, monkeypatch, async_enabled,
):
    config_path = tmp_path / "settings.json"
    # An existing config without the new key also gets the extended default.
    save_config(config_path, {"custom_setting": "keep"})
    vm = MainViewModel(config_path=config_path, async_enabled=async_enabled)
    committed_repo.repo.remotes.create("origin", "git@example.invalid:team/repo.git")
    vm.set_repository(committed_repo)
    errors = []
    vm.error_occurred.connect(errors.append)
    calls = []
    original_run = subprocess.run

    def run(args, **kwargs):
        if args[1] != "push":
            return original_run(args, **kwargs)
        calls.append(kwargs["timeout"])
        if len(calls) == 2:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(operations.subprocess, "run", run)
    try:
        for index, timeout in enumerate([1800, 3600, 7200]):
            if index:
                config = load_config(config_path)
                config["push_timeout_seconds"] = timeout
                save_config(config_path, config)
            vm.push_changes()
            qtbot.waitUntil(lambda: not vm.is_busy(), timeout=5000)
            assert calls == [1800, 3600, 7200][:index + 1]
            assert errors == ([] if index == 0 else [
                "git push origin HEAD timed out after 3600s",
            ])
        assert not vm.command_processor().can_undo
    finally:
        vm.close_repository()
