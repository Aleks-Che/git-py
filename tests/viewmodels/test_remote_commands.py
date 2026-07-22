"""Tests for the remote-related :class:`GitCommand` subclasses.

Covers :class:`PushCommand`, :class:`PullCommand`, :class:`FetchCommand`,
:class:`AddRemoteCommand`, and :class:`RemoveRemoteCommand`. Network
operations are exercised against a local bare ``origin`` repo (no
real network), so the test suite is fully hermetic.

The contracts being tested (R1.6 / R1.7 — see
``docs/updates/update1/VERIFICATION.md`` §5.3):

* :class:`PushCommand` / :class:`FetchCommand` carry ``is_noop = True``
  and are therefore NOT pushed onto the undo stack by
  :class:`CommandProcessor` (the action-history panel still records
  them). A client-side undo of a push/fetch is either impossible or
  meaningless, so we surface that explicitly via
  ``proc.can_undo == False`` rather than letting the toolbar silently
  "succeed" by doing nothing.
* :class:`PullCommand` undo captures the pre-pull HEAD SHA and rewinds
  via ``reset --hard``; on an up-to-date pull the undo is a no-op. A
  pull that raises :class:`MergeConflictError` STAYS on the undo
  stack so ``undo()`` can abort the in-progress merge.
* :class:`AddRemoteCommand` undo removes the freshly-added remote; it
  never destroys a remote that pre-existed.
* :class:`RemoveRemoteCommand` undo re-adds the remote with the captured
  URL.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtWidgets import QApplication
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
    QApplication.instance() or QApplication([])


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


def test_push_command_via_processor_records_but_does_not_push_to_undo(
    origin_and_clone,
) -> None:
    """Push is a no-op for undo and is NOT pushed onto the undo stack.

    New contract (R1.7 — ``PushCommand`` / ``FetchCommand`` carry
    ``is_noop = True`` so :class:`CommandProcessor` does not put
    them on the undo stack).  The action-history panel still
    records the push via the separate history mechanism, but the
    toolbar ``Undo`` button must not "succeed" by doing nothing on
    a push.  Pin that contract here so a regression that removes
    ``is_noop`` from :class:`PushCommand` (and re-enables the
    silently-succeeding undo) fails this test.

    The previous version of this test
    (``test_push_command_via_processor_can_be_redone``) asserted
    that ``proc.can_undo`` was True after a push and that
    ``proc.redo()`` re-ran the execute.  Both assumptions are now
    wrong: the command is excluded from the undo stack entirely
    (so neither ``can_undo`` nor ``can_redo`` reflects a pushed
    push), and ``redo()`` would not re-issue a command that was
    never on either stack.
    """
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
    # Push is excluded from the undo stack (no point in silently
    # succeeding an undo against a command that cannot be undone).
    assert not proc.can_undo
    assert not proc.can_redo
    # A subsequent redo of an empty stack is a no-op — the test
    # would catch a regression that silently mutated the stack
    # during execute.
    proc.redo()
    assert not proc.can_undo
    assert not proc.can_redo


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


def test_pull_command_conflict_stays_in_undo_stack(tmp_git_repo: Path) -> None:
    """A pull that conflicts stays on the undo stack; ``undo()`` aborts.

    New contract (R1.6 / R1.7 — the processor keeps the failing
    :class:`PullCommand` on the undo stack so ``undo()`` can abort
    the partial merge). The previous contract discarded the
    command on failure; this test pins the new behaviour so a
    regression that drops the command from history breaks here
    rather than at the conflict-resolution UI.
    """
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
    # New contract: the failed pull command is kept on the undo
    # stack so ``undo()`` can abort the in-progress merge.
    assert proc.can_undo
    proc.undo()
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
    """Fetch carries ``is_noop = True`` and never enters the undo stack.

    Fetch only mutates ``refs/remotes/<name>/*`` and there is no
    useful client-side rollback for that, so the command is
    marked ``is_noop`` (R1.7) — :class:`CommandProcessor` does
    not put it on the undo stack. ``undo()`` is therefore a no-op
    here.  The previous version of this test asserted
    ``proc.can_undo`` after the fetch, which contradicted the
    updated contract; this version pins the new contract.
    """
    _ensure_app()
    _origin, clone = origin_and_clone
    branch = next(b.name for b in clone.branches if b.is_head)
    proc = CommandProcessor()
    proc.execute(FetchCommand(clone, "origin"))
    # Fetch is excluded from the undo stack.
    assert not proc.can_undo
    # ``undo()`` against an empty stack is a no-op (must not raise).
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
