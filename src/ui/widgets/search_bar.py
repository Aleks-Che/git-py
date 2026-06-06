"""Search bar for filtering commits in the graph.

A :class:`QLineEdit` with a 300 ms debounce timer so the graph
does not re-filter on every keystroke. Emits :attr:`search_requested`
when the debounced text changes; the consumer (typically
:class:`MainWindow`) routes the query to
:meth:`src.viewmodels.graph_viewmodel.GraphViewModel.search_commits`.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QLineEdit


class SearchBar(QLineEdit):
    """Debounced commit search input field."""

    search_requested = Signal(str)
    """Emitted after a 300 ms pause; carries the current search text."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("Search commits (SHA, message, author)…")
        self.setClearButtonEnabled(True)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._emit_search)

        self.textChanged.connect(self._on_text_changed)

    def _on_text_changed(self, text: str) -> None:
        self._debounce.start()

    def _emit_search(self) -> None:
        self.search_requested.emit(self.text().strip())

    def set_search_text(self, text: str) -> None:
        """Set the search text without emitting."""
        self.blockSignals(True)
        try:
            self.setText(text)
        finally:
            self.blockSignals(False)
