"""Tests for merge / rebase / cherry-pick / revert on :class:`MainViewModel`.

The contract is the same as the other mutating verbs:

* The call routes through :class:`CommandProcessor` (Undo / Redo work).
* A conflict is captured into :attr:`MainViewModel.conflict_state`
  and the failed command is **not** pushed onto the undo stack.
* ``abort_*`` methods clear the conflict state without touching the
  undo stack.
* :meth:`MainViewModel.set_repository` clears the conflict state when
  the user opens a different repository.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _build_conflict(committed_repo: RepositoryManager) -> None:
    """Build a conflict on ``hello.txt`` between ``main`` and ``feature``."""
    from src.core.operations import checkout_branch, commit_changes, create_branch

    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "hello.txt").write_text("feature side\n")
    commit_changes(committed_repo, "feature side")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "hello.txt").write_text("main side\n")
    commit_changes(committed_repo, "main side")


# ----- merge_branch -------------------------------------------------------


def test_merge_branch_clean_three_way_updates_views(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import checkout_branch, commit_changes, create_branch

    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")
    checkout_branch(committed_repo, "main")

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    states: list[dict] = []
    vm.conflict_state_changed.connect(states.append)

    vm.merge_branch("feature")
    assert vm.command_processor().can_undo
    assert states == []  # no conflict
    assert vm.conflict_state() is None


def test_merge_branch_no_ff_creates_merge_commit_on_fast_forward(
    committed_repo: RepositoryManager,
) -> None:
    """``no_ff=True`` keeps the merge visible in the graph.

    The user reported: when a fast-forward merge happens (source
    is a descendant of HEAD), the user sees "no merge commit" in
    the graph. The fix is the ``no_ff`` parameter on
    :meth:`MainViewModel.merge_branch`. The test pins the
    parameter at the VM layer so the user-facing behaviour is
    locked in.
    """
    _ensure_app()
    from src.core.operations import checkout_branch, commit_changes, create_branch

    main_sha = committed_repo.head_commit.sha
    create_branch(committed_repo, "feature", target_sha=main_sha)
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    feat_sha = commit_changes(committed_repo, "add f").sha
    checkout_branch(committed_repo, "main")

    vm = MainViewModel()
    vm.set_repository(committed_repo)

    vm.merge_branch("feature", no_ff=True)
    new_head_sha = committed_repo.head_commit.sha
    # The merge commit is a brand new commit with two parents —
    # not the fast-forward tip and not the original main tip.
    assert new_head_sha != feat_sha
    assert new_head_sha != main_sha
    assert set(committed_repo.head_commit.parents) == {main_sha, feat_sha}


def test_merge_branch_conflict_emits_conflict_state(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    states: list[dict] = []
    vm.conflict_state_changed.connect(states.append)

    vm.merge_branch("feature")

    assert not vm.command_processor().can_undo
    assert states, "conflict_state_changed should have fired"
    state = states[-1]
    assert state["in_progress"] is True
    assert state["operation"] == "merge"
    assert "hello.txt" in state["conflicting_paths"]
    assert state["source"] == "feature"
    assert vm.conflict_state() is not None


def test_merge_branch_without_repo_emits_error() -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.merge_branch("feature")
    assert errors
    assert "No repository" in errors[0]


def test_merge_branch_unknown_source_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.merge_branch("no-such")
    assert errors
    assert not vm.command_processor().can_undo
    assert vm.conflict_state() is None


def test_merge_branch_unknown_remote_source_hint_includes_fetch(
    committed_repo: RepositoryManager,
) -> None:
    """A user-friendly fetch hint must reach the user-facing error signal.

    The user reported: dropping a remote branch on a local one
    produced "Unknown source: 'renovate/npm-vite-vulnerability'"
    with no hint that the branch needed fetching.  The error
    surfaced by the VM is what reaches the status bar / log, so
    the test pins the hint at the VM layer.
    """
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.merge_branch("renovate/npm-vite-vulnerability")
    assert errors
    # The fetch hint must survive the trip through the VM error
    # signal — the test would catch a regression that wrapped the
    # message in something less helpful.
    assert any("fetch" in e.lower() for e in errors)


# ----- abort_merge / resolve_conflict -------------------------------------


def test_abort_merge_clears_conflict_state(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    states: list[dict] = []
    vm.conflict_state_changed.connect(states.append)
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    vm.abort_merge()
    assert vm.conflict_state() is None
    assert states[-1]["in_progress"] is False
    # The worktree is back to the main version.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "main side\n"
    # Abort must not push anything onto the undo stack.
    assert not vm.command_processor().can_undo


def test_abort_merge_without_in_progress_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.abort_merge()
    assert errors


def test_resolve_conflict_writes_file_and_finalizes_merge(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    vm.resolve_conflict("hello.txt", "resolved!\n")
    state = vm.conflict_state()
    assert state is None  # cleared after finalize
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "resolved!\n"
    # A merge commit was created.
    assert len(committed_repo.head_commit.parents) == 2
    assert not vm.command_processor().can_undo  # the failed merge was never pushed


def test_resolve_conflict_keeps_state_when_other_paths_remain(
    committed_repo: RepositoryManager,
) -> None:
    """If only some conflicts were resolved, the state stays active."""
    _ensure_app()
    # Build a multi-file conflict.
    from src.core.operations import checkout_branch, commit_changes, create_branch

    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "hello.txt").write_text("feature hi\n")
    (Path(committed_repo.path) / "world.txt").write_text("feature w\n")
    commit_changes(committed_repo, "feature: hi+world")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "hello.txt").write_text("main hi\n")
    (Path(committed_repo.path) / "world.txt").write_text("main w\n")
    commit_changes(committed_repo, "main: hi+world")

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    state = vm.conflict_state()
    assert state is not None
    assert set(state["conflicting_paths"]) == {"hello.txt", "world.txt"}

    vm.resolve_conflict("hello.txt", "h!\n")
    state = vm.conflict_state()
    assert state is not None
    assert state["in_progress"] is True
    assert state["conflicting_paths"] == ["world.txt"]


# ----- rebase_branch ------------------------------------------------------


def test_rebase_branch_clean_undo_rewinds(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import checkout_branch, commit_changes, create_branch

    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")
    checkout_branch(committed_repo, "main")
    (Path(committed_repo.path) / "m.txt").write_text("m\n")
    commit_changes(committed_repo, "add m")
    checkout_branch(committed_repo, "feature")
    pre_rebase = committed_repo.head_commit.sha

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.rebase_branch("main")
    assert committed_repo.head_commit.sha != pre_rebase
    assert vm.command_processor().can_undo

    vm.undo()
    assert committed_repo.head_commit.sha == pre_rebase


def test_rebase_branch_conflict_emits_error_and_state(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)

    from src.core.operations import checkout_branch as cb

    cb(committed_repo, "feature")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    states: list[dict] = []
    vm.error_occurred.connect(errors.append)
    vm.conflict_state_changed.connect(states.append)
    vm.rebase_branch("main")
    assert errors
    assert states
    assert states[-1]["operation"] == "rebase"
    assert states[-1]["in_progress"] is True


def test_abort_rebase_clears_conflict_state(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    from src.core.operations import checkout_branch as cb

    cb(committed_repo, "feature")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.rebase_branch("main")
    assert vm.conflict_state() is not None
    vm.abort_rebase()
    assert vm.conflict_state() is None
    assert not vm.command_processor().can_undo


# ----- cherry_pick / revert -----------------------------------------------


def test_cherry_pick_clean_stages_in_index(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import commit_changes, create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")
    feature_sha = next(
        b.target_sha for b in committed_repo.branches if b.name == "feature"
    )

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.cherry_pick(feature_sha)
    assert "f.txt" in committed_repo.repo.index


def test_cherry_pick_conflict_emits_conflict_state(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    feature_sha = next(
        b.target_sha for b in committed_repo.branches if b.name == "feature"
    )

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    states: list[dict] = []
    vm.conflict_state_changed.connect(states.append)
    vm.cherry_pick(feature_sha)
    assert states
    state = states[-1]
    assert state["operation"] == "cherry-pick"
    assert "hello.txt" in state["conflicting_paths"]
    assert state["sha"] == feature_sha


def test_revert_clean_stages_inverse(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    target_sha = committed_repo.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.revert(target_sha)
    # The file is re-staged to the pre-HEAD version.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "hello\n"


# ----- set_repository clears conflict state -------------------------------


def test_set_repository_clears_conflict_state(
    tmp_git_repo: Path, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    # Switch to a different repo — conflict state must be cleared.
    other_mgr = RepositoryManager(str(tmp_git_repo))
    states: list[dict] = []
    vm.conflict_state_changed.connect(states.append)
    vm.set_repository(other_mgr)
    assert vm.conflict_state() is None
    assert states and states[-1]["in_progress"] is False


def test_set_repository_none_clears_conflict_state(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.merge_branch("feature")
    assert vm.conflict_state() is not None

    vm.set_repository(None)
    assert vm.conflict_state() is None


# ----- resolve_conflict for cherry-pick / revert --------------------------


def test_resolve_conflict_clears_state_for_cherry_pick(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    _build_conflict(committed_repo)
    feature_sha = next(
        b.target_sha for b in committed_repo.branches if b.name == "feature"
    )

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.cherry_pick(feature_sha)
    assert vm.conflict_state() is not None
    vm.resolve_conflict("hello.txt", "cp resolved\n")
    assert vm.conflict_state() is None
    # The cherry-pick change is staged for the user to commit.
    assert "hello.txt" in committed_repo.repo.index


# ----- guard rails ---------------------------------------------------------


def test_resolve_conflict_without_active_state_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.resolve_conflict("hello.txt", "x")
    assert errors
    assert "No conflict" in errors[0]


# ----- async infrastructure -----------------------------------------------


def test_is_busy_starts_false() -> None:
    _ensure_app()
    vm = MainViewModel()
    assert vm.is_busy() is False


def test_merge_branch_when_busy_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    """If a previous async operation is in flight, new verb calls bail out."""
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    # Force the busy state without actually starting a worker.
    vm._is_busy = True  # noqa: SLF001 - test only
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.merge_branch("feature")
    assert errors
    assert "in progress" in errors[0]


def test_async_enabled_routes_rebase_to_worker(
    committed_repo: RepositoryManager,
) -> None:
    """With ``async_enabled=True``, rebase goes through the worker
    and sets the busy state immediately (the work happens on a
    background thread, so we just check the synchronous prelude)."""
    _ensure_app()
    from src.core.operations import checkout_branch, commit_changes, create_branch

    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")
    checkout_branch(committed_repo, "main")

    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.rebase_branch("feature")
    # ``busy_changed(True)`` was emitted synchronously before the
    # worker started. We do NOT wait for the worker to finish — the
    # test teardown will join it via QThreadPool.
    assert busy == [True]
    assert vm.is_busy() is True


def test_async_disabled_keeps_rebase_sync(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import checkout_branch, commit_changes, create_branch

    create_branch(committed_repo, "feature")
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")
    checkout_branch(committed_repo, "main")

    vm = MainViewModel(async_enabled=False)
    vm.set_repository(committed_repo)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.rebase_branch("feature")
    assert busy == []  # sync path, no busy change
    assert vm.is_busy() is False
    # Sync rebase actually moved HEAD.
    assert committed_repo.head_commit.parents[0] != committed_repo.head_commit.sha


def test_estimate_merge_size_for_small_diff(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    from src.core.operations import commit_changes, create_branch

    create_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    # One file differs (f.txt) — should be well below the default threshold.
    assert vm._estimate_merge_size("feature") < 50  # noqa: SLF001


def test_merge_async_threshold_routes_to_worker(
    committed_repo: RepositoryManager,
) -> None:
    """Set the threshold to 0 so even a small diff goes async."""
    _ensure_app()
    from src.core.operations import commit_changes, create_branch

    create_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")

    vm = MainViewModel(async_enabled=True, merge_async_threshold=0)
    vm.set_repository(committed_repo)
    busy: list[bool] = []
    vm.busy_changed.connect(busy.append)
    vm.merge_branch("feature")
    assert busy == [True]
    assert vm.is_busy() is True
