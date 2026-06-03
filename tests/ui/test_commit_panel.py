"""Stage 3 UI tests for the :class:`CommitPanel` widget.

End-to-end flow under ``pytest-qt``: drive the panel via real signal
delivery and a real :class:`MainViewModel` bound to a real
:class:`RepositoryManager`. We mock at the ViewModel level only — the
panel itself is the system under test.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem
from src.core.repository import RepositoryManager
from src.ui.widgets.commit_panel import CommitPanel
from src.viewmodels.main_viewmodel import MainViewModel


def _make_repo_with_change(path: Path) -> RepositoryManager:
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


# ----- list rendering --------------------------------------------------


def test_panel_lists_modified_and_untracked_files(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    paths = [
        panel._files.item(i).data(Qt.ItemDataRole.UserRole)  # noqa: SLF001
        for i in range(panel._files.count())
    ]
    assert set(paths) == {"f.txt", "untracked.txt"}
    # Status badges: M for modified, U for untracked.
    labels = [
        panel._files.item(i).text()  # noqa: SLF001
        for i in range(panel._files.count())
    ]
    assert any(lbl.startswith("[M]") for lbl in labels)
    assert any(lbl.startswith("[U]") for lbl in labels)


def test_panel_file_count_in_header(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    assert panel._files_header.text().startswith("Files (2)")


# ----- commit button ---------------------------------------------------


def test_commit_button_disabled_without_message_or_staged(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()
    assert not panel._commit_button.isEnabled()


def test_commit_button_enables_with_message_and_staged(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # Stage f.txt
    for i in range(panel._files.count()):
        item = panel._files.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "f.txt":
            item.setCheckState(Qt.CheckState.Checked)
    # Type a message
    panel._message.setPlainText("hello")

    assert panel._commit_button.isEnabled()


def test_commit_button_creates_a_commit(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # Stage and type.
    for i in range(panel._files.count()):
        item = panel._files.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "f.txt":
            item.setCheckState(Qt.CheckState.Checked)
    panel._message.setPlainText("commit from ui")

    head_before = str(mgr.repo.head.target)
    panel._commit_button.click()
    head_after = str(mgr.repo.head.target)
    assert head_after != head_before
    assert mgr.repo[head_after].message.strip() == "commit from ui"


# ----- staging via checkbox -------------------------------------------


def test_toggling_checkbox_stages_file(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # Locate the f.txt row and check it.
    target: QListWidgetItem | None = None
    for i in range(panel._files.count()):
        item = panel._files.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "f.txt":
            target = item
            break
    assert target is not None
    target.setCheckState(Qt.CheckState.Checked)

    # The VM's staged_files set should now include "f.txt".
    assert "f.txt" in vm.commit_panel_view_model().staged_files()


def test_unchecking_staged_file_unstages(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    # Find the f.txt row, then check it. After the check, the panel
    # rebuilds the list (refresh_status); refetch by path before
    # unchecking.
    def find_f_item() -> QListWidgetItem | None:
        for i in range(panel._files.count()):
            it = panel._files.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == "f.txt":
                return it
        return None

    first = find_f_item()
    assert first is not None
    first.setCheckState(Qt.CheckState.Checked)
    assert "f.txt" in vm.commit_panel_view_model().staged_files()

    second = find_f_item()
    assert second is not None
    second.setCheckState(Qt.CheckState.Unchecked)
    assert "f.txt" not in vm.commit_panel_view_model().staged_files()


# ----- diff preview ----------------------------------------------------


def test_selecting_file_shows_diff(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    with qtbot.waitSignal(vm.commit_panel_view_model().diff_ready, timeout=2000) as blocker:
        panel._files.setCurrentRow(0)  # noqa: SLF001
    assert "v1" in blocker.args[0] or "v2" in blocker.args[0]


# ----- message binding -------------------------------------------------


def test_message_field_routes_through_viewmodel(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    panel._message.setPlainText("a new subject")  # noqa: SLF001
    assert vm.commit_panel_view_model().commit_message() == "a new subject"


def test_message_clears_after_commit(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_repo_with_change(tmp_git_repo)
    vm = MainViewModel()
    vm.set_repository(mgr)
    panel = CommitPanel(vm)
    qtbot.addWidget(panel)
    panel.show()

    for i in range(panel._files.count()):
        item = panel._files.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == "f.txt":
            item.setCheckState(Qt.CheckState.Checked)
    panel._message.setPlainText("subject")
    panel._commit_button.click()

    assert panel._message.toPlainText() == ""
    assert vm.commit_panel_view_model().commit_message() == ""
