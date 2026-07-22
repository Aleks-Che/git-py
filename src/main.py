"""Entry point: create ``QApplication``, show :class:`MainWindow`, run the event loop.

Use as a module (``python -m src.main``) during development. A console
script entry point is intentionally deferred — see
``docs/IMPLEMENTATION_PLAN.md`` Stage 0 notes.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow
from src.utils.config import default_config_path
from src.utils.theme import apply_theme, get_theme


def main() -> int:
    """Create the application, show the main window, and run the Qt event loop."""
    app = QApplication(sys.argv)
    # Apply the dark theme before any widget is constructed so the very
    # first paint of ``MainWindow`` already uses theme colours. The
    # theme name is read from config when one is wired in (Stage 9);
    # for now we default to :data:`DARK_THEME` so the entire app —
    # not just the graph — paints dark on first launch.
    apply_theme(app, get_theme("dark"))
    # Pass the per-user config path so :class:`MainWindow` restores
    # its size and splitter positions from the previous session and
    # writes them back on close. ``default_config_path`` resolves to
    # the Qt AppConfigLocation (``%APPDATA%/git-py/config.json`` on
    # Windows, ``~/.config/git-py/config.json`` on Linux, etc.).
    window = MainWindow(config_path=default_config_path())
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
