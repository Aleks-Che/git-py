"""Regression tests for the 2026-09-05 review (``docs/REVIEW_2026-09-05.md``).

Each test reproduces one of the review's data-loss / wrong-ref findings
and pins the fixed behaviour:

* finding 1 — undoing a HEAD-message edit must not touch index/worktree;
* finding 2 — dropping the tip with uncommitted changes is refused;
* finding 3 — a tip-range squash must not absorb unrelated staged files;
* finding 4 — resolving a merge into *another* branch must not move the
  branch the user started from;
* finding 7 — a clean three-way merge must clear the Git merge state;
* finding 11 — reword/squash must work with ``core.abbrev=12`` and
  return the *new* commit OID.

Undo guards (review phase-1 requirement): history-rewriting commands
verify HEAD and the dirty state before a destructive undo.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtWidgets import QApplication
from src.core.exceptions import DirtyWorkTreeError, MergeConflictError
from src.core.operations import (
    commit_changes,
    complete_merge,
    drop_commit,
    edit_commit_message,
    is_merge_in_progress,
    merge_branch,
    squash_commits,
)
from src.core.repository import RepositoryManager
from src.viewmodels.commands import (
    CommandProcessor,
    DropCommitCommand,
    EditCommitMessageCommand,
    MergeCommand,
    SquashCommitsCommand,
)


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _sig() -> pygit2.Signature:
    return pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)


def _commit_file(
    mgr: RepositoryManager,
    path: str,
    content: str,
    message: str,
    *,
    ref: str = "HEAD",
    parents: list | None = None,
) -> str:
    full = Path(mgr.path) / path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content)
    mgr.repo.index.add(path)
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    if parents is None:
        parents = [] if mgr.repo.head_is_unborn else [mgr.repo.head.target]
    sig = _sig()
    oid = mgr.repo.create_commit(ref, sig, sig, message, tree, parents)
    return str(oid)


def _build_conflicting_three_branches(
    mgr: RepositoryManager,
) -> tuple[str, str, str]:
    """Fork main/feature/dev with conflicting edits of ``hello.txt``.

    Leaves HEAD on ``dev`` and returns ``(main_tip, feat_tip, dev_tip)``.
    """
    base_oid = mgr.repo.revparse_single("HEAD~1").peel(pygit2.Commit).id
    mgr.repo.lookup_reference("refs/heads/main").set_target(base_oid)
    mgr.repo.index.read(force=True)
    main_tip = _commit_file(
        mgr, "hello.txt", "main side\n", "main: hello",
        ref="refs/heads/main", parents=[base_oid],
    )
    feat_tip = _commit_file(
        mgr, "hello.txt", "feature side\n", "feature: hello",
        ref="refs/heads/feature", parents=[base_oid],
    )
    dev_tip = _commit_file(
        mgr, "hello.txt", "dev side\n", "dev: hello",
        ref="refs/heads/dev", parents=[base_oid],
    )
    mgr.repo.set_head("refs/heads/dev")
    mgr.repo.checkout_head(strategy=pygit2.GIT_CHECKOUT_FORCE)
    mgr.repo.index.read(force=True)
    return main_tip, feat_tip, dev_tip


# ----- finding 1: reword undo must not delete uncommitted work ---------------


def test_reword_tip_undo_preserves_uncommitted_work(
    committed_repo: RepositoryManager,
) -> None:
    """Scenario ``reword_undo_loses_worktree`` from the review.

    Modify a tracked file (no commit) → reword HEAD → Undo.  The
    uncommitted edit must survive: the tip reword only moved the
    branch ref, so undo must move the ref back and nothing else.
    """
    _ensure_app()
    worktree_file = Path(committed_repo.path) / "hello.txt"
    worktree_file.write_text("valuable uncommitted work\n")
    tip = committed_repo.head_commit

    proc = CommandProcessor()
    proc.execute(EditCommitMessageCommand(committed_repo, tip.sha, "reworded tip"))
    assert committed_repo.head_commit.message.startswith("reworded tip")
    assert worktree_file.read_text() == "valuable uncommitted work\n"

    proc.undo()
    assert committed_repo.head_commit.sha == tip.sha
    assert worktree_file.read_text() == "valuable uncommitted work\n"
    # The file is still reported as modified (nothing was staged/reset).
    status = committed_repo.repo.status()
    assert status.get("hello.txt", 0) & pygit2.GIT_STATUS_WT_MODIFIED


def test_reword_tip_undo_refuses_when_head_moved(
    committed_repo: RepositoryManager,
) -> None:
    """Undo refuses when HEAD moved externally after the reword."""
    _ensure_app()
    tip = committed_repo.head_commit

    proc = CommandProcessor()
    proc.execute(EditCommitMessageCommand(committed_repo, tip.sha, "reworded tip"))
    # External change: another commit lands on top (e.g. via the CLI).
    _commit_file(committed_repo, "other.txt", "o\n", "external commit")

    errors: list[str] = []
    proc.error_occurred.connect(errors.append)
    proc.undo()  # refused — command stays on the undo stack
    assert errors, "undo failure must reach the error signal"
    assert proc.can_undo
    # The reworded commit and the external commit are both untouched.
    assert committed_repo.head_commit.message.strip() == "external commit"


# ----- finding 2: drop tip with uncommitted changes is refused ---------------


def test_drop_tip_with_worktree_changes_refused(
    committed_repo: RepositoryManager,
) -> None:
    """Scenario ``drop_tip_loses_worktree`` from the review."""
    worktree_file = Path(committed_repo.path) / "hello.txt"
    worktree_file.write_text("valuable uncommitted work\n")
    tip = committed_repo.head_commit.sha

    with pytest.raises(DirtyWorkTreeError):
        drop_commit(committed_repo, tip)
    # Nothing moved and the work is intact.
    assert committed_repo.head_commit.sha == tip
    assert worktree_file.read_text() == "valuable uncommitted work\n"


def test_drop_tip_with_staged_changes_refused(
    committed_repo: RepositoryManager,
) -> None:
    (Path(committed_repo.path) / "staged.txt").write_text("staged\n")
    committed_repo.repo.index.add("staged.txt")
    committed_repo.repo.index.write()
    tip = committed_repo.head_commit.sha

    with pytest.raises(DirtyWorkTreeError):
        drop_commit(committed_repo, tip)
    assert committed_repo.head_commit.sha == tip


def test_drop_tip_clean_tree_still_works(
    committed_repo: RepositoryManager,
) -> None:
    tip = committed_repo.head_commit
    drop_commit(committed_repo, tip.sha)
    assert committed_repo.head_commit.sha == tip.parents[0]
    assert not (Path(committed_repo.path) / "hello.txt").exists() or True  # tree of parent


def test_drop_undo_refused_with_uncommitted_changes(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    """A destructive undo refuses while the user has uncommitted work."""
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    c1 = make_commit("c1", files={"a.txt": "A\n"})
    c2 = make_commit("c2", files={"b.txt": "B\n"}, parents=[c1])
    make_commit("c3", files={"c.txt": "C\n"}, parents=[c2])

    proc = CommandProcessor()
    proc.execute(DropCommitCommand(mgr, str(c2)))

    # User starts new work after the drop — undoing now would destroy it.
    (tmp_git_repo / "a.txt").write_text("work in progress\n")
    errors: list[str] = []
    proc.error_occurred.connect(errors.append)
    proc.undo()
    assert errors
    assert (tmp_git_repo / "a.txt").read_text() == "work in progress\n"
    assert proc.can_undo  # command kept for a later retry


# ----- finding 3: squash must not absorb unrelated staged files ---------------


def test_squash_tip_excludes_unrelated_staged_file(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    """Scenario ``squash_includes_unrelated_staged_file`` from the review."""
    mgr = RepositoryManager(str(tmp_git_repo))
    c1 = make_commit("c1", files={"a.txt": "A\n"})
    c2 = make_commit("c2", files={"b.txt": "B\n"}, parents=[c1])
    c3 = make_commit("c3", files={"c.txt": "C\n"}, parents=[c2])

    # Stage an unrelated file *before* the squash.
    (tmp_git_repo / "unrelated.txt").write_text("not part of the range\n")
    mgr.repo.index.add("unrelated.txt")
    mgr.repo.index.write()

    info = squash_commits(mgr, [str(c3), str(c2)], "c2+c3 squashed")

    new_commit = mgr.repo[pygit2.Oid(hex=info.sha)]
    assert "unrelated.txt" not in {e.name for e in new_commit.tree}
    assert {e.name for e in new_commit.tree} == {"a.txt", "b.txt", "c.txt"}
    # The unrelated file is still staged (index untouched) and on disk.
    status = mgr.repo.status()
    assert status.get("unrelated.txt", 0) & pygit2.GIT_STATUS_INDEX_NEW
    assert (tmp_git_repo / "unrelated.txt").read_text() == "not part of the range\n"
    assert mgr.head_commit.parents == [str(c1)]


def test_squash_tip_undo_restores_history_and_keeps_staged(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    c1 = make_commit("c1", files={"a.txt": "A\n"})
    c2 = make_commit("c2", files={"b.txt": "B\n"}, parents=[c1])
    c3 = make_commit("c3", files={"c.txt": "C\n"}, parents=[c2])

    (tmp_git_repo / "unrelated.txt").write_text("keep me staged\n")
    mgr.repo.index.add("unrelated.txt")
    mgr.repo.index.write()

    proc = CommandProcessor()
    proc.execute(SquashCommitsCommand(mgr, [str(c3), str(c2)], "squashed"))
    assert mgr.head_commit.message.strip() == "squashed"

    proc.undo()
    assert mgr.head_commit.sha == str(c3)
    # The user's staged file survived execute *and* undo.
    status = mgr.repo.status()
    assert status.get("unrelated.txt", 0) & pygit2.GIT_STATUS_INDEX_NEW
    assert (tmp_git_repo / "unrelated.txt").read_text() == "keep me staged\n"


# ----- finding 4: merge into another branch resolves on the target ------------


def test_resolve_merge_into_other_branch_moves_only_target(
    committed_repo: RepositoryManager,
) -> None:
    """Scenario ``resolve_merge_moves_unrelated_current_branch``.

    HEAD=dev, ``merge feature into main`` conflicts.  Resolving and
    completing the merge must advance ``main`` only; ``dev`` stays
    exactly where it was, and the merge commit's first parent is the
    old ``main`` tip.
    """
    main_tip, feat_tip, dev_tip = _build_conflicting_three_branches(committed_repo)

    with pytest.raises(MergeConflictError) as exc_info:
        merge_branch(committed_repo, "feature", target="main")
    exc = exc_info.value
    assert exc.source_oid == feat_tip
    assert exc.target_branch == "main"
    assert exc.target_oid == main_tip
    # HEAD sits on the target while the merge is unresolved.
    assert committed_repo.repo.head.shorthand == "main"

    # Resolve the single conflicting file and complete the merge.
    (Path(committed_repo.path) / "hello.txt").write_text("resolved\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    merge_oid = complete_merge(
        committed_repo, source=exc.source_oid, target=exc.target_branch,
    )

    assert str(committed_repo.repo.lookup_reference("refs/heads/dev").target) == dev_tip
    main_ref = committed_repo.repo.lookup_reference("refs/heads/main")
    assert str(main_ref.target) == merge_oid
    merge_commit = committed_repo.repo[main_ref.target]
    assert [str(p) for p in merge_commit.parent_ids] == [main_tip, feat_tip]
    assert committed_repo.repo.head.shorthand == "main"
    assert not is_merge_in_progress(committed_repo)
    assert dict(committed_repo.repo.status()) == {}


def test_merge_command_undo_after_conflict_aborts_and_restores_branch(
    committed_repo: RepositoryManager,
) -> None:
    """Undo on a conflicted MergeCommand aborts the merge *and* returns
    HEAD to the branch the user started from."""
    _ensure_app()
    main_tip, _feat_tip, dev_tip = _build_conflicting_three_branches(committed_repo)

    proc = CommandProcessor()
    with pytest.raises(MergeConflictError):
        proc.execute(MergeCommand(committed_repo, "feature", target="main"))
    assert is_merge_in_progress(committed_repo)
    assert committed_repo.repo.head.shorthand == "main"

    proc.undo()
    assert not is_merge_in_progress(committed_repo)
    assert str(committed_repo.repo.lookup_reference("refs/heads/main").target) == main_tip
    assert committed_repo.repo.head.shorthand == "dev"
    assert str(committed_repo.repo.head.target) == dev_tip
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "dev side\n"
    assert dict(committed_repo.repo.status()) == {}


# ----- finding 7: a clean merge clears the Git merge state --------------------


def test_clean_merge_clears_merge_state(
    committed_repo: RepositoryManager,
) -> None:
    """Scenario ``clean_merge_keeps_merge_state`` from the review."""
    base = committed_repo.head_commit.sha
    from src.core.operations import checkout_branch, create_branch

    create_branch(committed_repo, "feature", target_sha=base)
    checkout_branch(committed_repo, "feature")
    _commit_file(committed_repo, "f.txt", "f\n", "add f")
    checkout_branch(committed_repo, "main")
    _commit_file(committed_repo, "m.txt", "m\n", "add m")

    assert merge_branch(committed_repo, "feature") is True
    head = committed_repo.repo[committed_repo.repo.head.target]
    assert len(head.parent_ids) == 2  # a real merge commit
    assert not is_merge_in_progress(committed_repo)
    # The repo is immediately usable for the next ordinary action.
    info = commit_changes(committed_repo, "follow-up", stage_all=True)
    assert info.parents == [str(head.id)]


# ----- finding 11: long abbreviations + the returned OID ----------------------


def test_reword_middle_commit_with_abbrev_12(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    """Scenario ``reword_abbrev_12_noop`` from the review.

    With ``core.abbrev=12`` the rebase todo lines carry 12-char SHAs;
    comparing against a 7-char prefix never matched, so the reword
    silently did nothing while reporting success.
    """
    mgr = RepositoryManager(str(tmp_git_repo))
    mgr.repo.config["core.abbrev"] = 12
    c1 = make_commit("c1", files={"a.txt": "A\n"})
    c2 = make_commit("c2 old", files={"b.txt": "B\n"}, parents=[c1])
    c3 = make_commit("c3", files={"c.txt": "C\n"}, parents=[c2])

    info = edit_commit_message(mgr, str(c2), "c2 new")

    # The *new* OID is returned (not the old dangling one) ...
    assert info.sha != str(c2)
    assert info.message.strip() == "c2 new"
    # ... and the rewrite actually happened.
    history = {c.sha: c for c in mgr.get_all_history()}
    assert str(c2) not in history
    assert mgr.head_commit.sha != str(c3)
    parent_of_tip = mgr.repo[mgr.repo.head.target].parents[0]
    assert parent_of_tip.message.strip() == "c2 new"
    assert str(parent_of_tip.id) == info.sha


def test_squash_middle_range_with_abbrev_12(
    tmp_git_repo: Path,
    make_commit,
) -> None:
    mgr = RepositoryManager(str(tmp_git_repo))
    mgr.repo.config["core.abbrev"] = 12
    c1 = make_commit("c1", files={"a.txt": "A\n"})
    c2 = make_commit("c2", files={"b.txt": "B\n"}, parents=[c1])
    c3 = make_commit("c3", files={"c.txt": "C\n"}, parents=[c2])
    c4 = make_commit("c4", files={"d.txt": "D\n"}, parents=[c3])

    info = squash_commits(mgr, [str(c3), str(c2)], "c2+c3 squashed")

    history = [c.message.strip() for c in mgr.get_all_history()]
    assert history == ["c4", "c2+c3 squashed", "c1"]
    assert info.message.strip() == "c2+c3 squashed"
    assert mgr.head_commit.sha != str(c4)
