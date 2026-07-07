"""Tests for the branch-mutating verb methods on :class:`MainViewModel`.

The contract is the same as for :meth:`MainViewModel.commit_changes`:
* the call goes through :class:`CommandProcessor` (so Undo / Redo work),
* on success every downstream view (graph, commit panel, branch panel)
  is refreshed,
* on failure the error is surfaced through ``error_occurred`` and the
  command is *not* pushed onto the undo stack.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


# ----- checkout_branch -----------------------------------------------


def test_checkout_branch_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    from src.core.operations import create_branch

    create_branch(
        committed_repo,
        "feature",
        target_sha=committed_repo.head_commit.parents[0],
    )

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.checkout_branch("feature")
    assert committed_repo.repo.head.shorthand == "feature"


def test_checkout_branch_updates_all_views(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import create_branch

    create_branch(
        committed_repo,
        "feature",
        target_sha=committed_repo.head_commit.parents[0],
    )

    vm = MainViewModel()
    vm.set_repository(committed_repo)

    # The branch panel VM starts on main; after the checkout it must
    # see ``feature`` as the current branch and the graph VM must have
    # run a refresh.
    bp = vm.branch_panel_view_model()
    gv = vm.graph_view_model()
    bp_refreshes: list[None] = []
    gv_refreshes: list[list] = []
    bp.references_changed.connect(lambda: bp_refreshes.append(None))
    gv.graph_updated.connect(lambda rows: gv_refreshes.append(rows))

    vm.checkout_branch("feature")
    assert bp.current_branch_name() == "feature"
    assert gv_refreshes  # at least one graph_updated fired
    assert bp_refreshes  # and the branch panel did refresh too


def test_checkout_branch_dirty_worktree_emits_error(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import create_branch

    create_branch(
        committed_repo,
        "feature",
        target_sha=committed_repo.head_commit.parents[0],
    )
    # Make the worktree dirty so SAFE checkout will refuse.
    (Path(committed_repo.path) / "hello.txt").write_text("dirty\n")

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.checkout_branch("feature")
    assert errors
    assert committed_repo.repo.head.shorthand == "main"  # unchanged
    assert not vm.command_processor().can_undo  # failed cmd not on stack


def test_checkout_branch_without_repo_emits_error() -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.checkout_branch("main")
    assert "No repository" in errors[0]


# ----- create_branch --------------------------------------------------


def test_create_branch_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.create_branch("topic", target_sha=committed_repo.head_commit.sha)
    assert any(b.name == "topic" for b in committed_repo.branches)
    assert vm.command_processor().can_undo


def test_create_branch_undo_removes_it(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.create_branch("topic", target_sha=committed_repo.head_commit.sha)
    assert any(b.name == "topic" for b in committed_repo.branches)
    vm.undo()
    assert not any(b.name == "topic" for b in committed_repo.branches)


def test_create_branch_tracks_recently_created(
    committed_repo: RepositoryManager,
) -> None:
    """``create_branch`` records the new name in ``recently_created_branches``.

    The set is consumed by the graph widget's chip-priority logic to
    keep the just-created branch visually secondary when several
    branches share a commit (the user-requested "source branch
    first" UX). Pinning the bookkeeping here means a future
    refactor that drops the notification would surface here.
    """
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.recently_created_branches() == set()
    vm.create_branch("topic", target_sha=committed_repo.head_commit.sha)
    assert "topic" in vm.recently_created_branches()


def test_create_branch_emits_recently_created_changed(
    committed_repo: RepositoryManager,
) -> None:
    """The :attr:`recently_created_changed` signal fires on every new branch.

    Carries the *full* (snapshot) set rather than the added name
    because the widgets that consume it refresh their entire
    priority map on every emission; deltas would force them to
    play catch-up. The signal is also re-emitted via
    :attr:`GraphViewModel.recently_created_changed` so the graph
    widget does not need a direct reference to the
    :class:`MainViewModel`.
    """
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    seen: list[set[str]] = []
    vm.recently_created_changed.connect(lambda names: seen.append(set(names)))
    gv = vm.graph_view_model()
    seen_via_gv: list[set[str]] = []
    gv.recently_created_changed.connect(lambda names: seen_via_gv.append(set(names)))
    vm.create_branch("alpha", target_sha=committed_repo.head_commit.sha)
    QCoreApplication.processEvents()
    vm.create_branch("beta", target_sha=committed_repo.head_commit.sha)
    QCoreApplication.processEvents()

    assert set(seen[-1]) == {"alpha", "beta"}
    assert set(seen_via_gv[-1]) == {"alpha", "beta"}


def test_set_repository_clears_recently_created(
    committed_repo: RepositoryManager,
) -> None:
    """Switching repositories forgets the previous run's tracking set.

    Across different repos the priority ordering must not carry
    stale state - a branch tagged "newly created" in repo A might
    be ancient in repo B. The signal is also re-emitted with an
    empty set so widgets drop any cached names.
    """
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.create_branch("topic", target_sha=committed_repo.head_commit.sha)
    assert "topic" in vm.recently_created_branches()

    seen: list[set[str]] = []
    vm.recently_created_changed.connect(lambda names: seen.append(set(names)))
    vm.set_repository(None)
    assert vm.recently_created_branches() == set()
    assert seen[-1] == set()


# ----- delete_branch --------------------------------------------------


def test_delete_branch_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    from src.core.operations import create_branch

    create_branch(committed_repo, "topic", target_sha=committed_repo.head_commit.sha)

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.delete_branch("topic")
    assert not any(b.name == "topic" for b in committed_repo.branches)


def test_delete_current_branch_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.delete_branch("main")
    assert errors
    assert any("current branch" in e for e in errors)


# ----- rename_branch --------------------------------------------------


def test_rename_branch_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    from src.core.operations import create_branch

    create_branch(committed_repo, "old", target_sha=committed_repo.head_commit.sha)

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.rename_branch("old", "new")
    names = {b.name for b in committed_repo.branches}
    assert "old" not in names
    assert "new" in names


def test_rename_branch_undo_restores_old(
    committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from src.core.operations import create_branch

    create_branch(committed_repo, "old", target_sha=committed_repo.head_commit.sha)

    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.rename_branch("old", "new")
    vm.undo()
    names = {b.name for b in committed_repo.branches}
    assert "old" in names
    assert "new" not in names


# ----- error forwarding ----------------------------------------------


def test_error_occurred_from_branch_panel_is_forwarded(
    committed_repo: RepositoryManager,
) -> None:
    """Sanity check: child VM errors still surface on the central signal."""
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.branch_panel_view_model().error_occurred.emit("simulated")
    assert "simulated" in errors[0]
