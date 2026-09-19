"""Other worktree snapshots must read their own HEAD/index without mutations."""
from pathlib import Path

import pygit2
from src.core.worktree_status import find_branch_worktree, read_other_worktree_changes


def _state(manager):
    git_dir = Path(manager.repo.path)
    return (
        (git_dir / "HEAD").read_bytes(), (git_dir / "index").read_bytes(),
        (Path(manager.path) / "hello.txt").read_bytes(),
    )


def test_counts_staged_unstaged_untracked_and_preserves_both_checkouts(
    committed_repo, linked_worktree,
):
    root = Path(linked_worktree.path)
    (root / "hello.txt").write_bytes(b"staged\n")
    linked_worktree.repo.index.add("hello.txt")
    linked_worktree.repo.index.write()
    (root / "hello.txt").write_bytes(b"unstaged after staging\n")
    (root / "new.txt").write_bytes(b"untracked\n")
    before = (_state(committed_repo), _state(linked_worktree))
    changes = read_other_worktree_changes(committed_repo)
    assert len(changes) == 1
    entry = changes[0]
    assert entry.path == root.as_posix()
    assert entry.branch == "agents-ide/run/test-worktree"
    assert entry.head_sha == str(linked_worktree.repo.head.target)
    assert entry.count == 2  # staged + unstaged on the same path counts once
    assert (_state(committed_repo), _state(linked_worktree)) == before
    assert read_other_worktree_changes(linked_worktree) == []


def test_linked_checkout_includes_dirty_primary_and_excludes_itself(
    committed_repo, linked_worktree,
):
    for manager in (committed_repo, linked_worktree):
        (Path(manager.path) / "hello.txt").unlink()
    changes = read_other_worktree_changes(linked_worktree)
    assert [(entry.path, entry.branch, entry.count) for entry in changes] == [
        (Path(committed_repo.path).as_posix(), "main", 1),
    ]


def test_clean_and_ignored_worktrees_do_not_create_markers(committed_repo, linked_worktree):
    assert read_other_worktree_changes(committed_repo) == []
    info = Path(committed_repo.repo.path) / "info"
    info.mkdir(exist_ok=True)
    (info / "exclude").write_text("ignored.txt\n", encoding="utf-8")
    (Path(linked_worktree.path) / "ignored.txt").write_text("ignored\n")
    assert read_other_worktree_changes(committed_repo) == []


def test_missing_worktree_is_skipped_without_pruning_registration(
    committed_repo, linked_worktree,
):
    path = Path(linked_worktree.path)
    linked_worktree.close()
    path.rename(path.with_name("temporarily moved"))
    assert read_other_worktree_changes(committed_repo) == []
    assert find_branch_worktree(committed_repo, "agents-ide/run/test-worktree") is None
    assert "linked" in committed_repo.repo.list_worktrees()


def test_unreadable_index_is_reported_without_breaking_active_graph(
    committed_repo, linked_worktree,
):
    index_path = Path(linked_worktree.repo.path) / "index"
    linked_worktree.close()
    index_path.unlink()
    errors = []
    assert read_other_worktree_changes(committed_repo, errors.append) == []
    assert len(errors) == 1 and "index does not exist" in errors[0]


def test_detached_and_unborn_worktree_heads(committed_repo, linked_worktree):
    (Path(linked_worktree.path) / "new.txt").write_text("new\n")
    linked_worktree.repo.set_head(linked_worktree.repo.head.target)
    changes = read_other_worktree_changes(committed_repo)
    assert changes[0].branch is None
    assert changes[0].head_sha == str(linked_worktree.repo.head.target)
    linked_worktree.repo.set_head("refs/heads/unborn-worktree")
    changes = read_other_worktree_changes(committed_repo)
    assert changes[0].branch == "unborn-worktree"
    assert changes[0].head_sha is None
    assert changes[0].count >= 1
    assert linked_worktree.repo.status()["new.txt"] & pygit2.GIT_STATUS_WT_NEW


def test_find_branch_worktree_reads_head_without_scanning_index(
    committed_repo, linked_worktree, monkeypatch,
):
    def no_status(*args):
        raise AssertionError("Locating a branch must not scan worktree status")

    monkeypatch.setattr(type(committed_repo), "get_raw_status", no_status)
    (Path(linked_worktree.repo.path) / "index").unlink()
    assert find_branch_worktree(committed_repo, "agents-ide/run/test-worktree") == (
        Path(linked_worktree.path).as_posix()
    )
    assert find_branch_worktree(linked_worktree, "main") == Path(committed_repo.path).as_posix()
    assert find_branch_worktree(committed_repo, "main") is None


def test_find_branch_worktree_rereads_head_and_does_not_match_commit_oid(
    committed_repo, linked_worktree,
):
    branch = linked_worktree.repo.head.shorthand
    linked_worktree.repo.set_head(linked_worktree.repo.head.target)
    assert find_branch_worktree(committed_repo, branch) is None
    linked_worktree.repo.create_branch("other/local", linked_worktree.repo.head.peel())
    linked_worktree.repo.set_head("refs/heads/other/local")
    assert find_branch_worktree(committed_repo, branch) is None
    assert find_branch_worktree(committed_repo, "other/local") == (
        Path(linked_worktree.path).as_posix()
    )
