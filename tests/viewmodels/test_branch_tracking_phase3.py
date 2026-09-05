"""Branch tracking state and async redo routing (review 2026-09-05, phase 3).

* ``RepositoryManager.branches`` exposes upstream name/tip and
  ahead/behind counters for local branches;
* the left-panel suppression keeps a same-name remote branch when its
  tip differs from the local tip (a diverged upstream stays visible);
* ``LeftPanel`` renders ↑/↓ counters next to the branch name;
* redoing a network/long command routes to the sequential mutation
  queue instead of blocking the UI thread (finding 10).
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtWidgets import QApplication
from src.core.models import BranchInfo
from src.core.repository import RepositoryManager
from src.ui.widgets.left_panel import LeftPanel
from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel
from src.viewmodels.commands import FetchCommand, PullCommand
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)


def _commit(mgr: RepositoryManager, path: str, content: str, message: str) -> str:
    full = Path(mgr.path) / path
    full.write_text(content)
    mgr.repo.index.add(path)
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    parents = [] if mgr.repo.head_is_unborn else [mgr.repo.head.target]
    sig = _sig()
    return str(mgr.repo.create_commit("HEAD", sig, sig, message, tree, parents))


def _repo_with_upstream(
    tmp_path: Path,
) -> tuple[RepositoryManager, str, str]:
    """Repo with local ``main`` tracking ``origin/main``, diverged 1/1.

    Returns ``(mgr, local_tip, remote_tip)``; the remote-tracking ref is
    one commit ahead of the local tip, which itself has one unpushed
    commit on top of the shared base.
    """
    repo_path = tmp_path / "repo"
    pygit2.init_repository(str(repo_path), initial_head="main")
    mgr = RepositoryManager(str(repo_path))
    base = _commit(mgr, "a.txt", "base\n", "base")
    # Upstream resolution needs a configured remote named ``origin``.
    mgr.repo.remotes.create("origin", str(repo_path))
    # The remote-tracking ref starts at base; configure tracking.
    mgr.repo.references.create("refs/remotes/origin/main", pygit2.Oid(hex=base))
    local_branch = mgr.repo.lookup_branch("main")
    local_branch.upstream = mgr.repo.lookup_branch(
        "origin/main", pygit2.enums.BranchType.REMOTE,
    )
    # Local goes one ahead of base; the "remote" also moves one ahead
    # of base — a *sibling* commit, so the branches diverge 1/1.
    local_tip = _commit(mgr, "b.txt", "local work\n", "local ahead")
    (Path(mgr.path) / "c.txt").write_text("remote work\n")
    mgr.repo.index.add("c.txt")
    mgr.repo.index.write()
    sig = _sig()
    remote_tip = str(
        mgr.repo.create_commit(
            "refs/remotes/origin/main", sig, sig, "remote ahead",
            mgr.repo.index.write_tree(), [pygit2.Oid(hex=base)],
        ),
    )
    # Drop the staged c.txt so the worktree matches the local tip.
    mgr.repo.reset(pygit2.Oid(hex=local_tip), pygit2.GIT_RESET_MIXED)
    return mgr, local_tip, remote_tip


def test_branches_expose_ahead_behind(tmp_path: Path) -> None:
    mgr, local_tip, remote_tip = _repo_with_upstream(tmp_path)
    main = next(b for b in mgr.branches if b.name == "main" and not b.is_remote)
    assert main.upstream == "origin/main"
    assert main.upstream_sha == remote_tip
    assert main.target_sha == local_tip
    assert main.ahead == 1
    assert main.behind == 1


def test_branches_without_upstream_have_no_counts(
    committed_repo: RepositoryManager,
) -> None:
    main = next(b for b in committed_repo.branches if not b.is_remote)
    assert main.upstream is None
    assert main.ahead is None
    assert main.behind is None


def test_suppression_keeps_diverged_same_name_remote() -> None:
    """origin/main at a *different* tip must survive the suppression."""
    local = [BranchInfo(name="main", target_sha="1" * 40)]
    remote = [BranchInfo(name="origin/main", is_remote=True, target_sha="2" * 40)]
    kept = BranchPanelViewModel._suppress_same_name_remotes(local, remote)
    assert [b.name for b in kept] == ["origin/main"]


def test_suppression_drops_identical_same_name_remote() -> None:
    same = "1" * 40
    local = [BranchInfo(name="main", target_sha=same)]
    remote = [
        BranchInfo(name="origin/main", is_remote=True, target_sha=same),
        BranchInfo(name="origin/HEAD", is_remote=True, target_sha=same),
    ]
    kept = BranchPanelViewModel._suppress_same_name_remotes(local, remote)
    assert kept == []


def test_left_panel_shows_ahead_behind_markers(qtbot, tmp_path: Path) -> None:
    _ensure_app()
    mgr, _local_tip, _remote_tip = _repo_with_upstream(tmp_path)
    vm = BranchPanelViewModel()
    panel = LeftPanel(vm, MainViewModel())
    qtbot.addWidget(panel)
    # The panel rebuilds on ``references_changed`` — bind after creation.
    vm.set_repository(mgr)

    local_group = panel._group_local
    texts = [local_group.child(i).text(0) for i in range(local_group.childCount())]
    main_text = next(t for t in texts if t.startswith("main"))
    assert "↑1" in main_text
    assert "↓1" in main_text


def test_redo_of_pull_routes_to_mutation_queue(
    committed_repo: RepositoryManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redo of a network command must not run on the UI thread."""
    _ensure_app()
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    # Seed a redo entry the cheap way: push a command and undo it.
    proc = vm.command_processor()
    cmd = FetchCommand(committed_repo, "origin", None)
    proc._undo_stack.append(cmd)
    proc.undo()
    assert proc.can_redo

    routed: list[object] = []
    monkeypatch.setattr(vm, "_run_async_redo", routed.append)
    vm.redo()
    assert len(routed) == 1
    assert isinstance(routed[0], FetchCommand)


def test_redo_of_local_command_stays_sync(
    committed_repo: RepositoryManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cheap local command keeps the synchronous redo path."""
    _ensure_app()
    from src.viewmodels.commands import CreateBranchCommand

    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    proc = vm.command_processor()
    cmd = CreateBranchCommand(committed_repo, "topic")
    proc.execute(cmd)
    proc.undo()
    assert proc.can_redo

    def _boom(_: object) -> None:  # pragma: no cover - must not run
        raise AssertionError("local redo must stay synchronous")

    monkeypatch.setattr(vm, "_run_async_redo", _boom)
    vm.redo()
    assert committed_repo.repo.lookup_branch("topic") is not None


def test_async_redo_candidates_cover_network_commands() -> None:
    _ensure_app()
    assert MainViewModel._is_async_redo_candidate(
        PullCommand(RepositoryManager(), "origin"),
    )
    assert MainViewModel._is_async_redo_candidate(
        FetchCommand(RepositoryManager(), "origin"),
    )
    assert not MainViewModel._is_async_redo_candidate(object())
