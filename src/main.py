"""Entry point: create ``QApplication``, show :class:`MainWindow`, run the event loop.

Use as a module (``python -m src.main``) during development. A console
script entry point is intentionally deferred — see
``docs/IMPLEMENTATION_PLAN.md`` Stage 0 notes.
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def main() -> int:
    """Create the application, show the main window, and run the Qt event loop."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
