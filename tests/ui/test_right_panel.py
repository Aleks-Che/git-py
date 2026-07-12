"""Tests for the redesigned right panel and the selection VM contract.

These cover the behaviour the design spec calls out explicitly:

* The right panel is hidden until a commit is selected.
* Clicking the WIP node opens the commit-input view.
* Clicking a real commit opens the commit-detail view.
* Clicking the *same* commit again toggles selection off
  (the panel disappears).
* After a successful commit the newly created commit is auto-selected
  and the panel switches to the commit-detail view for that SHA.

Most of these exercise :class:`MainViewModel` and
:class:`RightPanel` together — the panel is a thin shell over the VM
signals, so the assertions are mostly about *what the VM emits* and
*what the panel shows*.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from PySide6.QtGui import QColor
from src.core.models import FileStatus
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow
from src.ui.widgets.file_list_model import (
    PATH_TEXT_COLOR,
    STATUS_BADGE,
)
from src.ui.widgets.right_panel import RightPanel
from src.viewmodels.graph_viewmodel import WIP_SHA
from src.viewmodels.main_viewmodel import MainViewModel


def _make_committed_repo(path: Path) -> RepositoryManager:
    """A repo with one commit on ``main`` (a tracked ``f.txt``)."""
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])
    return mgr


def _make_dirty_repo(path: Path) -> RepositoryManager:
    """A repo with one commit plus a working-tree modification."""
    mgr = _make_committed_repo(path)
    (path / "f.txt").write_text("v2\n")
    return mgr


# ----- selection_changed signal contract ---------------------------------


def test_selection_starts_as_none(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    assert vm.selected_commit_sha() is None


def test_selecting_wip_emits_wip_sha(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(WIP_SHA)
    assert blocker.args[0] == WIP_SHA
    assert vm.selected_commit_sha() == WIP_SHA


def test_selecting_real_commit_emits_its_sha(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(head_sha)
    assert blocker.args[0] == head_sha


def test_selecting_same_commit_toggles_off(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)

    vm.select_commit(WIP_SHA)
    assert vm.selected_commit_sha() == WIP_SHA

    # Re-selecting the same SHA clears the selection.
    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(WIP_SHA)
    assert blocker.args[0] is None
    assert vm.selected_commit_sha() is None


def test_selecting_different_commit_replaces_selection(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)

    vm.select_commit(WIP_SHA)
    assert vm.selected_commit_sha() == WIP_SHA

    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.select_commit(head_sha)
    assert blocker.args[0] == head_sha
    assert vm.selected_commit_sha() == head_sha


def test_set_repository_clears_selection(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    assert vm.selected_commit_sha() == WIP_SHA

    with qtbot.waitSignal(vm.selection_changed, timeout=500) as blocker:
        vm.set_repository(None)
    assert blocker.args[0] is None
    assert vm.selected_commit_sha() is None


# ----- commit_changes auto-selects the new commit ----------------------


def test_commit_auto_selects_new_commit(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    vm.commit_panel_view_model().set_commit_summary("new commit")

    head_before = str(mgr.repo.head.target)
    with qtbot.waitSignal(vm.selection_changed, timeout=2000) as blocker:
        vm.commit_changes("new commit")
    head_after = str(mgr.repo.head.target)

    assert head_after != head_before
    assert blocker.args[0] == head_after
    # And the right panel / VM still consider the new commit the
    # current selection.
    assert vm.selected_commit_sha() == head_after


# ----- right-panel visibility / mode switching --------------------------


def test_right_panel_hidden_when_no_selection(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    # The panel starts hidden (no commit selected) — the
    # ``__init__`` calls ``_on_selection_changed(None)`` which sets
    # ``setVisible(False)`` so a freshly-opened window does not show
    # the panel until the user picks a commit.
    assert panel.isHidden()


def test_right_panel_visible_after_selecting_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    # ``isVisibleTo`` is the parent-aware variant — the panel only
    # needs to flag itself as not-hidden for the splitter to give it
    # real estate.
    assert not panel.isHidden()


def test_right_panel_hides_after_toggle_off(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    assert not panel.isHidden()

    vm.select_commit(head_sha)
    assert panel.isHidden()


def test_right_panel_wip_view_shows_commit_input(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(WIP_SHA)
    # Index 0 of the stack is the commit-input view.
    assert panel._stack.currentIndex() == 0
    assert panel._commit_input.isVisible()


def test_right_panel_detail_view_for_real_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    # Index 1 of the stack is the commit-detail view.
    assert panel._stack.currentIndex() == 1
    assert panel._commit_detail.isVisible()


# ----- commit-detail view: structure (message + info + file list) ------


def test_commit_detail_panel_renders_message_and_files(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    detail = panel._commit_detail

    # Subject of the test commit is "first".
    assert "first" in detail._message.text()
    # The file we committed in the fixture ("f.txt") shows up in the
    # changed-files list. pygit2 reports a freshly-tracked file as
    # an *add* in the diff, so the row carries a NEW-status
    # FileChange (the delegate paints the "[A]" badge from it).
    paths = [
        detail._files.item(i).text()  # noqa: SLF001
        for i in range(detail._files.count())  # noqa: SLF001
    ]
    assert any("f.txt" in p for p in paths)
    assert all("<span" not in p for p in paths), (
        "Items must not contain raw HTML markup — the [A] badge is "
        "painted by _FileRowDelegate from the FileChange payload"
    )


def test_commit_detail_panel_file_click_toggles_selection(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Clicking the same file row twice toggles the selection off."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    detail = panel._commit_detail

    item = detail._files.item(0)
    detail._on_files_item_clicked(item)
    assert detail.selected_file() == "f.txt"

    detail._on_files_item_clicked(item)
    assert detail.selected_file() is None


def test_commit_detail_panel_showing_new_commit_clears_selection(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Loading a new commit clears the previously selected file."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    detail = panel._commit_detail
    detail._on_files_item_clicked(detail._files.item(0))
    assert detail.selected_file() is not None

    detail.show_commit(head_sha)
    assert detail.selected_file() is None


# ----- commit-detail author avatar ---------------------------------------


def test_commit_detail_panel_shows_square_avatar_for_author(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The detail panel renders a square author avatar to the left of
    the info block, sized to match two lines of the info font."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    detail = panel._commit_detail

    avatar_label = detail._avatar_label  # noqa: SLF001
    assert avatar_label.isVisible()
    pix = avatar_label.pixmap()
    assert pix is not None
    # Square: width == height, and equals the badge widget's size.
    expected = detail._avatar_size  # noqa: SLF001
    assert pix.width() == expected
    assert pix.height() == expected
    assert avatar_label.width() == expected
    assert avatar_label.height() == expected
    # Height must equal roughly two text lines of the info font.
    from PySide6.QtGui import QFontMetrics
    two_lines = QFontMetrics(detail._info.font()).height() * 2  # noqa: SLF001
    assert expected == two_lines


def test_commit_detail_avatar_uses_author_email_as_seed(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The pixmap is keyed by the author email — same email produces
    a pixmap identical to a freshly-built one. This guards against
    regressions where the panel renders the avatar from a different
    seed than the rest of the UI."""
    from src.utils.avatar import make_avatar_pixmap

    mgr = _make_committed_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # Without a selected commit the badge is hidden — verify that.
    detail = panel._commit_detail
    assert not detail._avatar_label.isVisible()  # noqa: SLF001

    # Select a real commit; the badge should now show the author's
    # identicon rendered from the same algorithm.
    head_sha = mgr.head_commit.sha
    vm.select_commit(head_sha)

    info = mgr.get_commit(head_sha)
    seed = info.author_email or info.author_name
    assert seed, "fixture should produce an author email or name"

    rendered_img = detail._avatar_label.pixmap().toImage()  # noqa: SLF001
    expected_img = make_avatar_pixmap(
        seed, detail._avatar_size,  # noqa: SLF001
    ).toImage()
    # Comparing raw image data avoids any dependence on QPixmap cache identity.
    assert bytes(rendered_img.bits()) == bytes(expected_img.bits())


def test_commit_detail_avatar_hides_in_empty_state(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Without a selected commit the avatar badge is hidden so the
    "Select a commit" placeholder does not show a dangling image."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    detail = panel._commit_detail
    # The empty state is the starting state.
    detail.clear()
    assert not detail._avatar_label.isVisible()  # noqa: SLF001
    assert detail._avatar_label.pixmap().isNull()  # noqa: SLF001


# ----- commit-detail file context menu (right-click) -------------------


def _make_repo_with_stash(path: Path) -> tuple[RepositoryManager, str, str]:
    """A repo with one committed file and a stash holding a modified copy.

    Returns ``(manager, head_sha, stash_sha)``. The committed file is
    ``f.txt``; the stash rewrites it so the changed-files list for the
    stash entry is non-empty.
    """
    mgr = _make_committed_repo(path)
    head_sha = mgr.head_commit.sha
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("v2-stashed\n")
    mgr.repo.stash(sig, "wip", include_untracked=False)
    stash = mgr.stash_list
    assert stash, "stash list should contain the entry we just created"
    return mgr, head_sha, stash[0].sha


def test_commit_detail_context_menu_contains_copy_diff_for_regular_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-clicking a file row on a regular commit exposes *Copy Diff*."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    detail = panel._commit_detail

    menu = detail._build_file_context_menu("f.txt")
    assert menu is not None
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Copy Diff" in texts
    # "Apply stashed file" only appears for stash entries.
    assert "Apply stashed file" not in texts


def test_commit_detail_context_menu_copy_diff_routes_to_main_viewmodel(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    """Triggering the *Copy Diff* action on a regular commit calls the VM."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(head_sha)
    detail = panel._commit_detail

    captured: dict = {}

    def fake_copy(sha: str, path: str) -> None:
        captured["sha"] = sha
        captured["path"] = path

    monkeypatch.setattr(vm, "copy_commit_file_diff", fake_copy)

    menu = detail._build_file_context_menu("f.txt")
    assert menu is not None
    copy_action = next(a for a in menu.actions() if a.text() == "Copy Diff")
    copy_action.trigger()
    assert captured == {"sha": head_sha, "path": "f.txt"}


def test_commit_detail_context_menu_for_stash_has_both_actions(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-clicking a file on a stash entry exposes *Copy Diff* and
    *Apply stashed file*."""
    mgr, _, stash_sha = _make_repo_with_stash(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(stash_sha)
    detail = panel._commit_detail

    menu = detail._build_file_context_menu("f.txt")
    assert menu is not None
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Copy Diff" in texts
    assert "Apply stashed file" in texts


def test_commit_detail_context_menu_copy_diff_for_stash_routes_to_main_viewmodel(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    """*Copy Diff* on a stash entry also routes through the VM with the
    stash SHA, so the VM can pick the right diff source."""
    mgr, _, stash_sha = _make_repo_with_stash(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.select_commit(stash_sha)
    detail = panel._commit_detail

    captured: dict = {}

    def fake_copy(sha: str, path: str) -> None:
        captured["sha"] = sha
        captured["path"] = path

    monkeypatch.setattr(vm, "copy_commit_file_diff", fake_copy)

    menu = detail._build_file_context_menu("f.txt")
    assert menu is not None
    copy_action = next(a for a in menu.actions() if a.text() == "Copy Diff")
    copy_action.trigger()
    assert captured == {"sha": stash_sha, "path": "f.txt"}


def test_commit_detail_context_menu_returns_none_without_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A freshly-constructed detail panel has no commit selected, so the
    right-click menu must be a no-op (``None``)."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    detail = panel._commit_detail
    assert detail._build_file_context_menu("f.txt") is None


# ----- stage_all_unstaged verb ----------------------------------------


def test_stage_all_unstaged_moves_every_file(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    (tmp_git_repo / "new.txt").write_text("n\n")
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    assert set(vm.commit_panel_view_model().unstaged_paths()) == {"f.txt", "new.txt"}

    vm.stage_all_unstaged()
    assert vm.commit_panel_view_model().unstaged_paths() == []
    assert set(vm.commit_panel_view_model().staged_files()) == {"f.txt", "new.txt"}


def test_unstage_all_staged_moves_all_files_back(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_dirty_repo(tmp_git_repo)
    (tmp_git_repo / "new.txt").write_text("n\n")
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.select_commit(WIP_SHA)
    vm.stage_all_unstaged()
    assert set(vm.commit_panel_view_model().staged_files()) == {"f.txt", "new.txt"}

    vm.unstage_all_staged()
    assert vm.commit_panel_view_model().staged_files() == []
    assert set(vm.commit_panel_view_model().unstaged_paths()) == {"f.txt", "new.txt"}


# ----- diff view (graph replacement on file selection) -----------------


def test_selecting_file_switches_graph_stack(qtbot, tmp_git_repo: Path) -> None:
    """Selecting an unstaged file shows the diff view in place of the graph."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    # Initially graph is visible, diff is hidden.
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()

    cp_vm.select_file("f.txt")
    assert window._graph_stack.currentIndex() == 1
    assert window._diff_view.isVisible()
    # The diff text should be non-empty (the file is modified).
    assert len(window._diff_view.toPlainText()) > 0


def test_deselecting_file_returns_graph(qtbot, tmp_git_repo: Path) -> None:
    """Deselecting a file returns the graph view."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    assert window._graph_stack.currentIndex() == 1

    cp_vm.select_file(None)
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()


def test_selecting_staged_file_shows_diff(qtbot, tmp_git_repo: Path) -> None:
    """Selecting a staged file shows the diff view (staged diff)."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)
    window._main_vm.stage_file("f.txt")

    cp_vm = window._main_vm.commit_panel_view_model()
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()

    cp_vm.select_file("f.txt", staged=True)
    assert window._graph_stack.currentIndex() == 1
    assert window._diff_view.isVisible()
    assert len(window._diff_view.toPlainText()) > 0


def test_deselecting_staged_file_returns_graph(qtbot, tmp_git_repo: Path) -> None:
    """Deselecting a staged file returns the graph view."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)
    window._main_vm.stage_file("f.txt")

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt", staged=True)
    assert window._graph_stack.currentIndex() == 1

    cp_vm.select_file(None)
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()


def test_clicking_file_in_commit_detail_shows_diff(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Clicking a file row in the commit-detail panel shows the diff
    in place of the graph (same behaviour as the WIP panel)."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(head_sha)

    detail = window._right_panel._commit_detail
    detail._on_files_item_clicked(detail._files.item(0))

    assert window._graph_stack.currentIndex() == 1
    assert window._diff_view.isVisible()
    assert len(window._diff_view.toPlainText()) > 0


def test_clicking_same_file_in_commit_detail_toggles_off(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Clicking the same file again in the commit-detail panel hides
    the diff and returns the graph."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(head_sha)

    detail = window._right_panel._commit_detail
    item = detail._files.item(0)

    detail._on_files_item_clicked(item)
    assert window._graph_stack.currentIndex() == 1

    detail._on_files_item_clicked(item)
    assert window._graph_stack.currentIndex() == 0
    assert not window._diff_view.isVisible()


def test_switching_commits_clears_file_selection(
    qtbot, tmp_git_repo: Path,
) -> None:
    """When the user clicks a different commit, the file selection
    in the previous commit is cleared and the graph returns."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(head_sha)

    detail = window._right_panel._commit_detail
    detail._on_files_item_clicked(detail._files.item(0))
    assert window._graph_stack.currentIndex() == 1

    # Re-selecting the same commit (toggle-off → toggle-on) clears.
    window._main_vm.select_commit(head_sha)
    assert window._graph_stack.currentIndex() == 0
    assert detail.selected_file() is None


def test_commit_detail_panel_clears_selection_on_hide(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Going back to WIP from a commit-detail view with a file selected
    hides the diff and clears the panel's selection."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(head_sha)

    detail = window._right_panel._commit_detail
    detail._on_files_item_clicked(detail._files.item(0))
    assert window._graph_stack.currentIndex() == 1

    window._main_vm.select_commit(WIP_SHA)
    assert window._graph_stack.currentIndex() == 0


def test_stash_push_closes_open_diff(qtbot, tmp_git_repo: Path) -> None:
    """User-reported regression: clicking *Stash Changes* while a
    file's diff is open in the centre column must close the diff.

    Before the fix the diff view stayed open even though the file
    list in the right panel was emptied by the stash, leaving the
    user no way to dismiss the diff.
    """
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    assert window._diff_view.isVisible()
    assert window._graph_stack.currentIndex() == 1

    # Run the stash verb the same way the toolbar / menu does.
    window._main_vm.stash_push("WIP")

    # After the stash the working tree is clean, the file list in
    # the right panel is empty, and the diff must therefore close.
    assert cp_vm.file_changes() == []
    assert not window._diff_view.isVisible()
    assert window._graph_stack.currentIndex() == 0
    assert cp_vm.selected_file() is None


def test_discard_all_closes_open_diff(qtbot, tmp_git_repo: Path) -> None:
    """Same defensive guarantee for *Discard All Changes*: any
    operation that empties the file list while a diff is open must
    close the diff too."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    assert window._diff_view.isVisible()

    window._main_vm.discard_changes()

    assert cp_vm.file_changes() == []
    assert not window._diff_view.isVisible()
    assert cp_vm.selected_file() is None


# ----- left panel hide / show on diff ---------------------------------


def test_left_panel_visible_by_default(qtbot, tmp_git_repo: Path) -> None:
    """The left panel (branches / tags / stash) is visible when no
    file is selected — that's the normal working state."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    assert window._left_panel.isVisible()


def test_selecting_file_hides_left_panel(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Selecting an unstaged file shows the diff view *and* hides the
    left panel so the diff gets the full width of the centre column,
    matching GitKraken."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    assert window._left_panel.isVisible()

    cp_vm.select_file("f.txt")
    assert window._diff_view.isVisible()
    assert not window._left_panel.isVisible()


def test_deselecting_file_restores_left_panel(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Deselecting a file returns both the graph and the left panel."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    assert not window._left_panel.isVisible()

    cp_vm.select_file(None)
    assert window._left_panel.isVisible()
    assert not window._diff_view.isVisible()


def test_selecting_staged_file_hides_left_panel(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Selecting a staged file hides the left panel (same as unstaged)."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)
    window._main_vm.stage_file("f.txt")

    cp_vm = window._main_vm.commit_panel_view_model()
    assert window._left_panel.isVisible()

    cp_vm.select_file("f.txt", staged=True)
    assert not window._left_panel.isVisible()


def test_clicking_file_in_commit_detail_hides_left_panel(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Clicking a file in the commit-detail panel hides the left panel
    (same contract as the WIP panel)."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(head_sha)

    assert window._left_panel.isVisible()
    detail = window._right_panel._commit_detail
    detail._on_files_item_clicked(detail._files.item(0))
    assert not window._left_panel.isVisible()


def test_switching_commits_with_file_selected_restores_left_panel(
    qtbot, tmp_git_repo: Path,
) -> None:
    """When a file is selected (diff open, left panel hidden) and the
    user switches to a different commit, the file selection is cleared
    and the left panel reappears."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(head_sha)

    detail = window._right_panel._commit_detail
    detail._on_files_item_clicked(detail._files.item(0))
    assert not window._left_panel.isVisible()

    window._main_vm.select_commit(WIP_SHA)
    assert window._left_panel.isVisible()


def test_left_panel_sizes_cached_when_hiding_for_diff(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Opening a diff caches the current splitter sizes so closing the
    window with the diff open does not overwrite the saved layout
    with zeroed-out values for the hidden left panel."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    assert window._top_splitter is not None
    qtbot.waitUntil(
        lambda: any(s > 0 for s in window._top_splitter.sizes()),
        timeout=2000,
    )
    window._top_splitter.setSizes([300, 600, 200])
    normal_sizes = window._top_splitter.sizes()

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")

    # The cache holds the sizes that were current just before the
    # left panel was hidden.
    assert window._last_normal_splitter_sizes == normal_sizes


def test_right_panel_width_unchanged_when_diff_opens(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Opening a diff hides the left panel and would normally let the
    freed space bleed into the right panel (via the splitter's
    stretch factors 5 : 3). The right panel must keep its width
    while the diff is open — the graph absorbs the freed space."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    assert window._top_splitter is not None
    qtbot.waitUntil(
        lambda: any(s > 0 for s in window._top_splitter.sizes()),
        timeout=2000,
    )
    # Asymmetric layout so the right panel has a recognisable
    # width different from the default ~equal split.
    window._top_splitter.setSizes([300, 600, 200])
    qtbot.waitUntil(
        lambda: window._top_splitter.sizes()[0] > 0,
        timeout=2000,
    )
    right_width_before = window._top_splitter.sizes()[2]

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    qtbot.waitUntil(
        lambda: not window._left_panel.isVisible(),
        timeout=2000,
    )
    right_width_during = window._top_splitter.sizes()[2]

    # The right panel must be at the same width as before.
    assert right_width_during == right_width_before
    # And the left panel must really be hidden (zero width).
    assert window._top_splitter.sizes()[0] == 0


def test_right_panel_width_restored_when_diff_closes(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Deselecting a file restores the splitter to its normal sizes
    — left panel reappears, right panel keeps its width, graph
    shrinks back to its normal column."""
    mgr = _make_dirty_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._main_vm.select_commit(WIP_SHA)

    assert window._top_splitter is not None
    qtbot.waitUntil(
        lambda: any(s > 0 for s in window._top_splitter.sizes()),
        timeout=2000,
    )
    window._top_splitter.setSizes([300, 600, 200])
    qtbot.waitUntil(
        lambda: window._top_splitter.sizes()[0] > 0,
        timeout=2000,
    )
    expected = window._top_splitter.sizes()

    cp_vm = window._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    cp_vm.select_file(None)

    qtbot.waitUntil(
        lambda: window._top_splitter.sizes() == expected,
        timeout=2000,
    )
    assert window._top_splitter.sizes() == expected
    assert window._left_panel.isVisible()


# ----- integration with MainWindow ------------------------------------


def test_main_window_right_panel_in_place(qtbot, tmp_git_repo: Path) -> None:
    """The :class:`RightPanel` replaces the old right_vertical splitter
    and is owned by :class:`MainWindow` as ``_right_panel``."""
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert hasattr(window, "_right_panel")
    assert isinstance(window._right_panel, RightPanel)
    # The old attributes are gone.
    assert not hasattr(window, "_right_splitter")
    assert not hasattr(window, "_commit_panel")
    assert not hasattr(window, "_detail_panel")


def test_main_window_graph_click_drives_selection(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The graph widget's commit_selected signal routes through
    :meth:`MainViewModel.select_commit` so the right panel reacts
    to clicks."""
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    right = window._right_panel
    assert not right.isVisible()

    # Emulate a click on the head commit by emitting the graph
    # widget's commit_selected signal.
    window._graph_table.commit_selected.emit(head_sha)

    assert window._main_vm.selected_commit_sha() == head_sha
    assert right.isVisible()


def test_main_window_re_click_toggles_panel_off(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    right = window._right_panel

    window._graph_table.commit_selected.emit(head_sha)
    assert right.isVisible()

    # Clicking the same commit again clears the selection.
    window._graph_table.commit_selected.emit(head_sha)
    assert not right.isVisible()
    assert window._main_vm.selected_commit_sha() is None


def test_main_window_close_repository_hides_right_panel(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)
    window._graph_table.commit_selected.emit(head_sha)
    right = window._right_panel
    assert right.isVisible()

    window.set_repository(None)
    assert not right.isVisible()


# ----- commit-detail file row colour sync ------------------------------


def _render_row_to_image(panel, row: int):
    """Render a single changed-files row to a :class:`QImage`.

    Used by the colour-by-status integration tests below. The returned
    image is exactly one row tall, fully painted, and ready for pixel
    probing.
    """
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    delegate = panel._files.itemDelegate()
    index = panel._files.model().index(row, 0)
    width = max(panel._files.viewport().width(), 200)
    height = delegate.sizeHint(QStyleOptionViewItem(), index).height()
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(0)

    option = QStyleOptionViewItem()
    option.rect = img.rect()
    option.state = QStyle.StateFlag.State_None

    painter = QPainter(img)
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()
    return img


def _badge_pixels(img) -> list:
    """Pixels that lie inside the status-badge square."""
    from PySide6.QtCore import QPoint

    pixels = []
    m = 4
    bs = 16
    for dx in range(bs):
        for dy in range(bs):
            x = m + dx
            y = m + dy
            if 0 <= x < img.width() and 0 <= y < img.height():
                pixels.append(img.pixelColor(QPoint(x, y)))
    return pixels


def _path_text_pixels(img) -> list:
    """Pixels that lie inside the file-path text area."""
    from PySide6.QtCore import QPoint

    m = 4
    bs = 16
    x0 = m + bs + m
    y_centre = img.height() // 2
    pixels = []
    for dx in range(0, 200):
        for dy in range(-6, 7):
            x = x0 + dx
            y = y_centre + dy
            if 0 <= x < img.width() and 0 <= y < img.height():
                pixels.append(img.pixelColor(QPoint(x, y)))
    return pixels


def _greenish(pixels) -> int:

    return sum(
        1 for px in pixels
        if px.green() > px.red() + 25 and px.green() > px.blue() + 25
    )


def _reddish(pixels) -> int:

    return sum(
        1 for px in pixels
        if px.red() > px.green() + 25 and px.red() > px.blue() + 25
    )


def test_commit_detail_new_file_renders_in_green(
    qtbot, tmp_git_repo: Path,
) -> None:
    """An added file's badge is green and its path is the lighter green —
    matching the WIP panel's two-tone scheme.
    """
    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    panel.resize(800, 600)
    vm.select_commit(head_sha)

    detail = panel._commit_detail
    img = _render_row_to_image(detail, 0)
    badge_pixels = _badge_pixels(img)
    path_pixels = _path_text_pixels(img)

    _, badge_color_hex = STATUS_BADGE[FileStatus.NEW]
    path_color_hex = PATH_TEXT_COLOR[FileStatus.NEW]
    badge_qcolor = QColor(badge_color_hex)
    path_qcolor = QColor(path_color_hex)

    assert any(
        abs(px.red() - badge_qcolor.red()) < 10
        and abs(px.green() - badge_qcolor.green()) < 10
        and abs(px.blue() - badge_qcolor.blue()) < 10
        for px in badge_pixels
    ), f"Badge should render in {badge_color_hex}; got {badge_pixels[:3]}"

    assert any(
        abs(px.red() - path_qcolor.red()) < 10
        and abs(px.green() - path_qcolor.green()) < 10
        and abs(px.blue() - path_qcolor.blue()) < 10
        for px in path_pixels
    ), f"Path should render in {path_color_hex}; got {path_pixels[:3]}"


def test_commit_detail_modified_file_uses_neutral_path(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Modified files keep the neutral default for the path text."""
    mgr = _make_committed_repo(tmp_git_repo)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    parent_oid = mgr.repo.head.target
    (tmp_git_repo / "f.txt").write_text("v2\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit(
        "refs/heads/main", sig, sig, "second", tree, [parent_oid],
    )
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    panel.resize(800, 600)
    vm.select_commit(head_sha)

    detail = panel._commit_detail
    paths = [
        detail._files.item(i).text() for i in range(detail._files.count())
    ]
    assert any("f.txt" in p for p in paths)
    assert all("<span" not in p for p in paths), (
        "Items must not contain raw HTML markup — it must be painted by the delegate"
    )


def test_commit_detail_file_item_carries_change_payload(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Each row stores the FileChange under ``_FILE_CHANGE_ROLE`` so the
    delegate can look up badge / path colours.
    """
    from src.ui.widgets.commit_detail_panel import _FILE_CHANGE_ROLE

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = RightPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.select_commit(head_sha)

    detail = panel._commit_detail
    item = detail._files.item(0)
    change = item.data(_FILE_CHANGE_ROLE)
    assert change is not None
    assert change.path == "f.txt"
    assert change.status == FileStatus.NEW
