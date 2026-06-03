"""Tests for the remote-related :class:`GitCommand` subclasses.

Covers :class:`PushCommand`, :class:`PullCommand`, :class:`FetchCommand`,
:class:`AddRemoteCommand`, and :class:`RemoveRemoteCommand`. Network
operations are exercised against a local bare ``origin`` repo (no
real network), so the test suite is fully hermetic.

The contracts being tested:

* ``PushCommand`` / ``FetchCommand`` undo is a no-op (push and fetch
  only modify server-side / remote-tracking refs — nothing local to
  rewind). The command is still pushed onto the undo stack so the
  history panel shows the action.
* ``PullCommand`` undo captures the pre-pull HEAD SHA and rewinds via
  ``reset --hard``. On an up-to-date pull the undo is a no-op.
* ``AddRemoteCommand`` undo removes the freshly-added remote; it
  never destroys a remote that pre-existed.
* ``RemoveRemoteCommand`` undo re-adds the remote with the captured
  URL.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QCoreApplication
from src.core.exceptions import GitError, InvalidRefError
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    AddRemoteCommand,
    CommandProcessor,
    FetchCommand,
    PullCommand,
    PushCommand,
    RemoveRemoteCommand,
)


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "t@example.com", int(time.time()), 0)


@pytest.fixture
def origin_and_clone(tmp_path: Path) -> tuple[RepositoryManager, RepositoryManager]:
    """Build a bare ``origin`` and a working ``clone`` with one commit.

    Returns ``(origin_manager, clone_manager)``. The clone's HEAD is
    the only branch (``main``/``master``) with one initial commit
    already pushed to ``origin``.
    """
    origin_path = tmp_path / "origin.git"
    clone_path = tmp_path / "clone"
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
    from src.core.operations import push as core_push

    core_push(clone, "origin", f"refs/heads/{branch}")
    return (
        RepositoryManager(str(origin_path)),
        RepositoryManager(str(clone_path)),
    )


# ----- PushCommand ----------------------------------------------------------


def test_push_command_pushes_ref(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes

    commit_changes(clone, "add g")

    cmd = PushCommand(clone, "origin", f"refs/heads/{branch}")
    cmd.execute()
    assert cmd.name == f"push origin/refs/heads/{branch}"


def test_push_command_undo_is_noop(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes

    commit_changes(clone, "add g")
    head_sha = clone.head_commit.sha

    proc = CommandProcessor()
    proc.execute(PushCommand(clone, "origin", f"refs/heads/{branch}"))
    proc.undo()
    # The local commit is still on the branch; undo is intentionally
    # silent because there is no safe local rewind for a push.
    assert clone.head_commit.sha == head_sha


def test_push_command_unknown_remote_raises(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    with pytest.raises(InvalidRefError):
        PushCommand(committed_repo, "no-such", "refs/heads/main").execute()


def test_push_command_via_processor_can_be_redone(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes

    commit_changes(clone, "add g")
    proc = CommandProcessor()
    proc.execute(PushCommand(clone, "origin", f"refs/heads/{branch}"))
    assert proc.can_undo
    # Redo must re-run execute (it's a fresh push of the same ref).
    proc.redo()
    assert proc.can_undo


# ----- PullCommand ----------------------------------------------------------


def test_pull_command_brings_remote_changes(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    # Push a new commit to origin from this clone.
    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes
    from src.core.operations import push as core_push

    commit_changes(clone, "add g")
    core_push(clone, "origin", f"refs/heads/{branch}")

    # A fresh clone pulls it.
    second = clone_root.parent / "second"
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(second))
    second_mgr = RepositoryManager(str(second))

    cmd = PullCommand(second_mgr, "origin", f"refs/heads/{branch}")
    cmd.execute()
    assert second_mgr.head_commit.message.strip() == "add g"
    assert cmd.name == f"pull origin/refs/heads/{branch}"


def test_pull_command_undo_rewinds(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    # Spin up a second clone from the current origin (only has "init").
    second = clone_root.parent / "second"
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(second))
    second_mgr = RepositoryManager(str(second))
    pre_pull = second_mgr.head_commit.sha

    # Now push a new commit to origin from the first clone.
    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes
    from src.core.operations import push as core_push

    commit_changes(clone, "add g")
    core_push(clone, "origin", f"refs/heads/{branch}")

    cmd = PullCommand(second_mgr, "origin", f"refs/heads/{branch}")
    cmd.execute()
    assert second_mgr.head_commit.sha != pre_pull

    cmd.undo()
    assert second_mgr.head_commit.sha == pre_pull


def test_pull_command_up_to_date_undo_is_noop(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    # No new commits on origin since the clone; pull is a no-op.
    head_sha = clone.head_commit.sha
    cmd = PullCommand(clone, "origin")
    cmd.execute()
    assert clone.head_commit.sha == head_sha
    cmd.undo()  # must not raise
    assert clone.head_commit.sha == head_sha


def test_pull_command_conflict_is_not_pushed(tmp_git_repo: Path) -> None:
    """A pull that conflicts leaves the index in conflict; the command
    is not pushed onto the undo stack (the processor re-raises)."""
    _ensure_app()
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
    from src.core.operations import push as core_push

    core_push(clone, "origin", f"refs/heads/{branch}")

    # Clone a sibling from origin (only has "init" so far).
    second = clone_path.parent / "second"
    pygit2.clone_repository(str(origin_path), str(second))
    second_mgr = RepositoryManager(str(second))

    # Local commit in the second clone — diverges from the first clone
    # but the first clone has not pushed anything new yet.
    (second / "f.txt").write_text("local change\n")
    from src.core.operations import commit_changes

    commit_changes(second_mgr, "local")

    # Now the first clone makes a different change and pushes it.
    (clone_path / "f.txt").write_text("remote change\n")
    commit_changes(clone, "remote")
    core_push(clone, "origin", f"refs/heads/{branch}")

    # Pull from origin in the second clone → conflict on f.txt.
    proc = CommandProcessor()
    cmd = PullCommand(second_mgr, "origin", f"refs/heads/{branch}")
    with pytest.raises(GitError):
        proc.execute(cmd)
    assert not proc.can_undo


# ----- FetchCommand ---------------------------------------------------------


def test_fetch_command_brings_remote_branches(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    # Push a new commit to origin.
    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes
    from src.core.operations import push as core_push

    commit_changes(clone, "add g")
    core_push(clone, "origin", f"refs/heads/{branch}")

    # Fresh clone has no remote-tracking branch yet; fetch materialises it.
    second = clone_root.parent / "second"
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(second))
    second_mgr = RepositoryManager(str(second))
    cmd = FetchCommand(second_mgr, "origin")
    cmd.execute()
    remote_names = {b.name for b in second_mgr.branches if b.is_remote}
    assert f"origin/{branch}" in remote_names
    assert cmd.name == "fetch origin/all"


def test_fetch_command_undo_is_noop(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    branch = next(b.name for b in clone.branches if b.is_head)
    proc = CommandProcessor()
    proc.execute(FetchCommand(clone, "origin"))
    assert proc.can_undo
    # Undo: nothing to do, but it must not raise and must clear undo
    # flag (or rather: leave the command on the stack — undo is
    # defined as a no-op here).
    proc.undo()
    # The remote branch we just fetched is still there.
    assert any(b.name == f"origin/{branch}" for b in clone.branches)


def test_fetch_command_unknown_remote_raises(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    with pytest.raises(InvalidRefError):
        FetchCommand(committed_repo, "no-such").execute()


# ----- AddRemoteCommand -----------------------------------------------------


def test_add_remote_command_creates_remote(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    cmd = AddRemoteCommand(committed_repo, "upstream", "https://example.com/upstream.git")
    cmd.execute()
    assert "upstream" in list(committed_repo.repo.remotes.names())
    assert cmd.name == "add remote upstream"


def test_add_remote_command_undo_removes_remote(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    cmd = AddRemoteCommand(committed_repo, "upstream", "https://example.com/u.git")
    cmd.execute()
    cmd.undo()
    assert "upstream" not in list(committed_repo.repo.remotes.names())


def test_add_remote_command_undo_noop_when_pre_existing(
    committed_repo: RepositoryManager,
) -> None:
    """If the remote pre-existed (e.g. someone else added it), undo
    must not destroy it."""
    _ensure_app()
    from src.core.operations import add_remote

    add_remote(committed_repo, "preexisting", "https://example.com/x.git")
    cmd = AddRemoteCommand(committed_repo, "preexisting", "https://example.com/y.git")
    # Simulate the race: pretend the remote already existed when the
    # command ran (the real execute() would have raised).
    cmd._existed_before = True
    cmd.undo()
    # Still there.
    assert "preexisting" in list(committed_repo.repo.remotes.names())


def test_add_remote_command_already_exists_raises(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    AddRemoteCommand(committed_repo, "origin", "https://a.git").execute()
    with pytest.raises(GitError, match="already exists"):
        AddRemoteCommand(committed_repo, "origin", "https://b.git").execute()


def test_add_remote_command_via_processor_round_trip(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    proc = CommandProcessor()
    proc.execute(AddRemoteCommand(committed_repo, "u", "https://u.git"))
    assert "u" in list(committed_repo.repo.remotes.names())
    proc.undo()
    assert "u" not in list(committed_repo.repo.remotes.names())
    proc.redo()
    assert "u" in list(committed_repo.repo.remotes.names())


# ----- RemoveRemoteCommand --------------------------------------------------


def test_remove_remote_command_deletes_remote(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    from src.core.operations import add_remote

    add_remote(committed_repo, "origin", "https://example.com/origin.git")
    cmd = RemoveRemoteCommand(committed_repo, "origin")
    cmd.execute()
    assert "origin" not in list(committed_repo.repo.remotes.names())
    assert cmd.name == "remove remote origin"


def test_remove_remote_command_undo_re_adds_with_original_url(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import add_remote

    add_remote(committed_repo, "origin", "https://example.com/origin.git")
    cmd = RemoveRemoteCommand(committed_repo, "origin")
    cmd.execute()
    cmd.undo()
    names = list(committed_repo.repo.remotes.names())
    assert "origin" in names
    # The URL was captured before removal — undo re-adds with the
    # original URL.
    assert committed_repo.repo.remotes["origin"].url == "https://example.com/origin.git"


def test_remove_remote_command_unknown_raises(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    with pytest.raises(InvalidRefError):
        RemoveRemoteCommand(committed_repo, "no-such").execute()


def test_remove_remote_command_via_processor_round_trip(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import add_remote

    add_remote(committed_repo, "origin", "https://example.com/origin.git")
    proc = CommandProcessor()
    proc.execute(RemoveRemoteCommand(committed_repo, "origin"))
    assert "origin" not in list(committed_repo.repo.remotes.names())
    proc.undo()
    assert "origin" in list(committed_repo.repo.remotes.names())
    proc.redo()
    assert "origin" not in list(committed_repo.repo.remotes.names())
