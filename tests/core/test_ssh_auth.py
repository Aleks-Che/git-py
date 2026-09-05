"""SSH transport regressions; subprocesses are intercepted before any network I/O."""
from __future__ import annotations

import os
import shlex
import subprocess

import pytest
from src.core import operations
from src.core.exceptions import AuthError


@pytest.mark.parametrize("action", ["clone", "push", "fetch"])
def test_ssh_transports_keep_environment_and_select_key(
    action, committed_repo, tmp_path, monkeypatch,
):
    key = tmp_path / "keys with spaces" / "key's $literal`name"
    key.parent.mkdir()
    key.write_text("test private key", encoding="utf-8")
    url = "git@example.invalid:team/repo.git"
    committed_repo.repo.remotes.create("origin", url)
    monkeypatch.setenv("SSH_AUTH_SOCK", "test-agent-socket")
    monkeypatch.setenv("GIT_SSH_VARIANT", "plink")
    monkeypatch.setenv("GIT_SSH_COMMAND", "previous-ssh-command")
    captured = []

    def run(args, **kwargs):
        captured.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(operations.subprocess, "run", run)
    if action == "clone":
        operations._clone_via_cli(url, str(tmp_path / "clone"), ssh_key_path=str(key))
    else:
        getattr(operations, action)(committed_repo, ssh_key_path=str(key))

    args, kwargs = captured[0]
    assert action in args
    env = kwargs["env"]
    for name in ("PATH", "SYSTEMROOT", "HOME", "USERPROFILE", "SSH_AUTH_SOCK"):
        if name in os.environ:
            assert env[name] == os.environ[name]
    command = shlex.split(env["GIT_SSH_COMMAND"])
    assert command[command.index("-i") + 1] == key.as_posix()
    assert "IdentitiesOnly=yes" in command
    assert env["GIT_SSH_VARIANT"] == "ssh"
    assert os.environ["GIT_SSH_COMMAND"] == "previous-ssh-command"


@pytest.mark.parametrize("action", ["clone", "push", "fetch"])
def test_missing_configured_key_fails_before_network(action, committed_repo, tmp_path, monkeypatch):
    url = "git@example.invalid:team/repo.git"
    committed_repo.repo.remotes.create("origin", url)
    calls = []
    monkeypatch.setattr(operations.subprocess, "run", lambda *a, **k: calls.append(a))
    key = str(tmp_path / "missing-key")
    with pytest.raises(AuthError, match="SSH private key not found"):
        if action == "clone":
            operations._clone_via_cli(url, str(tmp_path / "clone"), ssh_key_path=key)
        else:
            getattr(operations, action)(committed_repo, ssh_key_path=key)
    assert not calls


def test_push_uses_ssh_push_url_when_fetch_url_is_https(committed_repo, tmp_path, monkeypatch):
    repo = committed_repo.repo
    repo.remotes.create("origin", "https://example.invalid/team/repo.git")
    repo.remotes.set_push_url("origin", "git@example.invalid:team/repo.git")
    key = tmp_path / "selected-key"
    key.write_text("test private key", encoding="utf-8")
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(operations.subprocess, "run", run)
    operations.push(committed_repo, ssh_key_path=str(key))
    assert key.as_posix() in calls[0]["GIT_SSH_COMMAND"]


def test_no_selected_key_preserves_system_ssh_selection():
    assert operations._ssh_environment(None) is None


def test_ssh_key_path_expands_home(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # expanduser uses platform environment variables, not Path.home().
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    key = tmp_path / ".ssh" / "selected"
    key.parent.mkdir()
    key.write_text("test private key", encoding="utf-8")
    command = shlex.split(operations._ssh_environment("~/.ssh/selected")["GIT_SSH_COMMAND"])
    assert command[command.index("-i") + 1] == key.as_posix()
