"""UI tests for the redesigned :class:`CommitPanel` (right-panel commit-input view).

The panel now has a two-list layout (Unstaged + Staged) and a sticky
commit block at the bottom. Tests run under ``pytest-qt`` and drive
the widget through real signal delivery against a real
:class:`MainViewModel` bound to a real :class:`RepositoryManager`.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from PySide6.QtCore import QItemSelectionModel, QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleOptionViewItem
from src.core.models import FileChange, FileStatus
from src.core.repository import RepositoryManager
from src.ui.widgets.commit_panel import CommitPanel, FileListView
from src.ui.widgets.file_list_model import FileListDelegate, FileListModel
from src.viewmodels.main_viewmodel import MainViewModel


def _make_repo_with_change(path: Path) -> RepositoryManager:
    """Open a repo with one commit, then leave ``f.txt`` modified
    and ``untracked.txt`` as an untracked file."""
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])
    (path / "f.txt").write_text("v2\n")
    (path / "untracked.txt").write_text("u\n")
    return mgr


def _panel_paths(list_view: FileListView) -> list[str]:
    """Return the path stored on every row in ``list_view``."""
    result: list[str] = []
    model = list_view.model()
    for i in range(model.count()):
        change = model.change_at(i)
        if change is not None:
            result.append(change.path)
    return result


def _click_stage_button(view: FileListView, row: int) -> None:
    """Click the painted stage/unstage button on the given *row*."""
    idx = view.model().index(row, 0)
    item_rect = view.visualRect(idx)
    m = FileListDelegate.MARGIN
    bs = FileListDelegate.BUTTON_SIZE
    rh = FileListDelegate.ROW_HEIGHT
    btn_x = item_rect.right() - m - bs + bs // 2
    btn_y = item_rect.top() + (rh - bs) // 2 + bs // 2
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(btn_x, btn_y))


# ----- Unstaged / Staged list rendering --------------------------------


def test_panel_lists_unstaged_modified_and_untracked(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    paths = set(_panel_paths(panel._unstaged_list))
    assert paths == {"f.txt", "untracked.txt"}


def test_panel_staged_list_is_empty_before_any_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert _panel_paths(panel._staged_list) == []


def test_panel_headers_show_counts(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert panel._unstaged_expander.text().strip() == "Unstaged Files (2)"
    assert panel._staged_expander.text().strip() == "Staged Files (0)"


def test_panel_headers_update_after_staging(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    vm.stage_file("f.txt")
    assert panel._unstaged_expander.text().strip() == "Unstaged Files (1)"
    assert panel._staged_expander.text().strip() == "Staged Files (1)"


# ----- Stage All Changes -----------------------------------------------


def test_stage_all_button_disabled_when_nothing_unstaged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    # Stage every file before constructing the panel.
    vm.stage_file("f.txt")
    vm.stage_file("untracked.txt")

    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert not panel._stage_all_button.isEnabled()


def test_stage_all_button_moves_every_file_to_staged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert panel._stage_all_button.isEnabled()
    panel._stage_all_button.click()

    paths = set(_panel_paths(panel._staged_list))
    assert paths == {"f.txt", "untracked.txt"}
    assert _panel_paths(panel._unstaged_list) == []


# ----- Per-row Stage File button ---------------------------------------


def test_clicking_stage_file_button_stages_one_file(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # Locate the row for f.txt and click its painted stage button.
    model = panel._unstaged_list.model()
    for i in range(model.count()):
        change = model.change_at(i)
        if change and change.path == "f.txt":
            _click_stage_button(panel._unstaged_list, i)
            break

    assert "f.txt" in vm.commit_panel_view_model().staged_files()
    assert "f.txt" not in _panel_paths(panel._unstaged_list)
    assert "f.txt" in _panel_paths(panel._staged_list)


# ----- Click on row = select for diff ----------------------------------


def test_clicking_unstaged_row_selects_file(qtbot, tmp_git_repo: Path) -> None:
    """Clicking an unstaged file row selects it for diff preview (does NOT stage)."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    cp_vm = vm.commit_panel_view_model()
    assert cp_vm.selected_file() is None

    index = panel._unstaged_list.model().index(0, 0)
    panel._unstaged_list.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)
    panel._on_unstaged_index_clicked(index)

    # The file is selected (not staged).
    assert cp_vm.selected_file() == "f.txt"
    assert cp_vm.staged_files() == []


def test_clicking_same_unstaged_row_again_deselects(qtbot, tmp_git_repo: Path) -> None:
    """Clicking the same unstaged row again deselects the file."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    cp_vm = vm.commit_panel_view_model()
    index = panel._unstaged_list.model().index(0, 0)

    panel._on_unstaged_index_clicked(index)
    assert cp_vm.selected_file() == "f.txt"

    panel._on_unstaged_index_clicked(index)
    assert cp_vm.selected_file() is None


def test_clicking_staged_row_selects_file(qtbot, tmp_git_repo: Path) -> None:
    """Clicking a staged row selects the file for diff view."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    cp_vm = vm.commit_panel_view_model()
    assert cp_vm.selected_file() is None

    index = panel._staged_list.model().index(0, 0)
    panel._staged_list.selectionModel().select(index, QItemSelectionModel.SelectionFlag.Select)
    panel._on_staged_index_clicked(index)

    assert cp_vm.selected_file() == "f.txt"
    assert cp_vm._selected_file_staged is True


def test_clicking_same_staged_row_again_deselects(qtbot, tmp_git_repo: Path) -> None:
    """Clicking the same staged row a second time deselects the file."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    cp_vm = vm.commit_panel_view_model()
    index = panel._staged_list.model().index(0, 0)

    panel._on_staged_index_clicked(index)
    assert cp_vm.selected_file() == "f.txt"

    panel._on_staged_index_clicked(index)
    assert cp_vm.selected_file() is None


# ----- Commit block (Summary / Description / button) ------------------


def test_commit_button_disabled_without_input_or_staged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert not panel._commit_button.isEnabled()


def test_commit_button_enabled_with_input_and_staged(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("hello")
    assert panel._commit_button.isEnabled()


def test_commit_button_label_singular_and_plural(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # 0 staged -> plural (no files).
    assert panel._commit_button.text() == "Commit Changes to 0 Files"
    panel._summary.setText("hi")
    # Still 0 staged -> button stays disabled.
    assert not panel._commit_button.isEnabled()

    # 1 staged -> singular.
    vm.stage_file("f.txt")
    assert "1 File" in panel._commit_button.text()
    assert "1 Files" not in panel._commit_button.text()

    # 2 staged -> plural again.
    vm.stage_file("untracked.txt")
    assert "2 Files" in panel._commit_button.text()


def test_commit_button_creates_a_real_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("subject")
    panel._description.setPlainText("body line one\nbody line two")
    head_before = str(mgr.repo.head.target)
    panel._commit_button.click()
    head_after = str(mgr.repo.head.target)

    assert head_after != head_before
    # The message should be "summary\n\ndescription" per the design.
    assert mgr.repo[head_after].message.strip() == "subject\n\nbody line one\nbody line two"


def test_commit_button_enabled_by_description_alone(
    qtbot, tmp_git_repo: Path,
) -> None:
    """At least one of summary / description is sufficient."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert not panel._commit_button.isEnabled()
    panel._description.setPlainText("just a body")
    assert panel._commit_button.isEnabled()


def test_summary_routes_through_viewmodel(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("a new subject")
    assert vm.commit_panel_view_model().commit_summary() == "a new subject"


def test_description_routes_through_viewmodel(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._description.setPlainText("multi\nline\nbody")
    assert vm.commit_panel_view_model().commit_description() == "multi\nline\nbody"


def test_inputs_clear_after_commit(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._summary.setText("subject")
    panel._description.setPlainText("body")
    panel._commit_button.click()

    assert panel._summary.text() == ""
    assert panel._description.toPlainText() == ""
    assert vm.commit_panel_view_model().commit_summary() == ""
    assert vm.commit_panel_view_model().commit_description() == ""


# ----- Collapsible expanders ------------------------------------------


def test_unstaged_expander_toggles_list_visibility(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    assert panel._unstaged_list.isVisible()
    panel._unstaged_expander.toggle()
    assert not panel._unstaged_list.isVisible()
    panel._unstaged_expander.toggle()
    assert panel._unstaged_list.isVisible()


# ----- Copy Diff context-menu action ---------------------------------


def test_unstaged_copy_diff_routes_to_main_viewmodel(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    captured: dict = {}

    def fake_copy(path: str, *, staged: bool = False) -> None:
        captured["path"] = path
        captured["staged"] = staged

    monkeypatch.setattr(vm, "copy_file_diff", fake_copy)
    panel._on_unstaged_context_action("copy_diff", "f.txt")
    assert captured == {"path": "f.txt", "staged": False}


def test_staged_copy_diff_routes_to_main_viewmodel(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    captured: dict = {}

    def fake_copy(path: str, *, staged: bool = False) -> None:
        captured["path"] = path
        captured["staged"] = staged

    monkeypatch.setattr(vm, "copy_file_diff", fake_copy)
    panel._on_staged_context_action("copy_diff", "f.txt")
    assert captured == {"path": "f.txt", "staged": True}


def test_unstaged_batch_copy_diff_routes_to_main_viewmodel(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    captured: dict = {}

    def fake_copy(paths, *, staged: bool = False) -> None:
        captured["paths"] = list(paths)
        captured["staged"] = staged

    monkeypatch.setattr(vm, "copy_files_diff", fake_copy)
    panel._on_unstaged_batch_context_action("copy_diff", ["f.txt", "untracked.txt"])
    assert captured == {"paths": ["f.txt", "untracked.txt"], "staged": False}


def test_staged_batch_copy_diff_routes_to_main_viewmodel(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    vm.stage_file("untracked.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    captured: dict = {}

    def fake_copy(paths, *, staged: bool = False) -> None:
        captured["paths"] = list(paths)
        captured["staged"] = staged

    monkeypatch.setattr(vm, "copy_files_diff", fake_copy)
    panel._on_staged_batch_context_action("copy_diff", ["f.txt", "untracked.txt"])
    assert captured == {"paths": ["f.txt", "untracked.txt"], "staged": True}


def test_unstaged_menu_contains_copy_diff_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    index = panel._unstaged_list.model().index(0, 0)
    panel._unstaged_list.selectionModel().select(
        index, QItemSelectionModel.SelectionFlag.Select,
    )
    menu = panel._unstaged_list._build_context_menu(["f.txt"])
    assert menu is not None
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Copy Diff" in texts
    assert "Copy File Path" in texts


def test_staged_menu_contains_copy_diff_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    vm.stage_file("f.txt")
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    index = panel._staged_list.model().index(0, 0)
    panel._staged_list.selectionModel().select(
        index, QItemSelectionModel.SelectionFlag.Select,
    )
    menu = panel._staged_list._build_context_menu(["f.txt"])
    assert menu is not None
    texts = [a.text() for a in menu.actions() if a.text()]
    assert "Copy Diff" in texts
    assert "Copy File Path" in texts


def test_unstaged_multi_select_menu_contains_copy_diff_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    menu = panel._unstaged_list._build_context_menu(["f.txt", "untracked.txt"])
    assert menu is not None
    texts = [a.text() for a in menu.actions() if a.text()]
    assert any("Copy Diff" in t for t in texts)
    assert any("2 Files" in t for t in texts)


def test_unstaged_menu_copy_diff_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Triggering the *Copy Diff* action emits the expected signal."""
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    menu = panel._unstaged_list._build_context_menu(["f.txt"])
    assert menu is not None
    copy_diff_action = next(
        a for a in menu.actions() if a.text() == "Copy Diff"
    )
    with qtbot.waitSignal(
        panel._unstaged_list.context_action_requested,
        timeout=500,
    ) as blocker:
        copy_diff_action.trigger()
    assert blocker.args == ["copy_diff", "f.txt"]


# ----- File path text colour by status ------------------------------


def test_path_text_color_added_statuses_are_green() -> None:
    """NEW and UNTRACKED paint green: the file will be added to the commit."""
    green = FileListDelegate.path_text_color(FileStatus.NEW)
    assert green == "#7CE38B"
    assert FileListDelegate.path_text_color(FileStatus.UNTRACKED) == green


def test_path_text_color_deleted_status_is_red() -> None:
    """DELETED paints red: the file will be removed from the commit."""
    assert FileListDelegate.path_text_color(FileStatus.DELETED) == "#F08A7E"


def test_path_text_color_other_statuses_keep_neutral_default() -> None:
    """Statuses that are neither 'added' nor 'deleted' stay neutral gray."""
    neutral = "#D4D4D4"
    for status in (
        FileStatus.MODIFIED,
        FileStatus.RENAMED,
        FileStatus.COPIED,
        FileStatus.TYPE_CHANGED,
        FileStatus.CONFLICTED,
        FileStatus.IGNORED,
    ):
        assert FileListDelegate.path_text_color(status) == neutral


def _render_row_to_image(status: FileStatus, path: str = "added.txt") -> QImage:
    """Render a single file-list row to a :class:`QImage`.

    Used by the colour-by-status integration tests below.  The
    returned image is exactly one row tall, fully painted, and
    ready for pixel probing.
    """
    model = FileListModel()
    model.set_changes([FileChange(path=path, status=status)])
    delegate = FileListDelegate(staged=False)

    width = 600
    height = FileListDelegate.ROW_HEIGHT
    img = QImage(width, height, QImage.Format.Format_ARGB32)
    img.fill(0)

    option = QStyleOptionViewItem()
    option.rect = img.rect()
    index = model.index(0, 0)

    painter = QPainter(img)
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()
    return img


def _path_text_pixels(img: QImage) -> list[QColor]:
    """Return every pixel of *img* that lies inside the path text area.

    The path starts at ``MARGIN + BADGE_SIZE + MARGIN`` and is drawn
    left-aligned, vertically centred.  We sample a 200-px wide band
    — long enough to cover a real filename regardless of its first
    character — to keep the test robust against antialiasing and
    character choice.
    """
    m = FileListDelegate.MARGIN
    bs = FileListDelegate.BADGE_SIZE
    x0 = m + bs + m
    y_centre = img.height() // 2
    pixels: list[QColor] = []
    for dx in range(0, 200):
        for dy in range(-6, 7):
            x = x0 + dx
            y = y_centre + dy
            if 0 <= x < img.width() and 0 <= y < img.height():
                pixels.append(img.pixelColor(QPoint(x, y)))
    return pixels


def _greenish(pixels: list[QColor]) -> int:
    return sum(
        1 for px in pixels
        if px.green() > px.red() + 25 and px.green() > px.blue() + 25
    )


def _reddish(pixels: list[QColor]) -> int:
    return sum(
        1 for px in pixels
        if px.red() > px.green() + 25 and px.red() > px.blue() + 25
    )


def test_paint_uses_green_for_added_file() -> None:
    """The rendered path of a NEW file is visibly green."""
    img = _render_row_to_image(FileStatus.NEW)
    pixels = _path_text_pixels(img)
    assert _greenish(pixels) > 0, "NEW path should render with green pixels"
    assert _reddish(pixels) == 0, "NEW path should not be red"


def test_paint_uses_green_for_untracked_file() -> None:
    """UNTRACKED files also render green (will be added on ``git add``)."""
    img = _render_row_to_image(FileStatus.UNTRACKED, path="untracked.txt")
    pixels = _path_text_pixels(img)
    assert _greenish(pixels) > 0, "UNTRACKED path should render with green pixels"


def test_paint_uses_red_for_deleted_file() -> None:
    """The rendered path of a DELETED file is visibly red."""
    img = _render_row_to_image(FileStatus.DELETED, path="deleted.txt")
    pixels = _path_text_pixels(img)
    assert _reddish(pixels) > 0, "DELETED path should render with red pixels"
    assert _greenish(pixels) == 0, "DELETED path should not be green"


def test_paint_uses_neutral_color_for_modified_file() -> None:
    """MODIFIED files keep the default neutral gray, not green or red."""
    img = _render_row_to_image(FileStatus.MODIFIED, path="modified.txt")
    pixels = _path_text_pixels(img)
    assert _greenish(pixels) == 0, "MODIFIED path should not be green"
    assert _reddish(pixels) == 0, "MODIFIED path should not be red"

