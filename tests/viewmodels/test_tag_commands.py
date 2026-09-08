"""Tag command history and errors surfaced by the central ViewModel."""
import pygit2
import pytest
from src.viewmodels.main_viewmodel import MainViewModel


@pytest.fixture
def tag_vm(qapp, committed_repo, tmp_path):
    vm = MainViewModel(config_path=tmp_path / "config.json")
    vm.set_repository(committed_repo)
    return vm


@pytest.mark.parametrize("message", [None, "Release notes"])
def test_tag_undo_redo_refreshes_panels(tag_vm, committed_repo, message):
    target = committed_repo.head_commit.parents[0]
    tag_vm.create_tag("v1", target, message)
    assert tag_vm.branch_panel_view_model().tags()[0].target_sha == target
    assert tag_vm.command_processor().can_undo

    tag_vm.undo()
    assert committed_repo.tags == []
    assert tag_vm.branch_panel_view_model().tags() == []
    assert tag_vm.command_processor().can_redo

    tag_vm.redo()
    assert tag_vm.branch_panel_view_model().tags()[0].target_sha == target
    assert committed_repo.tags[0].is_annotated is (message is not None)


@pytest.mark.parametrize("message", [None, "Release notes"])
def test_undo_preserves_externally_replaced_tag(tag_vm, committed_repo, message):
    tag_vm.create_tag("v1", committed_repo.head_commit.parents[0], message)
    replacement = committed_repo.repo.head.target
    committed_repo.repo.lookup_reference("refs/tags/v1").set_target(replacement)
    errors = []
    tag_vm.error_occurred.connect(errors.append)

    tag_vm.undo()

    assert errors and "changed after creation" in errors[0]
    assert committed_repo.repo.lookup_reference("refs/tags/v1").target == replacement
    assert tag_vm.command_processor().can_undo
    assert not tag_vm.command_processor().can_redo


def test_undo_allows_already_deleted_tag(tag_vm, committed_repo):
    tag_vm.create_tag("v1", committed_repo.head_commit.sha)
    committed_repo.repo.lookup_reference("refs/tags/v1").delete()
    tag_vm.undo()
    assert tag_vm.command_processor().can_redo
    tag_vm.redo()
    assert committed_repo.tags[0].name == "v1"


def test_redo_preserves_tag_created_after_undo(tag_vm, committed_repo):
    tag_vm.create_tag("v1", committed_repo.head_commit.parents[0])
    tag_vm.undo()
    replacement = committed_repo.repo.head.target
    committed_repo.repo.create_reference("refs/tags/v1", replacement)
    errors = []
    tag_vm.error_occurred.connect(errors.append)

    tag_vm.redo()

    assert errors
    assert committed_repo.repo.lookup_reference("refs/tags/v1").target == replacement
    assert tag_vm.command_processor().can_redo
    assert not tag_vm.command_processor().can_undo


@pytest.mark.parametrize("failure", ["invalid", "duplicate", "missing", "busy", "no_repo"])
def test_tag_error_does_not_enter_history(tag_vm, committed_repo, failure):
    name = "bad tag" if failure == "invalid" else "v1"
    target = committed_repo.head_commit.sha
    if failure == "duplicate":
        committed_repo.repo.create_reference("refs/tags/v1", pygit2.Oid(hex=target))
    elif failure == "missing":
        target = "0" * 40
    elif failure == "busy":
        tag_vm._is_busy = True
    elif failure == "no_repo":
        tag_vm.set_repository(None)
    errors = []
    tag_vm.error_occurred.connect(errors.append)

    tag_vm.create_tag(name, target)

    assert len(errors) == 1
    assert not tag_vm.command_processor().can_undo
    assert len(committed_repo.tags) == (1 if failure == "duplicate" else 0)
