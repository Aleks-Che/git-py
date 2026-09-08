"""Tag creation resolves the selected commit and preserves existing refs."""
import pygit2
import pytest
from src.core.exceptions import GitError, InvalidRefError
from src.core.operations import create_tag


@pytest.mark.parametrize("message", [None, "Release notes", ""])
def test_create_tag_at_selected_commit(committed_repo, message):
    repo = committed_repo.repo
    head = repo.head.target
    target = committed_repo.head_commit.parents[0]
    index_tree = repo.index.write_tree()
    status = repo.status()
    tagger = pygit2.Signature("Tag author", "tag@example.com")

    oid = create_tag(committed_repo, "release/v1", target, message, tagger)

    ref = repo.lookup_reference("refs/tags/release/v1")
    assert str(ref.target) == oid
    assert str(ref.peel(pygit2.Commit).id) == target
    tag = committed_repo.tags[0]
    assert tag.is_annotated is (message is not None)
    if message is not None:
        assert tag.message == message
        assert tag.tagger_name == "Tag author"
    assert repo.head.target == head
    assert repo.index.write_tree() == index_tree
    assert repo.status() == status


@pytest.mark.parametrize("message", [None, "Release notes"])
def test_create_tag_does_not_replace_existing_tag(committed_repo, message):
    target = committed_repo.head_commit.parents[0]
    create_tag(committed_repo, "v1", target, message)
    original = committed_repo.repo.lookup_reference("refs/tags/v1").target
    with pytest.raises(GitError):
        create_tag(committed_repo, "v1", committed_repo.head_commit.sha, message)
    assert committed_repo.repo.lookup_reference("refs/tags/v1").target == original


@pytest.mark.parametrize("name", ["", "bad tag", "a..b", "../tag", "tag.lock", "-tag"])
def test_create_tag_rejects_invalid_name(committed_repo, name):
    with pytest.raises(InvalidRefError, match="Invalid tag name"):
        create_tag(committed_repo, name, committed_repo.head_commit.sha)
    assert committed_repo.tags == []


@pytest.mark.parametrize("target", ["missing", "0" * 40, "tree", "blob"])
def test_create_tag_requires_a_commit(committed_repo, target):
    if target == "tree":
        target = str(committed_repo.repo.head.peel(pygit2.Commit).tree_id)
    elif target == "blob":
        target = str(committed_repo.repo.create_blob(b"hello"))
    with pytest.raises(InvalidRefError):
        create_tag(committed_repo, "v1", target)
    assert committed_repo.tags == []
