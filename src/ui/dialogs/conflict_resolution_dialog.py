"""Conflict resolution dialog.

When a merge / rebase / cherry-pick / revert stops with index
conflicts, the user needs a way to pick a resolution per file. The
:class:`ConflictResolutionDialog` shows three read-only panels
(``Ours`` / ``Base`` / ``Theirs``) for the staged blob content and
one editable ``Result`` panel for the final content.

The dialog only handles the **per-file** view: it does not write the
file to disk itself. The caller is expected to listen for
:attr:`resolved` and pass the resulting text to
:meth:`MainViewModel.resolve_conflict` (which stages the file and,
once every path is resolved, finalises the merge / rebase).

Extension point
---------------
:class:`ConflictResolver` is an ABC the future AI resolver will
implement. The dialog does not invoke the resolver directly — that
lives in a higher layer (out of scope for Stage 5) so the dialog
stays decoupled from any specific LLM provider.
"""
from __future__ import annotations

import pygit2
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.repository import RepositoryManager


class ConflictResolver:
    """Abstract base class for automatic conflict resolvers (Stage 9+).

    A concrete implementation receives the staged blob content for
    each side of a conflict and returns the resolved text. The dialog
    does not depend on this class directly; it lives here so future
    AI-backed resolvers have an obvious home.
    """

    def resolve(self, base: str, ours: str, theirs: str) -> str:  # pragma: no cover - abstract
        """Return the resolved text. Concrete subclasses override this."""
        raise NotImplementedError


def _index_entry_to_text(repo: pygit2.Repository, entry: pygit2.IndexEntry | None) -> str:
    """Decode the blob pointed to by ``entry`` as UTF-8 text.

    Returns an empty string if the entry is ``None`` (missing side of
    the conflict, e.g. a file added on one branch only) or the blob
    is binary — binary content is shown as ``"<binary>"`` so the user
    is not shown garbage.
    """
    if entry is None:
        return ""
    try:
        blob = repo[entry.id]
    except (KeyError, pygit2.GitError):
        return ""
    data = bytes(blob.data)
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return "<binary>"


class ConflictResolutionDialog(QDialog):
    """Modal dialog for resolving a single conflicted file.

    The four panels are arranged 3-up on top (Ours / Base / Theirs)
    and 1-up on the bottom (Result). The action row lets the user
    copy a side into Result, or accept both halves, and then mark
    the file as resolved.

    Signals
    -------
    resolved(str)
        Emitted when the user clicks "Mark Resolved". The payload is
        the contents of the Result panel at that moment.
    """

    resolved = Signal(str)

    def __init__(
        self,
        repo: RepositoryManager | None = None,
        path: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resolve Conflict")
        self.resize(960, 720)

        self._repo: RepositoryManager | None = None
        self._path: str | None = None
        self._base_text = ""
        self._ours_text = ""
        self._theirs_text = ""

        self._build_ui()

        if repo is not None and path is not None:
            self.set_conflict(repo, path)

    # ----- public API --------------------------------------------------

    def set_conflict(self, repo: RepositoryManager, path: str) -> None:
        """Load the staged ``Ours`` / ``Base`` / ``Theirs`` content for ``path``.

        Reads ``repo.repo.index.conflicts`` and finds the entry whose
        side paths match ``path``. Populates the read-only panels
        with the decoded blob content; the Result panel starts empty
        so the user picks a side explicitly.
        """
        self._repo = repo
        self._path = path
        base_entry = ours_entry = theirs_entry = None
        conflicts_attr = getattr(repo.repo.index, "conflicts", None)
        if conflicts_attr:
            for entry in conflicts_attr:
                ancestor, ours, theirs = entry
                sides = (ancestor, ours, theirs)
                if any(s is not None and getattr(s, "path", None) == path for s in sides):
                    base_entry, ours_entry, theirs_entry = sides
                    break
        self._base_text = _index_entry_to_text(repo.repo, base_entry)
        self._ours_text = _index_entry_to_text(repo.repo, ours_entry)
        self._theirs_text = _index_entry_to_text(repo.repo, theirs_entry)

        self.ours_view.setPlainText(self._ours_text)
        self.base_view.setPlainText(self._base_text)
        self.theirs_view.setPlainText(self._theirs_text)
        self._result_view.setPlainText("")
        self._path_label.setText(path)

    def result_text(self) -> str:
        """Return the current contents of the Result panel."""
        return self._result_view.toPlainText()

    def set_result_text(self, text: str) -> None:
        """Programmatically replace the Result panel content (test helper)."""
        self._result_view.setPlainText(text)

    # ----- internals ---------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._path_label = QLabel("(no file)")
        self._path_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._path_label)

        # Three read-only panels: ours / base / theirs.
        read_only_row = QHBoxLayout()
        read_only_row.addWidget(self._make_panel("Ours", "ours_view"))
        read_only_row.addWidget(self._make_panel("Base", "base_view"))
        read_only_row.addWidget(self._make_panel("Theirs", "theirs_view"))
        layout.addLayout(read_only_row, stretch=1)

        # Editable result panel.
        self._result_view = QPlainTextEdit()
        self._result_view.setPlaceholderText(
            "Edit the merged result, or use the buttons below to copy a side.",
        )
        result_label = QLabel("Result")
        result_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(result_label)
        layout.addWidget(self._result_view, stretch=1)

        # Action buttons.
        button_row = QHBoxLayout()
        self._accept_ours_btn = QPushButton("Accept Ours")
        self._accept_ours_btn.clicked.connect(self._accept_ours)
        button_row.addWidget(self._accept_ours_btn)

        self._accept_theirs_btn = QPushButton("Accept Theirs")
        self._accept_theirs_btn.clicked.connect(self._accept_theirs)
        button_row.addWidget(self._accept_theirs_btn)

        self._accept_both_btn = QPushButton("Accept Both (ours + theirs)")
        self._accept_both_btn.clicked.connect(self._accept_both)
        button_row.addWidget(self._accept_both_btn)
        layout.addLayout(button_row)

        # Standard OK / Cancel: OK is repurposed as "Mark Resolved".
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        ok_btn = self._button_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Mark Resolved")
        ok_btn.setDefault(True)
        self._button_box.accepted.connect(self._on_mark_resolved)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    def _make_panel(self, title: str, attr: str) -> QWidget:
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setStyleSheet("font-weight: bold;")
        v.addWidget(label)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        v.addWidget(view)
        setattr(self, attr, view)
        return container

    def _accept_ours(self) -> None:
        self._result_view.setPlainText(self._ours_text)

    def _accept_theirs(self) -> None:
        self._result_view.setPlainText(self._theirs_text)

    def _accept_both(self) -> None:
        sep = "" if (not self._ours_text or not self._theirs_text) else "\n"
        self._result_view.setPlainText(f"{self._ours_text}{sep}{self._theirs_text}")

    def _on_mark_resolved(self) -> None:
        self.resolved.emit(self._result_view.toPlainText())
        self.accept()


__all__ = ["ConflictResolutionDialog", "ConflictResolver"]
