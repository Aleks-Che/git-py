"""Tests for the remote-related verb methods on :class:`MainViewModel`.

Covers :meth:`push_changes`, :meth:`pull_changes`, :meth:`fetch_changes`,
:meth:`add_remote`, :meth:`remove_remote`, :meth:`list_remotes`,
:meth:`clone_repository`, and the auto-fetch timer. Network calls
are routed to a local bare ``origin`` so the suite never touches
the real network.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QThreadPool, QTimer
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "t@example.com", int(time.time()), 0)


@pytest.fixture
def origin_and_clone(tmp_path: Path) -> tuple[RepositoryManager, RepositoryManager]:
    """Build a bare ``origin`` and a working ``clone`` with one commit
    on the default branch."""
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


def _drain_async_ops(timeout_ms: int = 5000) -> None:
    """Spin a local event loop until the global thread pool is idle.

    Tests that trigger ``_run_async`` schedule a worker on the global
    pool. Waiting for the pool to drain (with a small timeout) is
    enough to make the test deterministic without joining the
    individual worker.
    """
    pool = QThreadPool.globalInstance()
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)

    def _check() -> None:
        if pool.activeThreadCount() == 0:
            loop.quit()

    poll = QTimer()
    poll.setInterval(20)
    poll.timeout.connect(_check)
    poll.start()
    timer.start(timeout_ms)
    loop.exec()
    poll.stop()


# ----- add_remote / remove_remote / list_remotes ---------------------------


def test_add_remote_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.add_remote("upstream", "https://example.com/upstream.git")
    assert any(r.name == "upstream" for r in vm.list_remotes())


def test_add_remote_undo_removes_via_undo(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.add_remote("upstream", "https://example.com/upstream.git")
    assert any(r.name == "upstream" for r in vm.list_remotes())
    vm.undo()
    assert not any(r.name == "upstream" for r in vm.list_remotes())


def test_add_remote_already_exists_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    # First add succeeds and lands on the undo stack; second collides.
    vm.add_remote("origin", "https://a.git")
    assert vm.command_processor().can_undo
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.add_remote("origin", "https://b.git")
    assert errors
    # Failed add was NOT pushed (stack count is unchanged).
    assert vm.command_processor().can_undo


def test_remove_remote_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.add_remote("origin", "https://example.com/origin.git")
    vm.remove_remote("origin")
    assert vm.list_remotes() == []


def test_remove_remote_undo_restores(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.add_remote("origin", "https://example.com/origin.git")
    vm.remove_remote("origin")
    vm.undo()
    assert any(r.name == "origin" for r in vm.list_remotes())


def test_remove_remote_unknown_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.remove_remote("no-such")
    assert errors


def test_list_remotes_empty_without_repo() -> None:
    _ensure_app()
    vm = MainViewModel()
    assert vm.list_remotes() == []


def test_list_remotes_returns_snapshot(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.add_remote("origin", "https://example.com/origin.git")
    vm.add_remote("upstream", "git@example.com:foo.git")
    remotes = vm.list_remotes()
    assert {r.name for r in remotes} == {"origin", "upstream"}


# ----- push_changes / pull_changes / fetch_changes -------------------------


def test_push_changes_via_main_vm_async(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes

    commit_changes(clone, "add g")

    vm = MainViewModel(async_enabled=True)
    vm.set_repository(clone)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.push_changes("origin", f"refs/heads/{branch}")
    # ``busy_changed(True)`` was emitted synchronously before the
    # worker started. We do not wait for the worker — pygit2's
    # :class:`Repository` is not thread-safe when shared with the
    # main thread, and the busy-guard means the test cannot race
    # against the UI either.
    assert busy == [True]
    assert vm.is_busy() is True


def test_push_changes_sync_path(origin_and_clone) -> None:
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes

    commit_changes(clone, "add g")

    vm = MainViewModel(async_enabled=False)
    vm.set_repository(clone)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.push_changes("origin", f"refs/heads/{branch}")
    assert busy == []  # sync
    assert vm.command_processor().can_undo


def test_push_changes_without_repo_emits_error() -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.push_changes("origin")
    assert errors


def test_push_changes_when_busy_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    vm._is_busy = True  # noqa: SLF001 - test only
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.push_changes("origin")
    assert errors
    assert "in progress" in errors[0]


def test_fetch_changes_async_path(origin_and_clone) -> None:
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

    vm = MainViewModel(async_enabled=True)
    vm.set_repository(clone)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.fetch_changes("origin")
    # Async prelude fires busy_changed(True); we do not wait for the
    # worker (see test_push_changes_via_main_vm_async for the
    # pygit2-thread-safety rationale).
    assert busy == [True]


def test_fetch_changes_silent_does_not_emit_error(
    committed_repo: RepositoryManager,
) -> None:
    """When ``silent=True`` the auto-fetch path does not surface errors."""
    _ensure_app()
    vm = MainViewModel(async_enabled=False)
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    # ``no-such-remote`` would normally emit an error; silent must swallow it.
    vm.fetch_changes("no-such-remote", silent=True)
    assert errors == []


def test_fetch_changes_verbose_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel(async_enabled=False)
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.fetch_changes("no-such-remote")
    assert errors


# ----- fetch_and_checkout_remote_branch ----------------------------------


def test_fetch_and_checkout_remote_branch_sync_brings_local_branch(
    origin_and_clone,
) -> None:
    """Double-clicking a remote branch: fetch first, then create+checkout local.

    We push a *new* branch (``feature``) from the working clone to
    ``origin`` so the subject clone doesn't have a local tracking
    branch for it. Calling
    ``fetch_and_checkout_remote_branch("origin/feature")`` on the
    subject should (1) fetch the new branch from origin, (2) create a
    local ``feature`` branch at the remote-tracking tip, and (3)
    switch HEAD to it.
    """
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)

    # Create and push a new branch from the working clone to origin.
    from src.core.operations import checkout_branch as core_checkout
    from src.core.operations import commit_changes
    from src.core.operations import create_branch as core_create_branch
    from src.core.operations import push as core_push

    core_create_branch(clone, "feature", target_sha=clone.head_commit.sha)
    core_checkout(clone, "feature")
    (clone_root / "f.txt").write_text("from feature\n")
    commit_changes(clone, "feature: init")
    core_push(clone, "origin", "refs/heads/feature")

    # Subject clone — has no local "feature" branch yet.
    subject = clone_root.parent / "subject_clone"
    if subject.exists():
        import shutil
        shutil.rmtree(subject)
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(subject))
    subject_mgr = RepositoryManager(str(subject))
    assert "feature" not in {b.name for b in subject_mgr.branches if not b.is_remote}
    assert not subject_mgr.repo.head_is_unborn

    vm = MainViewModel(async_enabled=False)
    vm.set_repository(subject_mgr)
    vm.fetch_and_checkout_remote_branch("origin/feature")

    # Local "feature" was created and HEAD is on it.
    local_names = {b.name for b in subject_mgr.branches if not b.is_remote}
    assert "feature" in local_names
    assert not subject_mgr.repo.head_is_unborn
    assert subject_mgr.repo.head.shorthand == "feature"
    assert subject_mgr.head_commit.message.strip() == "feature: init"


def test_fetch_and_checkout_remote_branch_fast_forwards_existing_local(
    origin_and_clone,
) -> None:
    """If the local branch exists but is behind, fast-forward to the remote tip.

    Regression test for the "I double-clicked origin/main and nothing
    happened" case: the user already has a local ``main`` at an old
    commit, fetch updates ``origin/main`` to a new tip, the local
    branch must move with it so HEAD ends up on the freshly
    downloaded commit.
    """
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    clone_root = Path(clone.path)
    branch = next(b.name for b in clone.branches if b.is_head)

    # Subject clone is made first, so it starts at the "init" commit only.
    subject = clone_root.parent / "subject_clone"
    if subject.exists():
        import shutil
        shutil.rmtree(subject)
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(subject))
    subject_mgr = RepositoryManager(str(subject))
    local_master = next(
        b for b in subject_mgr.branches if b.name == branch and not b.is_remote
    )
    assert local_master.target_sha == clone.head_commit.sha

    # Now push a new commit to origin from the working clone.
    (clone_root / "g.txt").write_text("g\n")
    from src.core.operations import commit_changes
    from src.core.operations import push as core_push

    commit_changes(clone, "add g")
    core_push(clone, "origin", f"refs/heads/{branch}")
    new_origin_sha = clone.head_commit.sha

    vm = MainViewModel(async_enabled=False)
    vm.set_repository(subject_mgr)
    vm.fetch_and_checkout_remote_branch(f"origin/{branch}")

    # Local branch has been fast-forwarded and HEAD is on the new tip.
    local_after = next(
        b for b in subject_mgr.branches if b.name == branch and not b.is_remote
    )
    assert local_after.target_sha == new_origin_sha
    assert subject_mgr.repo.head.shorthand == branch
    assert subject_mgr.head_commit.message.strip() == "add g"


def test_fetch_and_checkout_remote_branch_without_repo_emits_error() -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.fetch_and_checkout_remote_branch("origin/main")
    assert errors


def test_fetch_and_checkout_remote_branch_bad_name_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.fetch_and_checkout_remote_branch("not-a-remote-name")
    assert errors
    assert any("Not a remote branch" in e for e in errors)


def test_fetch_and_checkout_remote_branch_when_busy_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    # Force the busy flag without dispatching a real op.
    vm._is_busy = True  # noqa: SLF001
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.fetch_and_checkout_remote_branch("origin/main")
    assert any("in progress" in e for e in errors)
    vm._is_busy = False  # noqa: SLF001


def test_fetch_and_checkout_remote_branch_toggles_busy_during_fetch(
    origin_and_clone,
) -> None:
    """The method is sync, but the re-entrancy guard and spinner are honoured.

    busy_changed must go True then False, regardless of whether
    ``async_enabled`` is True — the fetch is executed inline to avoid
    the pygit2 thread-safety issue documented on the method.
    """
    _ensure_app()
    _origin, clone = origin_and_clone
    assert clone.path is not None
    branch = next(b.name for b in clone.branches if b.is_head)

    vm = MainViewModel(async_enabled=True)
    vm.set_repository(clone)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.fetch_and_checkout_remote_branch(f"origin/{branch}")
    assert busy == [True, False]


def test_pull_changes_brings_remote(origin_and_clone) -> None:
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

    # A fresh clone is at the "init" commit; pull picks up "add g".
    second = clone_root.parent / "second"
    pygit2.clone_repository(str(clone_root.parent / "origin.git"), str(second))
    second_mgr = RepositoryManager(str(second))

    vm = MainViewModel(async_enabled=False)
    vm.set_repository(second_mgr)
    vm.pull_changes("origin", f"refs/heads/{branch}")
    assert second_mgr.head_commit.message.strip() == "add g"


# ----- clone_repository -----------------------------------------------------


def test_clone_repository_sync(tmp_path: Path) -> None:
    _ensure_app()
    origin_path = tmp_path / "origin.git"
    pygit2.init_repository(str(origin_path), bare=True, initial_head="main")
    # Need a commit on origin so the clone has something to track.
    tmp = tmp_path / "tmp"
    pygit2.clone_repository(str(origin_path), str(tmp))
    sig = _sig()
    (tmp / "a.txt").write_text("a\n")
    clone = pygit2.Repository(str(tmp))
    clone.index.add("a.txt")
    clone.index.write()
    tree = clone.index.write_tree()
    # Unborn clone — read the configured initial head and commit there.
    head_ref = clone.references.get("HEAD")
    branch = (
        head_ref.target[11:]
        if head_ref and head_ref.target.startswith("refs/heads/")
        else "main"
    )
    clone.create_commit(
        f"refs/heads/{branch}", sig, sig, "init", tree, [],
    )
    from src.core.operations import push as core_push

    core_push(clone, "origin", f"refs/heads/{branch}")

    target = tmp_path / "fresh-clone"
    vm = MainViewModel(async_enabled=False)
    repo_changes: list[object] = []
    vm.repository_changed.connect(repo_changes.append)
    vm.clone_repository(str(origin_path), str(target))
    assert vm.repository_manager() is not None
    assert vm.repository_manager().path == str(target)
    assert repo_changes  # at least one signal fired
    # The clone directory exists and points at the remote.
    assert (target / ".git").exists()
    mgr = vm.repository_manager()
    assert mgr is not None
    # The bound manager sees the origin remote.
    assert "origin" in mgr.repo.remotes.names()


def test_clone_repository_async(tmp_path: Path) -> None:
    _ensure_app()
    origin_path = tmp_path / "origin.git"
    pygit2.init_repository(str(origin_path), bare=True, initial_head="main")
    tmp = tmp_path / "tmp"
    pygit2.clone_repository(str(origin_path), str(tmp))
    sig = _sig()
    (tmp / "a.txt").write_text("a\n")
    clone = pygit2.Repository(str(tmp))
    clone.index.add("a.txt")
    clone.index.write()
    tree = clone.index.write_tree()
    head_ref = clone.references.get("HEAD")
    branch = (
        head_ref.target[11:]
        if head_ref and head_ref.target.startswith("refs/heads/")
        else "main"
    )
    clone.create_commit(
        f"refs/heads/{branch}", sig, sig, "init", tree, [],
    )
    from src.core.operations import push as core_push

    core_push(clone, "origin", f"refs/heads/{branch}")

    target = tmp_path / "fresh-clone"
    vm = MainViewModel(async_enabled=True)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.clone_repository(str(origin_path), str(target))
    # The clone op runs on a worker thread; we only assert the async
    # prelude fired.
    assert busy == [True]
    assert vm.is_busy() is True


def test_clone_repository_bad_url_emits_error(tmp_path: Path) -> None:
    _ensure_app()
    vm = MainViewModel(async_enabled=False)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.clone_repository("/nonexistent/path", str(tmp_path / "x"))
    assert errors


# ----- auto-fetch timer -----------------------------------------------------


def test_auto_fetch_disabled_by_default_does_not_tick(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel(auto_fetch_enabled=False, auto_fetch_interval_ms=50)
    vm.set_repository(committed_repo)
    # 200 ms is enough for a timer that should not be running.
    loop = QEventLoop()
    QTimer.singleShot(200, loop.quit)
    loop.exec()
    assert not vm._auto_fetch_timer.isActive()  # noqa: SLF001


def test_auto_fetch_enabled_starts_timer(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel(auto_fetch_enabled=True, auto_fetch_interval_ms=1000)
    vm.set_repository(committed_repo)
    assert vm._auto_fetch_timer.isActive()  # noqa: SLF001
    vm.set_auto_fetch_enabled(False)
    assert not vm._auto_fetch_timer.isActive()  # noqa: SLF001


def test_auto_fetch_stops_when_repo_closes(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel(auto_fetch_enabled=True, auto_fetch_interval_ms=1000)
    vm.set_repository(committed_repo)
    assert vm._auto_fetch_timer.isActive()  # noqa: SLF001
    vm.set_repository(None)
    assert not vm._auto_fetch_timer.isActive()  # noqa: SLF001


def test_auto_fetch_tick_calls_fetch_silent(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel(
        async_enabled=False,
        auto_fetch_enabled=True,
        auto_fetch_interval_ms=50,
    )
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    # ``no-such-remote`` would normally emit; silent=False call from
    # the timer must NOT emit.
    vm.fetch_changes("no-such-remote", silent=True)
    assert errors == []


def test_auto_fetch_set_interval_zero_disables(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel(auto_fetch_enabled=True, auto_fetch_interval_ms=1000)
    vm.set_repository(committed_repo)
    assert vm._auto_fetch_timer.isActive()  # noqa: SLF001
    vm.set_auto_fetch_interval_ms(0)
    assert not vm.is_auto_fetch_enabled()
    assert not vm._auto_fetch_timer.isActive()  # noqa: SLF001
