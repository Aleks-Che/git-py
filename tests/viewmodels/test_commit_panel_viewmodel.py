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
from src.core.diff_parser import DiffLineType, parse_diff_lines
from src.core.models import FileStatus
from src.core.repository import RepositoryManager
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel


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


def test_refresh_status_clears_selection_when_file_disappears(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """If the user had a file selected for diff preview and the next
    ``refresh_status`` finds it gone (e.g. after Stash Changes empties
    the working tree), the selection must be cleared so the diff view
    in the centre column also closes.

    Without this the user is stuck looking at a diff whose source
    file is no longer in the right-panel list and so has no
    UI affordance to dismiss.
    """
    _ensure_app()
    from pathlib import Path

    worktree = Path(committed_repo.path)
    # Make the file dirty so the user has something to click.
    (worktree / "hello.txt").write_text("hello, world!\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)
    vm.select_file("hello.txt")
    assert vm.selected_file() == "hello.txt"

    # Simulate the post-stash state: revert the working tree to HEAD
    # (the fixture's ``committed_repo`` second commit has
    # ``"hello, world\n"`` as its tree content).
    (worktree / "hello.txt").write_text("hello, world\n")
    vm.refresh_status()

    assert vm.file_changes() == []
    assert vm.selected_file() is None
    assert vm.current_diff() == ""


def test_refresh_status_keeps_selection_when_file_still_present(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A refresh that still finds the selected file in the new
    status must NOT clear the selection — the diff is still
    relevant (e.g. user edited the file again, status is just
    being re-read)."""
    _ensure_app()
    from pathlib import Path

    worktree = Path(committed_repo.path)
    (worktree / "hello.txt").write_text("hello, world!\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)
    vm.select_file("hello.txt")
    assert vm.selected_file() == "hello.txt"

    # File is still modified, just with different content.
    (worktree / "hello.txt").write_text("hello, world!!\n")
    vm.refresh_status()

    assert vm.selected_file() == "hello.txt"


def test_refresh_status_clears_selection_after_partial_discard(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Discarding only one tracked file must also clear its diff
    selection when the refresh finds the file gone (the user
    discarded the file from the worktree, so it is no longer
    in the status list)."""
    _ensure_app()
    from pathlib import Path

    worktree = Path(committed_repo.path)
    (worktree / "hello.txt").write_text("hello, changed\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)
    vm.select_file("hello.txt")

    # Simulate the post-discard state on disk: file matches HEAD.
    (worktree / "hello.txt").write_text("hello, world\n")
    vm.refresh_status()

    assert vm.selected_file() is None


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


def test_deleted_index_does_not_crash_panel_refresh(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A missing index is reported by the panel instead of escaping a slot."""
    _ensure_app()
    index_path = Path(committed_repo.repo.path) / "index"
    index_path.unlink()
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo, refresh=False)

    with qtbot.waitSignal(vm.error_occurred, timeout=500) as blocker:
        vm.refresh_status()
    assert "index" in blocker.args[0].lower()


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


# ----- build_diff_text (public, used by Copy Diff) ---------------------


def test_build_diff_text_unstaged_tracked(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from pathlib import Path
    (Path(committed_repo.path) / "hello.txt").write_text("hello, world!\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    text = vm.build_diff_text("hello.txt", staged=False)
    assert "hello, world!" in text
    assert "+hello, world!" in text


def test_build_diff_text_staged_tracked(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    _ensure_app()
    from pathlib import Path
    (Path(committed_repo.path) / "hello.txt").write_text("hello, world!\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)
    vm.stage_file("hello.txt")

    text = vm.build_diff_text("hello.txt", staged=True)
    assert "hello, world!" in text


def test_build_diff_text_untracked(
    qtbot, tmp_git_repo: Path,
) -> None:
    _ensure_app()
    (tmp_git_repo / "fresh.txt").write_text("alpha\nbeta\n")
    vm = CommitPanelViewModel()
    vm.set_repository(RepositoryManager(str(tmp_git_repo)))

    text = vm.build_diff_text("fresh.txt", staged=False)
    assert "new file" in text
    assert "+alpha" in text
    assert "+beta" in text


def test_build_diff_text_accepts_context_lines(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``build_diff_text`` exposes ``context_lines`` so callers can
    request the full-document variant (``context_lines=large``)
    independently of the changes-only default of 3.

    The fixture commits a 50-line file first, then mutates one line
    in the worktree. The diff therefore carries *real* context lines
    on either side of the change, and the wider ``context_lines``
    value is expected to produce more of them in the patch text.
    """
    import time

    import pygit2
    from src.core.repository import RepositoryManager
    _ensure_app()

    initial = (
        "first\n"
        + "\n".join(f"line-{i}" for i in range(1, 49))
        + "\nlast\n"
    )
    (tmp_git_repo / "hello.txt").write_text(initial)
    manager = RepositoryManager(str(tmp_git_repo))
    sig = pygit2.Signature("tester", "tester@example.com", int(time.time()), 0)
    manager.repo.index.add("hello.txt")
    manager.repo.index.write()
    tree = manager.repo.index.write_tree()
    manager.repo.create_commit(
        "refs/heads/main", sig, sig, "init", tree, [],
    )

    # Replace the middle line to leave real context on both sides.
    mutated = initial.replace("line-25\n", "line-25-modified\n")
    (tmp_git_repo / "hello.txt").write_text(mutated)

    vm = CommitPanelViewModel()
    vm.set_repository(manager)

    short = vm.build_diff_text("hello.txt", staged=False, context_lines=3)
    long = vm.build_diff_text("hello.txt", staged=False, context_lines=200)
    assert short != long
    from src.core.diff_parser import parse_diff_lines

    short_ctx = sum(
        1 for p in parse_diff_lines(short) if p.line_type.name == "CONTEXT"
    )
    long_ctx = sum(
        1 for p in parse_diff_lines(long) if p.line_type.name == "CONTEXT"
    )
    assert long_ctx > short_ctx


def test_select_file_emits_diff_pair_ready(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Selecting a file emits the ``diff_pair_ready`` signal eagerly
    with the changes-only variant; the full-document variant is built
    lazily on :meth:`request_full_document` (R3.2 P4)."""
    from pathlib import Path
    _ensure_app()
    (Path(committed_repo.path) / "hello.txt").write_text("changed\n")
    vm = CommitPanelViewModel()
    vm.set_repository(committed_repo)

    with qtbot.waitSignal(vm.diff_pair_ready, timeout=500) as blocker:
        vm.select_file("hello.txt")
    changes_only, full_document_initial = blocker.args
    # The eager emission only carries the changes-only variant.
    assert changes_only
    assert "+changed" in changes_only
    assert full_document_initial == "", (
        "R3.2 P4: full_document is now lazy; the eager emission must "
        "leave the second slot empty"
    )

    # After ``request_full_document``, the pair is re-emitted with
    # both variants populated.
    with qtbot.waitSignal(vm.diff_pair_ready, timeout=2000) as blocker:
        vm.request_full_document()
    changes_only2, full_document = blocker.args
    assert changes_only2 == changes_only
    assert full_document
    assert "+changed" in full_document
    # The full-document variant is at least as long as the
    # changes-only one (more context = more lines).
    assert len(full_document) >= len(changes_only)


def test_partial_stage_keeps_file_in_both_lists_and_hides_clicked_line(
    qtbot,
    committed_repo: RepositoryManager,
) -> None:
    worktree = Path(committed_repo.path)
    worktree.joinpath("hello.txt").write_text("hello, world\n1\n2\n3\n")
    main_vm = MainViewModel()
    main_vm.set_repository(committed_repo)
    panel_vm = main_vm.commit_panel_view_model()
    panel_vm.select_file("hello.txt")
    line = next(
        item
        for item in parse_diff_lines(panel_vm.current_diff() or "")
        if item.line_type == DiffLineType.ADDITION and item.text == "+3"
    )

    main_vm.stage_diff_line("hello.txt", line)

    assert "hello.txt" in {item.path for item in panel_vm.unstaged_files()}
    assert "hello.txt" in {item.path for item in panel_vm.staged_files_detailed()}
    assert "+3" not in panel_vm.build_diff_text("hello.txt", staged=False)
    assert "+1" in panel_vm.build_diff_text("hello.txt", staged=False)
    assert "+3" in panel_vm.build_diff_text("hello.txt", staged=True)


def test_partial_stage_command_is_undoable(
    qtbot,
    committed_repo: RepositoryManager,
) -> None:
    worktree = Path(committed_repo.path)
    worktree.joinpath("hello.txt").write_text("hello, world\nextra\n")
    main_vm = MainViewModel()
    main_vm.set_repository(committed_repo)
    panel_vm = main_vm.commit_panel_view_model()
    line = next(
        item
        for item in parse_diff_lines(panel_vm.build_diff_text("hello.txt"))
        if item.line_type == DiffLineType.ADDITION
    )
    before = committed_repo.repo.index["hello.txt"].id

    main_vm.stage_diff_line("hello.txt", line)
    assert committed_repo.repo.index["hello.txt"].id != before
    assert main_vm.command_processor().can_undo

    main_vm.undo()
    assert committed_repo.repo.index["hello.txt"].id == before


def test_stage_multiple_lines_from_refreshed_filtered_diff(
    qtbot,
    committed_repo: RepositoryManager,
) -> None:
    worktree = Path(committed_repo.path)
    worktree.joinpath("hello.txt").write_text("hello, world\none\ntwo\nthree\n")
    main_vm = MainViewModel()
    main_vm.set_repository(committed_repo)
    panel_vm = main_vm.commit_panel_view_model()
    panel_vm.select_file("hello.txt")
    errors: list[str] = []
    main_vm.error_occurred.connect(errors.append)

    for text in ("+one", "+two", "+three"):
        line = next(
            item
            for item in parse_diff_lines(panel_vm.current_diff() or "")
            if item.line_type == DiffLineType.ADDITION and item.text == text
        )
        main_vm.stage_diff_line("hello.txt", line)

    assert errors == []
    staged_text = panel_vm.build_diff_text("hello.txt", staged=True)
    assert all(text in staged_text for text in ("+one", "+two", "+three"))


def test_unstage_all_refreshes_open_partial_diff_to_unstaged_side(
    qtbot,
    committed_repo: RepositoryManager,
) -> None:
    worktree = Path(committed_repo.path)
    worktree.joinpath("hello.txt").write_text(
        "hello, world\none\ntwo\nthree\nfour\n",
    )
    main_vm = MainViewModel()
    main_vm.set_repository(committed_repo)
    panel_vm = main_vm.commit_panel_view_model()
    panel_vm.select_file("hello.txt")
    for text in ("+one", "+two", "+three"):
        line = next(
            item
            for item in parse_diff_lines(panel_vm.current_diff() or "")
            if item.line_type == DiffLineType.ADDITION and item.text == text
        )
        main_vm.stage_diff_line("hello.txt", line)
    panel_vm.select_file("hello.txt", staged=True)
    assert "+four" not in (panel_vm.current_diff() or "")

    main_vm.unstage_all_staged()

    assert panel_vm.staged_files() == []
    assert panel_vm.selected_file() == "hello.txt"
    assert not panel_vm.selected_file_is_staged()
    assert all(
        text in (panel_vm.current_diff() or "")
        for text in ("+one", "+two", "+three", "+four")
    )

