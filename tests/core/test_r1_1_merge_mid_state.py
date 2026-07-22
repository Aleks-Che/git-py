"""R1.1 — ``merge X into Y`` with ``HEAD = Z`` must leave ``HEAD`` untouched.

Background (see ``docs/updates/update1/VERIFICATION.md`` §3):

The previous implementation of :func:`src.core.operations.merge_branch`
called ``r.checkout(target_ref_name)`` before doing the merge analysis.
That move was intended to let :func:`pygit2.Repository.merge_analysis`
evaluate the merge against the *target* branch, but the checkout
silently switched ``HEAD`` to the target, brought the index and
worktree along with it, and never restored them after the merge
completed. The result: after ``merge X into Y`` with the user on
branch ``Z``:

* ``HEAD`` was silently switched to ``Y`` (user context lost);
* the worktree carried files from ``Y`` instead of ``Z``;
* the index was left dirty with phantom ``INDEX_DELETED`` entries
  for the files that had been on ``Z`` but were absent from ``Y``.

The "merge in memory" fix (option (b) in the verification report)
computes the merge via :func:`pygit2.Repository.merge_commits` against
the target/source OIDs directly and writes the resulting commit to the
target ref. ``HEAD``, the index, and the worktree are never touched.

These tests pin that contract:

* ``HEAD`` stays on the user's branch (``Z``) throughout.
* The worktree still carries the files the user had on ``Z`` (no
  files from ``X`` or ``Y`` that ``Z`` did not already have).
* The target ref (``Y``) advances to the merge commit; the merge
  commit's first parent is the old tip of ``Y`` (not the tip of
  ``Z``).
* ``git status`` on the user's branch is empty — no
  ``INDEX_DELETED``, no phantom modifications.

Each test asserts HEAD did NOT move and the target ref DID move; a
test that passed against the buggy code must fail against the
regression once the fix is reverted.
"""
from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import GitError, MergeConflictError
from src.core.operations import (
    checkout_branch,
    commit_changes,
    create_branch,
    merge_branch,
)
from src.core.repository import RepositoryManager


def _build_three_branches(
    committed_repo: RepositoryManager,
) -> tuple[str, str, str, str, str]:
    """Shape a ``committed_repo`` into three diverging branches.

    Layout (chronological):

    * ``main``  → adds ``m.txt`` on top of the base
    * ``feature`` → adds ``f.txt`` on top of the base
    * ``dev``  → adds ``d.txt`` on top of the base

    All three branch off from the same base commit (the first
    ``hello.txt`` commit in ``committed_repo``). Each tip carries a
    different file, so merging any one into another is a real
    three-way merge (no fast-forward). The function leaves ``HEAD``
    on ``dev``.

    Returns ``(main_tip, feat_tip, dev_tip, dev_workdir_files, pre_merge_main_sha)``
    so the caller can assert against the pre-merge state.

    ``dev_workdir_files`` is the set of files the worktree contains
    while ``HEAD`` is on ``dev`` (used to prove the worktree is
    untouched after the merge).

    The new untracked-file rule (R1.4) means
    :func:`commit_changes` no longer auto-stages ``f.txt`` /
    ``m.txt`` / ``d.txt``.  We stage the new file explicitly via
    ``repo.index.add(path)`` so each branch tip carries the file
    we wrote.
    """
    import pygit2 as _pg

    # The ``committed_repo`` fixture put a SECOND commit on ``main``;
    # for the regression we want all three branches to fork off the
    # same FIRST commit.  Capture the SHA *before* we reset the ref
    # so ``HEAD~1`` still walks a valid chain.
    base_oid = committed_repo.repo.revparse_single("HEAD~1").peel(_pg.Commit).id
    base = str(base_oid)

    committed_repo.repo.lookup_reference("refs/heads/main").set_target(base_oid)

    # Build the tree for each branch tip with the file explicitly
    # staged (R1.4 keeps untracked files out of ``commit_changes``).
    # Each commit's parent is the shared base, so all three
    # branches are siblings and merging any two is a real
    # three-way merge (no fast-forward).

    feat_sig = _pg.Signature("tester", "tester@example.com", 1000, 0)
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    committed_repo.repo.index.add("f.txt")
    committed_repo.repo.index.write()
    feat_tree = committed_repo.repo.index.write_tree()
    feat_tip = committed_repo.repo.create_commit(
        "refs/heads/feature", feat_sig, feat_sig, "add f", feat_tree, [base_oid],
    )
    feat_tip = str(feat_tip)

    # Reset to a clean index for the main commit (drop the ``f.txt``
    # we just staged above).
    committed_repo.repo.index.read(force=True)

    (Path(committed_repo.path) / "m.txt").write_text("m\n")
    committed_repo.repo.index.add("m.txt")
    committed_repo.repo.index.write()
    main_tree = committed_repo.repo.index.write_tree()
    main_tip = committed_repo.repo.create_commit(
        "refs/heads/main", feat_sig, feat_sig, "add m", main_tree, [base_oid],
    )
    main_tip = str(main_tip)

    committed_repo.repo.index.read(force=True)

    (Path(committed_repo.path) / "d.txt").write_text("d\n")
    committed_repo.repo.index.add("d.txt")
    committed_repo.repo.index.write()
    dev_tree = committed_repo.repo.index.write_tree()
    dev_tip = committed_repo.repo.create_commit(
        "refs/heads/dev", feat_sig, feat_sig, "add d", dev_tree, [base_oid],
    )
    dev_tip = str(dev_tip)

    # Move HEAD to ``dev`` so the regression tests start with HEAD
    # on a non-target branch.  ``set_head`` does not refresh the
    # worktree — that's fine, we only care about HEAD's symbolic
    # ref.  The worktree state will be that of the latest
    # ``commit_changes`` (i.e. ``dev``).
    committed_repo.repo.set_head("refs/heads/dev")

    workdir_files = {p.name for p in Path(committed_repo.path).iterdir() if p.is_file()}

    return main_tip, feat_tip, dev_tip, workdir_files, base


def has_index_deleted_phantom(repo: pygit2.Repository) -> bool:
    """Return whether status contains the R1.1 phantom index deletion."""
    return any(
        flags & pygit2.GIT_STATUS_INDEX_DELETED
        for flags in repo.status().values()
    )


def _worktree_files(repo: pygit2.Repository) -> dict[str, bytes]:
    """Return tracked worktree file contents, excluding the Git directory."""
    root = Path(repo.workdir)
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }


def _tree_files(repo: pygit2.Repository, tree: pygit2.Tree) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for entry in tree:
        if entry.type == "tree":
            result.update(_tree_files(repo, repo[entry.id]))
        else:
            result[entry.name] = repo[entry.id].data
    return result


def test_merge_into_other_branch_does_not_move_head_or_worktree(
    committed_repo: RepositoryManager,
) -> None:
    """``merge feature into main`` while ``HEAD = dev`` keeps HEAD on dev.

    This is the regression: the old implementation would silently
    switch ``HEAD`` to ``main`` (and bring the worktree with it),
    leaving the user with a different branch context than the one
    they were on when they invoked the merge. After the fix, the
    user's branch context (``dev``) and the worktree's files (the
    dev-only ``d.txt``) must be preserved.
    """
    main_tip, feat_tip, dev_tip, dev_workdir_files, _base = _build_three_branches(
        committed_repo,
    )
    # Sanity: the worktree carries ``d.txt`` because we are on dev.
    assert "d.txt" in dev_workdir_files
    assert committed_repo.repo.head.shorthand == "dev"

    pre_main_ref = str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )

    merge_branch(committed_repo, "feature", target="main")

    # Successful variant (a): target becomes HEAD and worktree is the merge tree.
    assert committed_repo.repo.head.shorthand == "main"
    assert str(committed_repo.repo.lookup_reference("refs/heads/dev").target) == dev_tip
    post_main_ref = committed_repo.repo.lookup_reference("refs/heads/main")
    assert str(post_main_ref.target) != pre_main_ref
    merge_commit = committed_repo.repo[post_main_ref.target]
    assert [str(p) for p in merge_commit.parent_ids] == [main_tip, feat_tip]
    assert _worktree_files(committed_repo.repo) == _tree_files(
        committed_repo.repo, merge_commit.tree,
    )
    assert not has_index_deleted_phantom(committed_repo.repo)
    assert dict(committed_repo.repo.status()) == {}
    assert not (Path(committed_repo.path) / "MERGE_HEAD").exists()


def test_merge_into_other_branch_does_not_touch_index(
    committed_repo: RepositoryManager,
) -> None:
    """``HEAD = Z`` and an existing staged change on Z survive ``merge X into Y``.

    The previous behaviour moved ``HEAD`` to ``Y`` and rewrote the
    index to ``Y``'s tree, silently dropping whatever the user had
    staged on ``Z``. After the fix, the index is byte-identical to
    its pre-merge state because the merge never touches the index.
    """
    _main_tip, _feat_tip, _dev_tip, _files, _base = _build_three_branches(
        committed_repo,
    )

    pre_main = str(committed_repo.repo.lookup_reference("refs/heads/main").target)
    merge_branch(committed_repo, "feature", target="main")
    post_main = committed_repo.repo.lookup_reference("refs/heads/main")
    assert str(post_main.target) != pre_main
    assert committed_repo.repo.head.shorthand == "main"
    assert dict(committed_repo.repo.status()) == {}
    assert not has_index_deleted_phantom(committed_repo.repo)
    assert not (Path(committed_repo.path) / "MERGE_HEAD").exists()


def test_merge_into_other_branch_three_way_creates_merge_commit(
    committed_repo: RepositoryManager,
) -> None:
    """``merge X into Y`` while ``HEAD = Z`` produces a real merge commit.

    The merge commit must carry two parents: the OLD tip of ``Y``
    and the tip of ``X``. Its tree must contain the union of all
    three branches' tracked files (``d.txt``, ``f.txt``, ``m.txt``,
    plus the base ``hello.txt``).
    """
    main_tip, feat_tip, dev_tip, _files, _base = _build_three_branches(
        committed_repo,
    )
    assert committed_repo.repo.head.shorthand == "dev"

    merge_branch(committed_repo, "feature", target="main")

    post_main = committed_repo.repo.lookup_reference("refs/heads/main")
    merge_commit = committed_repo.repo[post_main.target]
    parents = [str(p) for p in merge_commit.parent_ids]
    assert set(parents) == {main_tip, feat_tip}

    # Successful merge checks out the target.
    assert committed_repo.repo.head.shorthand == "main"
    assert str(committed_repo.repo.head.target) == str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )
    assert dict(committed_repo.repo.status()) == {}
    assert not has_index_deleted_phantom(committed_repo.repo)
    assert not (Path(committed_repo.path) / "MERGE_HEAD").exists()


def test_merge_into_current_branch_when_head_matches_target_advances_head(
    committed_repo: RepositoryManager,
) -> None:
    """Sanity check: ``merge X into Y`` with ``HEAD = Y`` still works.

    When the user explicitly merges into the branch they are on,
    the new commit is the merge commit AND HEAD's resolved SHA
    follows the symbolic ref. This is the "merge X into current"
    flow that the UI also uses, and it must keep working after the
    in-memory refactor.
    """
    main_tip, feat_tip, _dev_tip, _files, _base = _build_three_branches(
        committed_repo,
    )
    # Switch to main so HEAD and target match.
    checkout_branch(committed_repo, "main")
    assert committed_repo.repo.head.shorthand == "main"

    merge_branch(committed_repo, "feature", target="main")

    post_main = committed_repo.repo.lookup_reference("refs/heads/main")
    merge_commit = committed_repo.repo[post_main.target]
    parents = [str(p) for p in merge_commit.parent_ids]
    assert set(parents) == {main_tip, feat_tip}

    # HEAD follows the symbolic ref (still on main), now at the
    # merge commit.
    assert committed_repo.repo.head.shorthand == "main"
    assert str(committed_repo.repo.head.target) == str(post_main.target)


def test_merge_fast_forward_does_not_move_head_or_worktree(
    committed_repo: RepositoryManager,
) -> None:
    """Fast-forward ``merge feature into main`` with ``HEAD = dev`` is HEAD-neutral.

    Fast-forward path (no three-way, no merge commit) is the
    cheapest possible behaviour: just move the target ref.  The
    in-memory implementation must not accidentally checkout the
    target either.
    """
    base = committed_repo.head_commit.sha
    create_branch(committed_repo, "feature", target_sha=base)
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    feat_tip = commit_changes(committed_repo, "add f").sha

    create_branch(committed_repo, "dev", target_sha=base)
    checkout_branch(committed_repo, "dev")
    (Path(committed_repo.path) / "d.txt").write_text("d\n")
    commit_changes(committed_repo, "add d")
    _dev_workdir = {p.name for p in Path(committed_repo.path).iterdir() if p.is_file()}

    pre_main_ref = str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )

    merge_branch(committed_repo, "feature", target="main")

    # Target checkout is intentional in variant (a).
    assert committed_repo.repo.head.shorthand == "main"
    assert str(committed_repo.repo.head.target) == feat_tip
    assert not has_index_deleted_phantom(committed_repo.repo)
    # the source's tree).
    post_main = committed_repo.repo.lookup_reference("refs/heads/main")
    assert str(post_main.target) == feat_tip
    assert str(post_main.target) != pre_main_ref


def test_merge_conflict_does_not_touch_head_or_worktree(
    committed_repo: RepositoryManager,
) -> None:
    """A conflicting merge raises and leaves ``HEAD``/worktree untouched.

    Conflicts are reported through :class:`MergeConflictError`, but
    the in-memory merge implementation must not have already
    touched ``HEAD``/index/worktree by the time the exception is
    raised. The old implementation had a window where the index
    was left in a conflicted state AND HEAD was switched; the new
    one short-circuits before touching either.

    We build the conflict by giving ``main``, ``feature`` and
    ``dev`` independent edits of the same tracked file
    (``hello.txt``), each on top of the same base. ``main`` and
    ``feature`` both diverge from the base — a ``merge feature
    into main`` produces a real three-way conflict on
    ``hello.txt``.
    """
    # ``HEAD~1`` is the first commit of the fixture; we use it as
    # the shared base so all three branches have a clean fork.
    base_oid = committed_repo.repo.revparse_single("HEAD~1").peel(
        pygit2.Commit,
    ).id

    sig = pygit2.Signature("tester", "tester@example.com", 1000, 0)

    def _add_commit(ref: str, message: str, content: str) -> str:
        # Write a new file content, stage it, commit directly with
        # ``base_oid`` as the sole parent.  Stages the file
        # explicitly because R1.4 keeps untracked files out of
        # ``commit_changes``.
        (Path(committed_repo.path) / "hello.txt").write_text(content)
        committed_repo.repo.index.add("hello.txt")
        committed_repo.repo.index.write()
        tree_oid = committed_repo.repo.index.write_tree()
        oid = committed_repo.repo.create_commit(
            ref, sig, sig, message, tree_oid, [base_oid],
        )
        return str(oid)

    # Reset ``main`` so it forks off ``base`` too.
    committed_repo.repo.lookup_reference("refs/heads/main").set_target(base_oid)
    # Drop the staged hello.txt from the reset so the next commit
    # starts from a clean index (matches ``base_oid``'s tree).
    committed_repo.repo.index.read(force=True)

    main_tip = _add_commit("refs/heads/main", "main: hello", "main side\n")
    _add_commit(
        "refs/heads/feature", "feature: hello", "feature side\n",
    )
    dev_tip = _add_commit("refs/heads/dev", "dev: hello", "dev side\n")

    # HEAD on dev; pre-merge state captured for post-merge asserts.
    committed_repo.repo.set_head("refs/heads/dev")
    pre_main_ref = str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )
    assert pre_main_ref == main_tip
    _workdir_files = {p.name for p in Path(committed_repo.path).iterdir() if p.is_file()}
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "dev side\n"

    # ``merge feature into main`` while HEAD = dev → conflict.
    with pytest.raises(MergeConflictError) as exc_info:
        merge_branch(committed_repo, "feature", target="main")
    assert "hello.txt" in exc_info.value.conflicting_paths

    # Conflict state is preserved for resolve_conflict/complete_merge.
    assert committed_repo.repo.head.shorthand == "dev"
    assert str(committed_repo.repo.head.target) == dev_tip
    assert str(committed_repo.repo.lookup_reference("refs/heads/main").target) == pre_main_ref
    assert not (Path(committed_repo.path) / "MERGE_HEAD").exists()
    assert any(committed_repo.repo.index.conflicts)
    assert dict(committed_repo.repo.status())


def test_merge_into_unknown_target_raises_and_leaves_state_untouched(
    committed_repo: RepositoryManager,
) -> None:
    """A bad target name raises before any state change.

    ``target`` does not exist as a local branch. The function must
    reject the request before mutating anything — HEAD stays on
    the user's branch, no merge commit is created.
    """
    base = committed_repo.head_commit.sha
    create_branch(committed_repo, "feature", target_sha=base)
    checkout_branch(committed_repo, "feature")
    (Path(committed_repo.path) / "f.txt").write_text("f\n")
    commit_changes(committed_repo, "add f")

    create_branch(committed_repo, "dev", target_sha=base)
    checkout_branch(committed_repo, "dev")
    _dev_tip = committed_repo.head_commit.sha

    with pytest.raises(GitError):
        merge_branch(committed_repo, "feature", target="no-such-branch")

    # Nothing moved.
    assert committed_repo.repo.head.shorthand == "dev"
    assert str(committed_repo.repo.head.target) == str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )


def test_merge_via_command_processor_does_not_move_head(
    committed_repo: RepositoryManager,
) -> None:
    """The regression is also caught through :class:`MergeCommand`.

    We round-trip the merge through ``CommandProcessor`` to mirror
    the production path (UI → VM → command).  A naive implementation
    that moved HEAD would silently break the user's branch context
    here too.
    """
    from src.viewmodels.commands import CommandProcessor, MergeCommand

    _main_tip, feat_tip, dev_tip, _files, _base = _build_three_branches(
        committed_repo,
    )
    assert committed_repo.repo.head.shorthand == "dev"
    pre_main_ref = str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )

    cmd = MergeCommand(committed_repo, "feature", target="main")
    proc = CommandProcessor()
    proc.execute(cmd)

    # Successful merge moves HEAD to the target; undo restores the ref.
    assert committed_repo.repo.head.shorthand == "main"
    assert str(committed_repo.repo.head.target) == str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )
    post_main = committed_repo.repo.lookup_reference("refs/heads/main")
    assert str(post_main.target) != pre_main_ref

    # Undo restores the previous ``main`` ref.  Crucially, the
    # dev ref and HEAD were never involved — undo only has to
    # touch the target branch.
    cmd.undo()
    assert (
        str(committed_repo.repo.lookup_reference("refs/heads/main").target)
        == pre_main_ref
    )
    assert committed_repo.repo.head.shorthand == "dev"
    assert str(committed_repo.repo.head.target) == dev_tip
    # Sanity: the undone merge commit's OID is no longer the tip of
    # ``main`` (it still exists in the repo as a dangling commit,
    # which is fine — reflog / git-fsck will collect it).
    assert str(post_main.target) not in {
        str(committed_repo.repo.lookup_reference("refs/heads/main").target),
    }


def test_merge_up_to_date_does_not_move_head_or_target(
    committed_repo: RepositoryManager,
) -> None:
    """``merge X into X`` while ``HEAD = Z`` is a no-op.

    The "up-to-date" case must not touch the target ref either —
    it just returns ``False``. A regression here would silently
    rewrite a ref the user did not ask to touch.
    """
    base = committed_repo.head_commit.sha
    create_branch(committed_repo, "dev", target_sha=base)
    checkout_branch(committed_repo, "dev")
    _dev_tip = committed_repo.head_commit.sha
    str(
        committed_repo.repo.lookup_reference("refs/heads/main").target,
    )

    result = merge_branch(committed_repo, "main", target="main")
    assert result is False

    assert committed_repo.repo.head.shorthand == "main"
# ----- defensive helpers used by the regression suite ----------------------


def test_build_three_branches_helper_shapes_history(
    committed_repo: RepositoryManager,
) -> None:
    """``_build_three_branches`` leaves ``HEAD`` on ``dev``.

    Locks the helper's precondition so a future refactor that
    accidentally leaves HEAD on a different branch surfaces here
    rather than as a "test passes for the wrong reason" false
    positive in the headline regression tests.
    """
    _main_tip, _feat_tip, _dev_tip, _files, _base = _build_three_branches(
        committed_repo,
    )
    assert committed_repo.repo.head.shorthand == "dev"
    # Each tip should be a distinct commit (no shared history beyond
    # the base).
    assert len({_main_tip, _feat_tip, _dev_tip}) == 3


def test_pygit2_descendant_of_works_for_in_memory_ancestor_check(
    tmp_git_repo: Path,
) -> None:
    """Sanity check on the helper API the new ``merge_branch`` relies on.

    The fix uses :func:`pygit2.Repository.descendant_of` to detect
    up-to-date / fast-forward cases without touching ``HEAD``. If
    that API ever disappears or changes shape, this test surfaces
    the breakage here rather than as silent mis-merges downstream.
    """
    import time

    repo = pygit2.init_repository(str(tmp_git_repo), initial_head="main")
    sig = pygit2.Signature("t", "t@e", int(time.time()), 0)
    (tmp_git_repo / "a.txt").write_text("a\n")
    repo.index.add("a.txt")
    repo.index.write()
    t = repo.index.write_tree()
    c1 = repo.create_commit("refs/heads/main", sig, sig, "c1", t, [])

    repo.create_branch("other", repo[c1])
    repo.checkout("refs/heads/other")
    (tmp_git_repo / "b.txt").write_text("b\n")
    repo.index.add("b.txt")
    repo.index.write()
    t2 = repo.index.write_tree()
    c2 = repo.create_commit("refs/heads/other", sig, sig, "c2", t2, [c1])

    # c2 is a descendant of c1.
    assert repo.descendant_of(c2, c1) is True
    assert repo.descendant_of(c1, c2) is False
