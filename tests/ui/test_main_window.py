"""Stage 0: the main window constructs, shows, and exposes the Stage 0 layout."""
from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import Qt
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


def test_app_activation_refreshes_repository(qtbot) -> None:
    """Switching back to the app must trigger a ViewModel refresh.

    Simulates the ``QApplication.applicationStateChanged`` signal that
    Qt fires when the user Alt-Tabs back, un-minimises the window, or
    clicks on the taskbar. The bound :class:`MainViewModel` should
    receive exactly one ``refresh_state`` call so changes made in
    another Git client show up in this UI.
    """
    assert isinstance(QApplication.instance(), QApplication)
    window = MainWindow()
    qtbot.addWidget(window)

    refresh = MagicMock()
    window._main_vm.refresh_state = refresh  # type: ignore[method-assign]

    app = QApplication.instance()
    assert app is not None
    # Active → refresh; inactive → no refresh.
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)

    assert refresh.call_count == 1
    window.close()
