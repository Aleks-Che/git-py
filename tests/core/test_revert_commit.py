"""Revert commits on real repositories, including conflicts and merge parents."""
from pathlib import Path

import pygit2
import pytest
from src.core.exceptions import DirtyWorkTreeError, GitError, MergeConflictError
from src.core.operations import (
    abort_revert,
    checkout_branch,
    commit_changes,
    complete_revert,
    create_branch,
    merge_branch,
    revert_commit,
    revert_head_oid,
)


def test_revert_older_commit_preserves_later_changes_and_branch(committed_repo):
    mgr = committed_repo
    target = mgr.head_commit.sha
    create_branch(mgr, "mine")
    checkout_branch(mgr, "mine")
    (Path(mgr.path) / "later.txt").write_text("keep me\n")
    mgr.repo.index.add("later.txt")
    mgr.repo.index.write()
    tip = commit_changes(mgr, "later", stage_all=False).sha
    author = pygit2.Signature("Reverter", "reverter@example.com")

    result = revert_commit(mgr, target, author=author)

    assert result.parents == [tip]
    assert result.message == f'Revert "greet the world"\n\nThis reverts commit {target}.\n'
    assert result.author_name == result.committer_name == "Reverter"
    assert (Path(mgr.path) / "hello.txt").read_text() == "hello\n"
    assert (Path(mgr.path) / "later.txt").read_text() == "keep me\n"
    assert mgr.repo.head.shorthand == "mine"
    assert str(mgr.repo.branches["main"].target) == target
    assert not mgr.repo.status()
    assert mgr.repo.state() == pygit2.GIT_REPOSITORY_STATE_NONE


def test_revert_root_commit(tmp_git_repo, make_commit):
    from src.core.repository import RepositoryManager

    root = make_commit("root", {"root.txt": "one\n"})
    mgr = RepositoryManager(str(tmp_git_repo))
    result = revert_commit(mgr, str(root))
    assert result.parents == [str(root)]
    assert not (tmp_git_repo / "root.txt").exists()
    assert not mgr.repo.status()


@pytest.mark.parametrize("staged", [False, True])
def test_revert_rejects_local_changes_without_touching_them(committed_repo, staged):
    mgr = committed_repo
    target = mgr.head_commit.sha
    path = Path(mgr.path) / "hello.txt"
    path.write_text("local work\n")
    if staged:
        mgr.repo.index.add("hello.txt")
        mgr.repo.index.write()
    index = mgr.repo.index.write_tree()
    with pytest.raises(DirtyWorkTreeError):
        revert_commit(mgr, target)
    assert mgr.head_commit.sha == target
    assert path.read_text() == "local work\n"
    assert mgr.repo.index.write_tree() == index
    assert not revert_head_oid(mgr)


def test_revert_preserves_unrelated_untracked_file(committed_repo):
    path = Path(committed_repo.path) / "untracked.txt"
    path.write_text("local\n")
    revert_commit(committed_repo, committed_repo.head_commit.sha)
    assert path.read_text() == "local\n"
    assert "untracked.txt" not in committed_repo.repo.head.peel().tree


def test_revert_preserves_unrelated_ignored_file(committed_repo):
    mgr = committed_repo
    (Path(mgr.repo.path) / "info" / "exclude").write_text("local.txt\n")
    path = Path(mgr.path) / "local.txt"
    path.write_text("local\n")
    revert_commit(mgr, mgr.head_commit.sha)
    assert path.read_text() == "local\n"
    assert not mgr.repo.status()


@pytest.mark.parametrize("case", ["detached", "invalid", "pending", "empty"])
def test_revert_rejects_invalid_context(committed_repo, case):
    mgr = committed_repo
    target = mgr.head_commit.sha
    if case == "detached":
        mgr.repo.set_head(mgr.repo.head.target)
    elif case == "invalid":
        target = "missing-revision"
    elif case == "pending":
        (Path(mgr.repo.path) / "MERGE_HEAD").write_text(target + "\n")
    elif case == "empty":
        revert_commit(mgr, target)
    before = mgr.head_commit.sha
    with pytest.raises(GitError):
        revert_commit(mgr, target)
    assert mgr.head_commit.sha == before
    assert not mgr.repo.status()
    assert not revert_head_oid(mgr)


def _conflicting_revert(mgr):
    target = mgr.head_commit.sha
    (Path(mgr.path) / "hello.txt").write_text("later greeting\n")
    tip = commit_changes(mgr, "later greeting").sha
    with pytest.raises(MergeConflictError) as error:
        revert_commit(mgr, target)
    assert error.value.conflicting_paths == ["hello.txt"]
    assert revert_head_oid(mgr) == target
    assert mgr.head_commit.sha == tip
    return target, tip


def test_revert_conflict_continue(committed_repo):
    from src.core.conflict_resolution import load_conflict

    mgr = committed_repo
    target, tip = _conflicting_revert(mgr)
    snapshot = load_conflict(mgr, "hello.txt")
    assert snapshot.theirs_label == f"Before reverted changes ({target[:7]})"
    with pytest.raises(MergeConflictError):
        complete_revert(mgr, target)
    with pytest.raises(GitError, match="changed"):
        complete_revert(mgr, tip)
    (Path(mgr.path) / "hello.txt").write_text("resolved\n")
    mgr.repo.index.add("hello.txt")
    mgr.repo.index.write()
    result = complete_revert(mgr, target)
    assert result.parents == [tip]
    assert result.message.startswith('Revert "greet the world"')
    assert not mgr.repo.status()
    assert not revert_head_oid(mgr)


def test_revert_conflict_abort(committed_repo):
    mgr = committed_repo
    _, tip = _conflicting_revert(mgr)
    abort_revert(mgr)
    assert mgr.head_commit.sha == tip
    assert (Path(mgr.path) / "hello.txt").read_text() == "later greeting\n"
    assert not mgr.repo.status()
    assert not revert_head_oid(mgr)


@pytest.mark.parametrize("mainline", [1, 2])
def test_revert_merge_keeps_selected_parent(committed_repo, mainline):
    mgr = committed_repo
    create_branch(mgr, "feature")
    checkout_branch(mgr, "feature")
    (Path(mgr.path) / "hello.txt").write_text("feature\n")
    commit_changes(mgr, "feature")
    checkout_branch(mgr, "main")
    (Path(mgr.path) / "main.txt").write_text("main only\n")
    mgr.repo.index.add("main.txt")
    mgr.repo.index.write()
    commit_changes(mgr, "main", stage_all=False)
    merge_branch(mgr, "feature", no_ff=True)
    merge = mgr.repo.head.peel()
    with pytest.raises(GitError, match="mainline"):
        revert_commit(mgr, str(merge.id))
    revert_commit(mgr, str(merge.id), mainline=mainline)
    assert mgr.repo.head.peel().tree_id == merge.parents[mainline - 1].tree_id
    assert not mgr.repo.status()
    assert not revert_head_oid(mgr)


def test_revert_in_linked_worktree_does_not_move_main_checkout(linked_worktree, committed_repo):
    original = committed_repo.head_commit.sha
    revert_commit(linked_worktree, original)
    assert committed_repo.head_commit.sha == original
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "hello, world\n"
    assert (Path(linked_worktree.path) / "hello.txt").read_text() == "hello\n"


def _merge_commit(mgr):
    create_branch(mgr, "feature")
    checkout_branch(mgr, "feature")
    (Path(mgr.path) / "hello.txt").write_text("feature\n")
    commit_changes(mgr, "feature")
    checkout_branch(mgr, "main")
    merge_branch(mgr, "feature", no_ff=True)
    return mgr.head_commit.sha


@pytest.mark.parametrize("finish", ["continue", "abort"])
def test_merge_revert_conflict_can_be_finished(committed_repo, finish):
    mgr = committed_repo
    target = _merge_commit(mgr)
    (Path(mgr.path) / "hello.txt").write_text("later\n")
    tip = commit_changes(mgr, "later").sha
    with pytest.raises(MergeConflictError):
        revert_commit(mgr, target, mainline=1)
    assert revert_head_oid(mgr) == target
    if finish == "continue":
        (Path(mgr.path) / "hello.txt").write_text("resolved\n")
        mgr.repo.index.add("hello.txt")
        mgr.repo.index.write()
        assert complete_revert(mgr, target).parents == [tip]
    else:
        abort_revert(mgr)
        assert mgr.head_commit.sha == tip
        assert (Path(mgr.path) / "hello.txt").read_text() == "later\n"
    assert not mgr.repo.status()
    assert not revert_head_oid(mgr)


@pytest.mark.parametrize("merge", [False, True])
def test_failed_revert_commit_can_be_continued(committed_repo, monkeypatch, merge):
    from src.core import operations

    mgr = committed_repo
    target = _merge_commit(mgr) if merge else mgr.head_commit.sha
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise GitError("Cannot write commit")

        patch.setattr(operations, "commit_changes", fail)
        with pytest.raises(GitError, match="Cannot write commit"):
            revert_commit(mgr, target, mainline=1 if merge else 0)
    assert revert_head_oid(mgr) == target
    assert complete_revert(mgr, target).parents == [target]
    assert not mgr.repo.status()


@pytest.mark.parametrize("ignored", [False, True])
def test_revert_does_not_overwrite_untracked_collision(committed_repo, ignored):
    mgr = committed_repo
    path = Path(mgr.path) / "hello.txt"
    path.unlink()
    target = commit_changes(mgr, "delete hello").sha
    if ignored:
        (Path(mgr.repo.path) / "info" / "exclude").write_text("hello.txt\n")
    path.write_text("untracked work\n")
    with pytest.raises(GitError):
        revert_commit(mgr, target)
    assert mgr.head_commit.sha == target
    assert path.read_text() == "untracked work\n"


@pytest.mark.parametrize("collision", [None, "file", "directory", "ancestor_file"])
def test_revert_checks_files_inside_collapsed_ignored_directory(committed_repo, collision):
    """A cached sibling is safe; the exact restored path or a file above it is not."""
    mgr = committed_repo
    root = Path(mgr.path)
    relative = "scripts/__pycache__/nested/restore.pyc"
    restored = root / relative
    restored.parent.mkdir(parents=True)
    restored.write_bytes(b"tracked cache")
    mgr.repo.index.add(relative)
    mgr.repo.index.write()
    commit_changes(mgr, "track cache", stage_all=False)
    restored.unlink()
    restored.parent.rmdir()
    (root / ".gitignore").write_text("__pycache__/\n")
    mgr.repo.index.add(".gitignore")
    mgr.repo.index.write()
    target = commit_changes(mgr, "ignore and remove tracked cache").sha
    sibling = root / "scripts/__pycache__/different.pyc"
    sibling.write_bytes(b"local cache")
    if collision == "file":
        restored.parent.mkdir()
        restored.write_bytes(b"local version")
    elif collision == "directory":
        restored.mkdir(parents=True)
        (restored / "keep.txt").write_bytes(b"local version")
    elif collision == "ancestor_file":
        restored.parent.write_bytes(b"local version")
    assert "scripts/__pycache__/" in mgr.repo.status(ignored=True, untracked_files="all")

    if collision:
        with pytest.raises(DirtyWorkTreeError) as error:
            revert_commit(mgr, target)
        assert "nested" in str(error.value)
        assert mgr.head_commit.sha == target
        local = (restored / "keep.txt" if collision == "directory" else
                 restored.parent if collision == "ancestor_file" else restored)
        assert local.read_bytes() == b"local version"
    else:
        revert_commit(mgr, target)
        assert restored.read_bytes() == b"tracked cache"
        # The same protection is used by the command's Undo/Redo checkout guard.
        from src.core.operations import ensure_safe_tree_update, reset

        result = mgr.head_commit.sha
        # The ignore rule was reverted too; keep the cache ignored for this guard check.
        (Path(mgr.repo.path) / "info" / "exclude").write_text("__pycache__/\n")
        ensure_safe_tree_update(mgr, target, "undo revert")
        reset(mgr, target, mode="hard")
        ensure_safe_tree_update(mgr, result, "redo revert")
        reset(mgr, result, mode="hard")
        assert restored.read_bytes() == b"tracked cache"
    assert sibling.read_bytes() == b"local cache"
