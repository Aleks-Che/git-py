"""The stash toolbar displays a partial failure without hiding the new archive."""
from pathlib import Path

import pygit2
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel
from src.ui.main_window import MainWindow


def test_stash_toolbar_explains_partial_failure(qtbot, committed_repo, monkeypatch):
    root = Path(committed_repo.path)
    (root / "hello.txt").write_text("changes\n")
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window._main_vm._async_enabled = False
    window.set_repository(committed_repo)
    window._main_vm.set_selected_commit("WIP")
    original_stash = pygit2.Repository.stash

    def fail_after_save(repo, signature, message, **kwargs):
        original_stash(repo, signature, message, keep_all=True, **kwargs)
        raise pygit2.GitError(
            "could not rmdir 'C:/work/<project>/scripts/': "
            "The process cannot access the file because it is being used by another process.",
        )

    monkeypatch.setattr(pygit2.Repository, "stash", fail_after_save)
    try:
        assert window._action_stash_push.isEnabled()
        window._action_stash_push.trigger()
        label = window._toast_label.findChild(QLabel)
        assert label.textFormat() == Qt.TextFormat.PlainText
        assert "C:/work/<project>/scripts/" in label.text()
        assert "Папка занята другим процессом" in label.text()
        assert committed_repo.stash_list[0].sha[:12] in label.text()
        assert "Uncommitted Changes" in label.text()
        assert "could not rmdir" not in label.text()
        assert window._main_vm.branch_panel_view_model().stash_list()
        assert not window._main_vm.command_processor().can_undo
    finally:
        window._main_vm.set_auto_fetch_enabled(False)
        window.close()
