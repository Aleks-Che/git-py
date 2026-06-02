"""Stage 0: the main window constructs, shows, and exposes the Stage 0 layout."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication
from src.ui.main_window import MainWindow


def test_main_window_builds(qtbot) -> None:
    assert isinstance(QApplication.instance(), QApplication)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.windowTitle() == "git-py"
    assert window.isVisible()
    window.close()
