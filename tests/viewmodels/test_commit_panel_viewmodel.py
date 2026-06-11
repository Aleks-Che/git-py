"""Tests for :class:`src.viewmodels.commit_panel_viewmodel.CommitPanelViewModel`.

The ViewModel is a ``QObject``; tests are signal-driven and use
``qtbot.waitSignal`` for delivery. Repositories come from the
``committed_repo`` and ``tmp_git_repo`` fixtures in ``conftest.py``.
"""
from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QCoreApplication
from src.core.models import FileStatus
from src.core.repository import RepositoryManager
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


# ----- lifecycle / binding -----------------------------------------------


def test_set_repository_clears_state(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)
    assert vm.file_changes() == []
    assert vm.staged_files() == []
    assert vm.selected_file() is None
    assert vm.current_diff() is None
    assert vm.commit_message() == ""


def test_set_repository_none_emits_clear_signals(qtbot) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    with qtbot.waitSignal(vm.selected_file_changed, timeout=500) as blocker:
        vm.set_repository(None)
    assert blocker.args[0] is None
    assert vm.commit_message() == ""


# ----- refresh_status ------------------------------------------------------


def test_refresh_status_on_unborn_repo_is_empty(qtbot, tmp_git_repo: Path) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))
    assert vm.file_changes() == []
    assert vm.staged_files() == []


def test_refresh_status_picks_up_untracked_file(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    worktree = tmp_git_repo
    (worktree / "scratch.txt").write_text("x\n")
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(worktree)))

    changes = {c.path: c.status for c in vm.file_changes()}
    assert changes == {"scratch.txt": FileStatus.UNTRACKED}
    # Untracked files are not in the index, so nothing is staged.
    assert vm.staged_files() == []


def test_refresh_status_reports_staged_file(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    worktree = tmp_git_repo
    (worktree / "f.txt").write_text("a\n")
    mgr = RepositoryManager(str(worktree))
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    vm = CommitPanelViewModel()
    vm.set_repository(mgr)

    assert vm.staged_files() == ["f.txt"]
    changes = {c.path: c.status for c in vm.file_changes()}
    assert changes["f.txt"] == FileStatus.NEW


def test_refresh_status_reports_staged_modified(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    # Stage a modification of an already-tracked file.
    from pathlib import Path
    worktree = Path(committed_repo.path)
    (worktree / "hello.txt").write_text("hello, modified\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    assert vm.staged_files() == ["hello.txt"]
    changes = {c.path: c.status for c in vm.file_changes()}
    # INDEX_MODIFIED wins over WT_MODIFIED; both are MODIFIED, so the
    # status itself doesn't tell us staging apart, but the staged_files
    # set does.
    assert changes["hello.txt"] == FileStatus.MODIFIED


def test_refresh_status_distinguishes_staged_vs_unstaged_modified(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A modified-but-unstaged file must NOT appear in ``staged_files``."""
    from pathlib import Path
    worktree = Path(committed_repo.path)
    (worktree / "hello.txt").write_text("hello, modified only in worktree\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    # File shows up in status (MODIFIED), but is NOT in staged_files.
    paths_in_status = {c.path for c in vm.file_changes()}
    assert "hello.txt" in paths_in_status
    assert "hello.txt" not in vm.staged_files()


# ----- stage_file / unstage_file -----------------------------------------


def test_stage_file_promotes_untracked_to_staged(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    (tmp_git_repo / "new.txt").write_text("n\n")
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))

    with qtbot.waitSignal(vm.staged_files_changed, timeout=500) as blocker:
        vm.stage_file("new.txt")
    assert blocker.args[0] == ["new.txt"]
    assert vm.staged_files() == ["new.txt"]


def test_stage_file_persists_to_index(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    (tmp_git_repo / "new.txt").write_text("n\n")
    mgr = RepositoryManager(str(tmp_git_repo))
    vm = CommitPanelViewModel()
    vm.set_repository(mgr)
    vm.stage_file("new.txt")

    # Re-read the index from disk to confirm ``index.write`` ran.
    fresh = pygit2.Repository(str(tmp_git_repo))
    assert "new.txt" in fresh.index


def test_stage_deleted_tracked_file(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Staging a file that was deleted from disk must work via ``index.remove()``."""
    _ensure_app()
    from pathlib import Path
    worktree = Path(committed_repo.path)
    (worktree / "hello.txt").unlink()
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    assert "hello.txt" in [c.path for c in vm.unstaged_files()]

    with qtbot.waitSignal(vm.staged_files_changed, timeout=500) as blocker:
        vm.stage_file("hello.txt")
    assert "hello.txt" in blocker.args[0]
    assert "hello.txt" in vm.staged_files()


def test_stage_deleted_then_unstage_restores_unstaged(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Unstaging a staged deletion must restore the file to the unstaged list."""
    _ensure_app()
    from pathlib import Path
    worktree = Path(committed_repo.path)
    (worktree / "hello.txt").unlink()
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    vm.stage_file("hello.txt")
    assert "hello.txt" in vm.staged_files()

    with qtbot.waitSignal(vm.staged_files_changed, timeout=2000) as blocker:
        vm.unstage_file("hello.txt")
    assert "hello.txt" not in vm.staged_files()
    assert blocker.args[0] == []
    # The file should be back in the unstaged list as WT_DELETED.
    unstaged = {c.path for c in vm.unstaged_files()}
    assert "hello.txt" in unstaged


def test_unstage_file_drops_from_index_and_set(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    (tmp_git_repo / "f.txt").write_text("a\n")
    mgr = RepositoryManager(str(tmp_git_repo))
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    vm = CommitPanelViewModel()
    vm.set_repository(mgr)
    assert "f.txt" in vm.staged_files()

    with qtbot.waitSignal(vm.staged_files_changed, timeout=500) as blocker:
        vm.unstage_file("f.txt")
    assert blocker.args[0] == []
    fresh = pygit2.Repository(str(tmp_git_repo))
    assert "f.txt" not in fresh.index


def test_unstage_tracked_file_restores_head_entry(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """For a tracked file, unstage must restore the HEAD entry — not drop it.

    ``index.remove()`` on a tracked file would leave an intent-to-delete
    entry (``INDEX_DELETED``), which the staged-files set still counts
    as staged. The CLI-backed ``git reset HEAD -- <path>`` is what
    correctly restores the HEAD blob into the index.
    """
    from pathlib import Path
    (Path(committed_repo.path) / "hello.txt").write_text("modified\n")
    committed_repo.repo.index.add("hello.txt")
    committed_repo.repo.index.write()
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)
    assert "hello.txt" in vm.staged_files()

    with qtbot.waitSignal(vm.staged_files_changed, timeout=2000) as blocker:
        vm.unstage_file("hello.txt")
    assert "hello.txt" not in vm.staged_files()
    assert blocker.args[0] == []


def test_unstage_unknown_file_does_not_raise(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))
    # Should be a quiet no-op (path not in the index).
    vm.unstage_file("never-added.txt")
    assert vm.staged_files() == []


def test_stage_then_unstage_round_trip(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    (tmp_git_repo / "f.txt").write_text("a\n")
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))

    vm.stage_file("f.txt")
    assert vm.staged_files() == ["f.txt"]
    vm.unstage_file("f.txt")
    assert vm.staged_files() == []


# ----- select_file / diff ------------------------------------------------


def test_select_file_emits_diff_for_tracked_change(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from pathlib import Path
    (Path(committed_repo.path) / "hello.txt").write_text("hello, world!\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    with qtbot.waitSignal(vm.diff_ready, timeout=1000) as blocker:
        vm.select_file("hello.txt")
    text = blocker.args[0]
    assert "hello" in text
    assert "+hello, world!" in text


def test_select_file_emits_diff_for_untracked(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    (tmp_git_repo / "fresh.txt").write_text("alpha\nbeta\n")
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))

    with qtbot.waitSignal(vm.diff_ready, timeout=1000) as blocker:
        vm.select_file("fresh.txt")
    text = blocker.args[0]
    assert "new file" in text
    assert "+alpha" in text
    assert "+beta" in text


def test_select_none_clears_diff(qtbot, committed_repo: RepositoryManager) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)
    with qtbot.waitSignal(vm.diff_ready, timeout=500) as blocker:
        vm.select_file(None)
    assert blocker.args[0] == ""


def test_select_emits_selected_file_changed(qtbot, tmp_git_repo: Path) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))
    with qtbot.waitSignal(vm.selected_file_changed, timeout=500) as blocker:
        vm.select_file("some/path.py")
    assert blocker.args[0] == "some/path.py"


# ----- commit_message -----------------------------------------------------


def test_set_commit_message_emits_change(qtbot) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    with qtbot.waitSignal(vm.commit_message_changed, timeout=500) as blocker:
        vm.set_commit_message("first line")
    assert blocker.args[0] == "first line"
    assert vm.commit_message() == "first line"


def test_set_commit_message_unchanged_does_not_emit(qtbot) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    vm.set_commit_message("same")
    with qtbot.assertNotEmitted(vm.commit_message_changed, wait=200):
        vm.set_commit_message("same")


# ----- error path --------------------------------------------------------


def test_set_repository_to_none_does_not_emit_error(qtbot) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    with qtbot.assertNotEmitted(vm.error_occurred, wait=200):
        vm.set_repository(None)


@pytest.mark.parametrize("bad_path", ["", "does-not-exist.txt"])
def test_stage_unknown_file_emits_error(
    qtbot, tmp_git_repo: Path, bad_path: str,
) -> None:
    _ensure_app()
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))
    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.stage_file(bad_path)
    assert "Failed to stage" in blocker.args[0]
