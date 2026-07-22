"""Regression tests for explicit subprocess text encoding in Git operations."""
from __future__ import annotations

import subprocess
from pathlib import Path

from src.core import operations
from src.core.repository import RepositoryManager


def test_subprocess_runs_use_utf8_encoding_replace(
    tmp_git_repo: Path,
    monkeypatch,
) -> None:
    """The shared Git CLI path always decodes process output as UTF-8 safely."""
    manager = RepositoryManager(str(tmp_git_repo))
    captured_kwargs: dict[str, object] = {}

    def fake_run(*_args, **kwargs) -> subprocess.CompletedProcess[str]:
        captured_kwargs.update(kwargs)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(operations.shutil, "which", lambda _name: "git")
    monkeypatch.setattr(operations.subprocess, "run", fake_run)

    operations._run_git_in_workdir(manager.repo, ["status"])

    assert captured_kwargs["encoding"] == "utf-8"
    assert captured_kwargs["errors"] == "replace"
