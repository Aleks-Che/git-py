"""Right-hand panel that shows details of the currently selected commit.

Stage 3 version: read-only :class:`QTextEdit` populated from
:class:`src.core.repository.RepositoryManager.get_commit`. The
synthetic WIP node is special-cased: its "details" are a one-line
pointer to the WIP / commit panel above, since the WIP is where
the user stages files and types the message.
"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTextEdit, QVBoxLayout, QWidget

from src.viewmodels.graph_viewmodel import WIP_SHA, GraphViewModel


class CommitDetailPanel(QWidget):
    """Read-only commit details, bound to :class:`GraphViewModel`."""

    def __init__(self, view_model: GraphViewModel, parent=None) -> None:
        super().__init__(parent)
        self._view_model = view_model

        self._header = QLabel("Select a commit to see details", self)
        self._header.setWordWrap(True)
        self._header.setStyleSheet("font-weight: bold; padding: 6px;")

        self._body = QTextEdit(self)
        self._body.setReadOnly(True)
        self._body.setPlaceholderText("No commit selected.")
        self._body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._header)
        layout.addWidget(self._body, stretch=1)

        self._view_model.commit_selected.connect(self._on_commit_selected)

    # ----- signal handlers ---------------------------------------------

    def _on_commit_selected(self, sha: str) -> None:
        if sha == WIP_SHA:
            self._header.setText("WIP: Uncommitted changes")
            self._body.setHtml(
                "<p style='color: #8B8B8B;'>"
                "Use the <b>Commit</b> panel above to stage files and write "
                "a commit message. The graph node will be replaced by a "
                "real commit once you click <i>Commit</i>."
                "</p>"
            )
            cursor = self._body.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self._body.setTextCursor(cursor)
            return
        info = self._view_model.get_commit_details(sha)
        if info is None:
            self._header.setText(f"Unknown commit: {sha[:12]}")
            self._body.clear()
            return
        self._header.setText(info.subject or info.short_sha)
        self._body.setHtml(self._render_html(info))
        # Scroll to top so the user always sees the header.
        cursor = self._body.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self._body.setTextCursor(cursor)

    # ----- formatting ---------------------------------------------------

    @staticmethod
    def _render_html(info) -> str:  # noqa: ANN001 - CommitInfo is a dataclass
        parents = ", ".join(p[:7] for p in info.parents) or "(root)"
        lines = [
            f"<p><b>SHA:</b> <code>{info.sha}</code></p>",
            f"<p><b>Author:</b> {info.author_name} &lt;{info.author_email}&gt;</p>",
            f"<p><b>Committed:</b> {info.author_time} (unix)</p>",
            f"<p><b>Parents:</b> {parents}</p>",
            "<hr/>",
            "<pre style='white-space: pre-wrap;'>"
            + (info.message or "").replace("<", "&lt;").replace(">", "&gt;")
            + "</pre>",
        ]
        return "".join(lines)


__all__ = ["CommitDetailPanel"]
