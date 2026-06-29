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

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


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
