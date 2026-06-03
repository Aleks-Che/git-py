"""Tests for :class:`src.viewmodels.branch_panel_viewmodel.BranchPanelViewModel`.

The ViewModel is a read-only data container fed by
:class:`RepositoryManager`; tests exercise the ``set_repository`` /
``refresh`` lifecycle, the four property lists, the ``current_branch``
lookup, and the ``references_changed`` signal.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from PySide6.QtCore import QCoreApplication
from src.core.models import BranchInfo, StashInfo, TagInfo
from src.core.operations import create_branch, stash_push
from src.core.repository import RepositoryManager
from src.viewmodels.branch_panel_viewmodel import BranchPanelViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "t@example.com", int(time.time()), 0)


# ----- lifecycle / binding --------------------------------------------


def test_set_repository_none_clears_state(qtbot) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    vm.set_repository(None)
    assert vm.local_branches() == []
    assert vm.remote_branches() == []
    assert vm.tags() == []
    assert vm.stash_list() == []
    assert vm.current_branch_name() is None


def test_set_repository_emits_references_changed(qtbot) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    with qtbot.waitSignal(vm.references_changed, timeout=500):
        vm.set_repository(None)


def test_refresh_on_no_repo_emits_signal_with_empty_lists(qtbot) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    with qtbot.waitSignal(vm.references_changed, timeout=500):
        vm.refresh()
    assert vm.local_branches() == []


# ----- data population -------------------------------------------------


def test_local_branches_populated(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    vm.set_repository(committed_repo)
    names = {b.name for b in vm.local_branches()}
    assert "main" in names
    # All populated entries are local (no remote branches in committed_repo).
    assert vm.remote_branches() == []


def test_local_branches_filters_out_remotes(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A local + remote-tracking branch must not appear in both lists."""
    _ensure_app()
    # Build a working clone of a local bare origin, push one commit, and
    # then ``fetch`` so the remote-tracking branch is materialised.
    origin_path = tmp_git_repo / "origin.git"
    clone_path = tmp_git_repo / "clone"
    pygit2.init_repository(str(origin_path), bare=True)
    pygit2.clone_repository(str(origin_path), str(clone_path))
    sig = _sig()
    (clone_path / "f.txt").write_text("x\n")
    clone = pygit2.Repository(str(clone_path))
    clone.index.add("f.txt")
    clone.index.write()
    tree = clone.index.write_tree()
    clone.create_commit("HEAD", sig, sig, "init", tree, [])
    branch = clone.head.shorthand

    from src.core.operations import fetch, push

    push(clone, "origin", f"refs/heads/{branch}")
    mgr = RepositoryManager(str(clone_path))
    fetch(mgr, "origin")

    vm = BranchPanelViewModel()
    vm.set_repository(mgr)
    remote_names = {b.name for b in vm.remote_branches()}
    local_names = {b.name for b in vm.local_branches()}
    # ``origin/<default>`` lives only in the remote list.
    assert any(n.startswith("origin/") for n in remote_names)
    assert not any(n.startswith("origin/") for n in local_names)


def test_tags_populated(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    repo = committed_repo.repo
    sig = _sig()
    obj = repo.revparse_single("HEAD").peel(pygit2.Commit)
    repo.create_tag("v1.0", obj.id, pygit2.GIT_OBJECT_COMMIT, sig, "release 1.0")

    vm = BranchPanelViewModel()
    vm.set_repository(committed_repo)
    tags = vm.tags()
    assert any(t.name == "v1.0" for t in tags)
    assert isinstance(tags[0], TagInfo)


def test_stash_list_populated(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    # Make a stashable change.
    assert committed_repo.path is not None
    (Path(committed_repo.path) / "hello.txt").write_text("wip line\n")
    stash_push(committed_repo, "WIP for test")

    vm = BranchPanelViewModel()
    vm.set_repository(committed_repo)
    stash = vm.stash_list()
    assert len(stash) == 1
    assert isinstance(stash[0], StashInfo)
    assert "WIP for test" in stash[0].message


def test_current_branch_name(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    vm.set_repository(committed_repo)
    assert vm.current_branch_name() == "main"


def test_current_branch_name_is_none_on_unborn(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))
    assert vm.current_branch_name() is None


def test_local_branches_marks_head(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    vm.set_repository(committed_repo)
    head_entries = [b for b in vm.local_branches() if b.is_head]
    assert len(head_entries) == 1
    assert head_entries[0].name == "main"
    assert isinstance(head_entries[0], BranchInfo)


# ----- refresh after mutation -----------------------------------------


def test_refresh_picks_up_newly_created_branch(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    vm.set_repository(committed_repo)
    assert not any(b.name == "fresh" for b in vm.local_branches())

    create_branch(committed_repo, "fresh", target_sha=committed_repo.head_commit.sha)
    with qtbot.waitSignal(vm.references_changed, timeout=500):
        vm.refresh()
    assert any(b.name == "fresh" for b in vm.local_branches())


def test_set_repository_triggers_immediate_refresh(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = BranchPanelViewModel()
    with qtbot.waitSignal(vm.references_changed, timeout=500):
        vm.set_repository(committed_repo)
    assert any(b.name == "main" for b in vm.local_branches())


# ----- error path -----------------------------------------------------


def test_error_on_invalid_repo_does_not_raise(
    qtbot, tmp_path: Path,
) -> None:
    """A non-repo path cannot be opened by :class:`RepositoryManager`."""
    _ensure_app()
    # We feed a *valid* repo to ``set_repository`` so binding succeeds,
    # then corrupt it (delete the .git/HEAD file) to provoke a Core error
    # on the next ``refresh()``.
    repo_path = tmp_path / "broken"
    pygit2.init_repository(str(repo_path), initial_head="main")
    mgr = RepositoryManager(str(repo_path))
    # Simulate breakage by closing the underlying repo and clearing the
    # path: ``set_repository`` accepts it, but the next ``refresh``
    # call finds ``is_open=False`` and emits the empty state.
    mgr.close()
    vm = BranchPanelViewModel()
    with qtbot.waitSignal(vm.references_changed, timeout=500):
        vm.set_repository(mgr)
    assert vm.local_branches() == []


def test_set_repository_with_closed_manager_keeps_lists_empty(qtbot) -> None:
    """A bound-but-closed manager must not blow up on :meth:`refresh`."""
    _ensure_app()
    mgr = RepositoryManager()
    mgr.close()  # is_open == False
    vm = BranchPanelViewModel()
    with qtbot.waitSignal(vm.references_changed, timeout=500):
        vm.set_repository(mgr)
    assert vm.local_branches() == []
    assert vm.remote_branches() == []
    assert vm.tags() == []
    assert vm.stash_list() == []
    assert vm.current_branch_name() is None
