"""Tests for the clipboard-helper verbs on :class:`MainViewModel`.

The *Copy File Path*, *Copy Diff*, and the generic *Copy to clipboard*
helpers all live in the ViewModel layer so widgets stay passive. These
tests pin the public contract: a working diff for the right file goes
onto the system clipboard, errors are surfaced via ``error_occurred``,
and the multi-file variant concatenates per-file patches with a path
header so the result is still readable.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _clipboard_text() -> str:
    return QApplication.clipboard().text()


# ----- copy_file_path ---------------------------------------------------


def test_copy_file_path_writes_to_clipboard(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    mgr = RepositoryManager(str(tmp_git_repo))
    vm = MainViewModel()
    vm.set_repository(mgr)

    vm.copy_file_path("src/foo.py")
    assert _clipboard_text() == "src/foo.py"


# ----- copy_file_diff ---------------------------------------------------


def test_copy_file_diff_unstaged(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    (Path(committed_repo.path) / "hello.txt").write_text("hello, world!\n")
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    vm.copy_file_diff("hello.txt", staged=False)
    text = _clipboard_text()
    assert "hello, world!" in text
    assert "+hello, world!" in text


def test_copy_file_diff_staged(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    (Path(committed_repo.path) / "hello.txt").write_text("hello, world!\n")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.stage_file("hello.txt")

    vm.copy_file_diff("hello.txt", staged=True)
    text = _clipboard_text()
    assert "hello, world!" in text


def test_copy_file_diff_untracked(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    (tmp_git_repo / "fresh.txt").write_text("alpha\nbeta\n")
    vm = MainViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))

    vm.copy_file_diff("fresh.txt", staged=False)
    text = _clipboard_text()
    assert "new file" in text
    assert "+alpha" in text
    assert "+beta" in text


def test_copy_file_diff_without_repo_emits_error(qtbot) -> None:
    _ensure_app()
    vm = MainViewModel()
    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.copy_file_diff("anything.txt")
    assert "No repository open" in blocker.args[0]


# ----- copy_files_diff --------------------------------------------------


def test_copy_files_diff_concatenates_with_headers(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("hello, world!\n")
    (root / "extra.txt").write_text("extra content\n")
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    vm.copy_files_diff(["hello.txt", "extra.txt"], staged=False)
    text = _clipboard_text()
    assert "hello, world!" in text
    assert "extra content" in text
    assert "path: hello.txt" in text
    assert "path: extra.txt" in text


def test_copy_files_diff_empty_list_is_noop(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    QApplication.clipboard().setText("sentinel")

    vm.copy_files_diff([], staged=False)
    assert _clipboard_text() == "sentinel"


def test_copy_files_diff_staged_uses_index(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    (Path(committed_repo.path) / "hello.txt").write_text("hello, world!\n")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    vm.stage_file("hello.txt")

    vm.copy_files_diff(["hello.txt"], staged=True)
    text = _clipboard_text()
    assert "hello, world!" in text
    assert "path: hello.txt" in text


# ----- copy_commit_file_diff (commit-detail right-click) -----------------


def test_copy_commit_file_diff_writes_per_file_patch(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """For a regular commit, the per-file patch goes onto the clipboard."""
    _ensure_app()
    head_sha = committed_repo.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    vm.copy_commit_file_diff(head_sha, "hello.txt")
    text = _clipboard_text()
    # The second commit (head) modified ``hello.txt`` from ``hello\n``
    # to ``hello, world\n`` — the per-file diff is exactly that
    # modification.
    assert "diff --git a/hello.txt b/hello.txt" in text
    assert "-hello" in text
    assert "+hello, world" in text


def test_copy_commit_file_diff_for_stash(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """*Copy Diff* works the same way for stash entries: it puts the
    file-level diff (stash tree vs the commit the stash was taken from)
    onto the clipboard."""
    _ensure_app()
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("hello, stash\n")
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    ok = vm.stash_push("wip")
    assert ok is True
    stash = committed_repo.stash_list
    assert stash
    stash_sha = stash[0].sha

    vm.copy_commit_file_diff(stash_sha, "hello.txt")
    text = _clipboard_text()
    assert "diff --git a/hello.txt b/hello.txt" in text
    assert "-hello, world" in text
    assert "+hello, stash" in text


def test_copy_commit_file_diff_without_repo_emits_error(qtbot) -> None:
    _ensure_app()
    vm = MainViewModel()
    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.copy_commit_file_diff("deadbeef" * 5, "f.txt")
    assert "No repository open" in blocker.args[0]


def test_copy_commit_file_diff_unknown_sha_emits_error(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    QApplication.clipboard().setText("sentinel")
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.copy_commit_file_diff("deadbeef" * 5, "hello.txt")
    assert "No diff available" in blocker.args[0]
    # Clipboard was not touched.
    assert _clipboard_text() == "sentinel"


def test_copy_commit_file_diff_untouched_path_emits_error(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A path that the commit did not touch produces no diff and the
    clipboard must stay untouched."""
    _ensure_app()
    QApplication.clipboard().setText("sentinel")
    head_sha = committed_repo.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(committed_repo)

    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.copy_commit_file_diff(head_sha, "does_not_exist.txt")
    assert "No diff available" in blocker.args[0]
    assert _clipboard_text() == "sentinel"
