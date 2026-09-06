"""Selected-file reads must preserve bytes and the chosen Git version."""
from pathlib import Path

import pytest
from src.core.exceptions import GitError
from src.core.repository import RepositoryManager


def _commit_index(manager):
    repo = manager.repo
    repo.index.write()
    parents = [] if repo.head_is_unborn else [repo.head.target]
    import pygit2

    sig = pygit2.Signature("tester", "tester@example.com")
    return str(repo.create_commit("HEAD", sig, sig, "image", repo.index.write_tree(), parents))


def test_reads_worktree_index_and_commit_independently(committed_repo):
    manager = committed_repo
    file = Path(manager.path) / "image.png"
    file.write_bytes(b"committed\x00\xff")
    manager.repo.index.add("image.png")
    sha = _commit_index(manager)
    file.write_bytes(b"staged\x00\xfe")
    manager.repo.index.add("image.png")
    manager.repo.index.write()
    file.write_bytes(b"working\x00\xfd")
    index_before = (Path(manager.repo.path) / "index").read_bytes()
    status_before = manager.repo.status()

    assert manager.read_file_content("image.png").data == b"working\x00\xfd"
    assert manager.read_file_content("image.png", staged=True).data == b"staged\x00\xfe"
    assert manager.read_file_content("image.png", sha=sha).data == b"committed\x00\xff"
    assert (Path(manager.repo.path) / "index").read_bytes() == index_before
    assert manager.repo.status() == status_before
    assert str(manager.repo.head.target) == sha


def test_deleted_image_reads_previous_side_at_each_stage(committed_repo):
    manager = committed_repo
    file = Path(manager.path) / "image.png"
    file.write_bytes(b"old image")
    manager.repo.index.add("image.png")
    _commit_index(manager)
    file.write_bytes(b"staged image")
    manager.repo.index.add("image.png")
    manager.repo.index.write()
    file.unlink()
    content = manager.read_file_content("image.png")
    assert content.data == b"staged image"
    assert content.before_deletion

    manager.repo.index.remove("image.png")
    manager.repo.index.write()
    # A newly created worktree file must not replace a staged deletion preview.
    file.write_bytes(b"untracked replacement")
    content = manager.read_file_content("image.png", staged=True)
    assert content.data == b"old image"
    assert content.before_deletion
    deleted_sha = _commit_index(manager)
    content = manager.read_file_content("image.png", sha=deleted_sha)
    assert content.data == b"old image"
    assert content.before_deletion


def test_unborn_repository_and_root_commit(tmp_git_repo):
    manager = RepositoryManager(str(tmp_git_repo))
    (tmp_git_repo / "IMAGE.PNG").write_bytes(b"new image")
    assert manager.read_file_content("IMAGE.PNG").data == b"new image"
    manager.repo.index.add("IMAGE.PNG")
    manager.repo.index.write()
    assert manager.read_file_content("IMAGE.PNG", staged=True).data == b"new image"
    sha = _commit_index(manager)
    content = manager.read_file_content("IMAGE.PNG", sha=sha)
    assert content.data == b"new image"
    assert not content.before_deletion


def test_renamed_image_and_stash_use_saved_tree(committed_repo):
    manager = committed_repo
    file = Path(manager.path) / "old.png"
    file.write_bytes(b"original")
    manager.repo.index.add("old.png")
    _commit_index(manager)
    file.rename(file.with_name("new.png"))
    manager.repo.index.remove("old.png")
    manager.repo.index.add("new.png")
    renamed_sha = _commit_index(manager)
    assert manager.read_file_content("new.png", sha=renamed_sha).data == b"original"

    file.with_name("new.png").write_bytes(b"stashed")
    import pygit2

    stash_sha = manager.repo.stash(pygit2.Signature("tester", "tester@example.com"))
    assert manager.read_file_content("new.png", sha=str(stash_sha)).data == b"stashed"
    assert file.with_name("new.png").read_bytes() == b"original"


@pytest.mark.parametrize("selection", [{}, {"staged": True}, {"sha": "HEAD"}])
def test_missing_file_is_domain_error(committed_repo, selection):
    with pytest.raises(GitError, match="missing.png"):
        committed_repo.read_file_content("missing.png", **selection)


def test_preview_rejects_worktree_escape(committed_repo, tmp_path):
    (tmp_path / "outside.png").write_bytes(b"outside")
    with pytest.raises(GitError, match="outside the working directory"):
        committed_repo.read_file_content("../outside.png")
