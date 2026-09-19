"""A partially completed stash must refresh panels and preserve its diagnostic."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pygit2
import pytest
from src.core import operations
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.mark.parametrize("method", ["stash_push", "stash_push_staged", "stash_single_file"])
def test_partial_stash_refreshes_panels_and_emits_readable_error(
    committed_repo, monkeypatch, qapp, method,
):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("changed\n")
    (root / "removed.txt").write_text("also saved\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    vm = MainViewModel(auto_fetch_enabled=False)
    vm.set_repository(committed_repo)
    vm.stop_worktree_refresh()
    errors = []
    logs = []
    vm.error_occurred.connect(errors.append)
    vm.log_message.connect(logs.append)
    original_stash = pygit2.Repository.stash
    detail = (
        f"could not rmdir '{root.as_posix()}/scripts/': "
        "The process cannot access the file because it is being used by another process."
    )

    def partial_stash(repo, signature, message, **kwargs):
        # Save real Git objects and remove one file before simulating failure.
        # The archive includes that file, so refresh must show its disappearance.
        kwargs.pop("paths", None)
        original_stash(repo, signature, message, keep_all=True, **kwargs)
        (root / "removed.txt").unlink()
        raise pygit2.GitError(detail)

    def partial_cli(repo, args, **kwargs):
        assert args[:2] == ["stash", "push"]
        try:
            partial_stash(repo, operations._now_signature(), "WIP", include_untracked=True)
        except pygit2.GitError:
            return subprocess.CompletedProcess(args, 1, "", detail)

    monkeypatch.setattr(pygit2.Repository, "stash", partial_stash)
    if method == "stash_push_staged":
        monkeypatch.setattr(operations, "_run_git_in_workdir", partial_cli)
    revision = vm._worktree_refresh_revision
    try:
        result = getattr(vm, method)("hello.txt" if method == "stash_single_file" else "WIP")
        assert result is (None if method == "stash_single_file" else False)
        assert len(errors) == 1
        assert "Папка занята другим процессом" in errors[0]
        assert "сохранён, но очистка" in errors[0]
        assert detail not in errors[0]
        assert any(detail in line and "ERROR" in line for line in logs)
        assert len(vm.branch_panel_view_model().stash_list()) == 1
        assert {f.path for f in vm.commit_panel_view_model().file_changes()} == {"hello.txt"}
        assert not vm.command_processor().can_undo
        assert vm._worktree_refresh_revision > revision
        assert (root / "hello.txt").read_text() == "changed\n"
    finally:
        vm.stop_worktree_refresh()
        vm.set_auto_fetch_enabled(False)
