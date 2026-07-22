"""Tests for the stash verb methods on :class:`MainViewModel`.

The contract mirrors the rest of the MainViewModel surface:

* the call goes through :class:`CommandProcessor` (so Undo / Redo work);
* on success every downstream view (graph, commit panel, branch panel)
  is refreshed — the left panel's stash group must reflect the change;
* on failure the error is surfaced through ``error_occurred`` and the
  command is *not* pushed onto the undo stack.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _make_dirty(repo: RepositoryManager, text: str = "wip\n") -> None:
    assert repo.path is not None
    (Path(repo.path) / "hello.txt").write_text(text)


# ----- stash_push -------------------------------------------------------


def test_stash_push_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    ok = vm.stash_push("via-vm")
    assert ok is True
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("via-vm")
    assert vm.command_processor().can_undo


def test_stash_push_refreshes_views(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    bp = vm.branch_panel_view_model()
    events: list[None] = []
    bp.references_changed.connect(lambda: events.append(None))
    vm.stash_push("refresh test")
    assert events  # at least one refresh emission


def test_stash_push_clean_worktree_is_noop(committed_repo: RepositoryManager) -> None:
    """A push on a clean worktree still returns True (the command is a
    successful no-op) but does not create a new stash entry."""
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_push() is True
    assert committed_repo.stash_list == []


def test_stash_push_without_repo_emits_error() -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_push() is False
    assert errors
    assert "No repository" in errors[0]


# ----- stash_pop --------------------------------------------------------


def test_stash_pop_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "pop me")
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "pop me")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_pop(0) is True
    assert committed_repo.stash_list == []
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "pop me"


def test_stash_pop_empty_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_pop(0) is False
    assert errors
    assert not vm.command_processor().can_undo


def test_stash_pop_invalid_index_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "only one")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_pop(99) is False
    assert errors
    assert not vm.command_processor().can_undo


# ----- stash_apply ------------------------------------------------------


def test_stash_apply_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "apply me")
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "apply me")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_apply(0) is True
    # Apply keeps the entry.
    assert len(committed_repo.stash_list) == 1
    # Worktree has the content.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "apply me"


def test_stash_apply_empty_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_apply(0) is False
    assert errors


# ----- stash_drop -------------------------------------------------------


def test_stash_drop_via_main_vm(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo)
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "drop me")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    assert vm.stash_drop(0) is True
    assert committed_repo.stash_list == []
    assert vm.command_processor().can_undo


def test_stash_drop_invalid_index_emits_error(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    assert vm.stash_drop(0) is False
    assert errors


# ----- undo / redo ------------------------------------------------------


def test_stash_push_undo_restores_worktree(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "wip-undo")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.stash_push("undoable")
    assert len(committed_repo.stash_list) == 1
    vm.undo()
    # The worktree is restored.
    assert (Path(committed_repo.path) / "hello.txt").read_text() == "wip-undo"


def test_stash_pop_undo_restores_stash_entry(committed_repo: RepositoryManager) -> None:
    _ensure_app()
    _make_dirty(committed_repo, "pop-undo")
    from src.core.operations import stash_push as core_stash_push

    core_stash_push(committed_repo, "pop-undo")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.stash_pop(0)
    assert committed_repo.stash_list == []
    vm.undo()
    # The stash entry is back.
    assert len(committed_repo.stash_list) == 1
    assert committed_repo.stash_list[0].message.endswith("pop-undo")


# ----- apply_stash_files (multi-file) ----------------------------------


def _make_repo_with_multi_file_stash(
    path: Path,
    files: dict[str, str],
) -> tuple[RepositoryManager, str, str]:
    """Create a repo where ``files`` are tracked on HEAD with placeholder
    content, then stash them with new content so the stash entry
    carries modifications to every file.

    Returns ``(manager, head_sha, stash_sha)``. All files must already
    exist on HEAD so the stash ``get_commit_changes`` (which diffs
    against the HEAD tree) reports them as modifications.
    """
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    for name in files:
        full = path / name
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text("placeholder\n")
        mgr.repo.index.add(name)
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    head_sha = mgr.repo.create_commit(
        "refs/heads/main", sig, sig, "init", tree, [],
    )
    for name, content in files.items():
        (path / name).write_text(content)
    mgr.repo.stash(sig, "multi", include_untracked=False)
    stash = mgr.stash_list
    assert stash, "stash list should contain the multi-file entry"
    return mgr, str(head_sha), stash[0].sha


def test_apply_stash_files_applies_every_requested_path(
    tmp_git_repo: Path,
) -> None:
    """``apply_stash_files`` writes each requested file's stash content
    to the working tree and stages it, in the same way as
    ``apply_stash_file`` would in a loop."""
    mgr, _, stash_sha = _make_repo_with_multi_file_stash(
        tmp_git_repo,
        {
            "hello.txt": "hello-applied\n",
            "a.txt": "alpha-applied\n",
            "b.txt": "beta-applied\n",
        },
    )
    vm = MainViewModel()
    vm.set_repository(mgr)

    vm.apply_stash_files(stash_sha, ["a.txt", "b.txt"])

    # Applied files have the stashed content and are staged.
    assert (tmp_git_repo / "a.txt").read_text() == "alpha-applied\n"
    assert (tmp_git_repo / "b.txt").read_text() == "beta-applied\n"
    staged = vm.commit_panel_view_model().staged_files()
    assert "a.txt" in staged
    assert "b.txt" in staged
    # The un-applied file is still missing from the worktree (it was
    # never restored).
    assert not (tmp_git_repo / "hello.txt").exists() or (
        (tmp_git_repo / "hello.txt").read_text() != "hello-applied\n"
    )


def test_apply_stash_files_without_repo_emits_error(
    tmp_git_repo: Path,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.apply_stash_files("deadbeef" * 5, ["a.txt"])
    assert errors
    assert "No repository" in errors[0]


def test_apply_stash_files_empty_list_is_noop(
    tmp_git_repo: Path,
) -> None:
    """Passing no paths is a quiet no-op — no error, no log noise, no
    state change."""
    mgr, _, stash_sha = _make_repo_with_multi_file_stash(
        tmp_git_repo, {"hello.txt": "x"},
    )
    vm = MainViewModel()
    vm.set_repository(mgr)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    # The stash reverts the worktree to HEAD, so hello.txt now holds
    # the placeholder content the fixture committed — capture that as
    # the pre-call snapshot.
    pre = (tmp_git_repo / "hello.txt").read_text()
    vm.apply_stash_files(stash_sha, [])
    assert errors == []
    # Worktree unchanged.
    assert (tmp_git_repo / "hello.txt").read_text() == pre


def test_apply_stash_files_stops_on_first_failure(
    tmp_git_repo: Path,
) -> None:
    """If one file cannot be applied (e.g. unknown path), the error is
    surfaced and no further files are touched. The previously-applied
    files remain applied."""
    mgr, _, stash_sha = _make_repo_with_multi_file_stash(
        tmp_git_repo,
        {"hello.txt": "hello\n", "a.txt": "alpha\n", "b.txt": "beta\n"},
    )
    vm = MainViewModel()
    vm.set_repository(mgr)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.apply_stash_files(stash_sha, ["a.txt", "does-not-exist.txt", "b.txt"])

    # The first apply succeeded; the second failed and stopped the loop.
    assert errors, "second file should have raised GitError"
    assert (tmp_git_repo / "a.txt").read_text() == "alpha\n"
    # b.txt was NOT applied — the loop bailed out before reaching it,
    # so the worktree still holds the post-stash placeholder content
    # the fixture committed to HEAD (the stash reverts the worktree
    # to HEAD).
    assert (tmp_git_repo / "b.txt").read_text() == "placeholder\n"


def test_copy_commit_files_diff_concatenates_per_file(
    tmp_git_repo: Path,
) -> None:
    """``copy_commit_files_diff`` copies each per-file diff prefixed
    with a ``# path: <p>`` header so the result is readable when
    pasted."""
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (tmp_git_repo / "a.txt").write_text("v1\n")
    (tmp_git_repo / "b.txt").write_text("v1\n")
    mgr.repo.index.add("a.txt")
    mgr.repo.index.add("b.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    commit_oid = mgr.repo.create_commit(
        "refs/heads/main", sig, sig, "two new", tree, [],
    )
    sha = str(commit_oid)

    vm = MainViewModel()
    vm.set_repository(mgr)

    captured: dict = {}
    if QApplication.instance() is None:
        QApplication([])
    # Patch copy_to_clipboard so we don't depend on a real clipboard.
    vm.copy_to_clipboard = lambda text: captured.setdefault("text", text)  # type: ignore[assignment]

    vm.copy_commit_files_diff(sha, ["a.txt", "b.txt"])

    text = captured["text"]
    assert "# path: a.txt" in text
    assert "# path: b.txt" in text
    # Both per-file patches follow their headers.
    a_idx = text.index("# path: a.txt")
    b_idx = text.index("# path: b.txt")
    a_block = text[a_idx:b_idx]
    b_block = text[b_idx:]
    assert "a.txt" in a_block
    assert "b.txt" in b_block


def test_copy_commit_files_diff_no_repo_emits_error(
    tmp_git_repo: Path,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.copy_commit_files_diff("deadbeef" * 5, ["a.txt"])
    assert errors
    assert "No repository" in errors[0]


def test_copy_commit_files_diff_empty_paths_is_noop(
    tmp_git_repo: Path,
) -> None:
    _ensure_app()
    # Need an initial commit on HEAD — copy_commit_files_diff would
    # accept any sha but the test wants the VM to behave like an
    # opened repo, so we put one in place.
    mgr = RepositoryManager(str(tmp_git_repo))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    mgr.repo.create_commit(
        "refs/heads/main",
        sig,
        sig,
        "init",
        mgr.repo.TreeBuilder().write(),
        [],
    )
    vm = MainViewModel()
    vm.set_repository(mgr)
    if QApplication.instance() is None:
        QApplication([])
    captured: dict = {}
    vm.copy_to_clipboard = lambda text: captured.setdefault("text", text)  # type: ignore[assignment]
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.copy_commit_files_diff(str(mgr.head_commit.sha), [])
    assert errors == []
    assert "text" not in captured
