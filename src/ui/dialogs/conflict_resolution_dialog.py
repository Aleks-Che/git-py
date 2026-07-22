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


def _is_binary_blob(data: bytes) -> bool:
    """Return ``True`` if *data* looks like a binary blob.

    Mirrors git's own heuristic: a NUL byte anywhere in the first
    8 KiB of the content means the file is not safe to render as
    text. Empty data is **not** considered binary — there is nothing
    to corrupt, and treating the empty file as binary would force the
    user into the bytes-only path unnecessarily.
    """
    if not data:
        return False
    sample = data[:8192]
    return b"\x00" in sample


def _decode_text(data: bytes) -> str:
    """Best-effort decode *data* for display in the text panels.

    Order:

    1. UTF-8 (strict) — covers everything git itself treats as
       UTF-8 and is the expected encoding for new commits.
    2. ``cp1251`` — Russian Windows fallback (the locale the project
       occasionally ships content in, even on Linux). ``cp1251`` is
       a strict superset of ISO-8859-1, so single-byte sequences
       that are invalid UTF-8 almost always decode cleanly here.
    3. ``replace`` errors as a last resort — better to show the
       user the file with replacement markers than refuse to
       render at all.
    """
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1251")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def _detect_eol(data: bytes) -> str:
    """Pick the dominant line ending in *data*.

    Counts ``\\r\\n`` and bare ``\\n`` occurrences and returns the one
    that appears more often. Ties (and a string with no newlines)
    default to ``\\n`` because that is git's canonical line ending
    when the index says ``core.autocrlf`` is off.
    """
    crlf = data.count(b"\r\n")
    bare_lf = data.count(b"\n") - crlf
    if crlf > bare_lf:
        return "\r\n"
    return "\n"


def _index_entry_payload(
    repo: pygit2.Repository, entry: pygit2.IndexEntry | None,
) -> tuple[bytes, str | None, bool]:
    """Return ``(raw_bytes, decoded_text, is_binary)`` for *entry*.

    ``raw_bytes`` is the exact blob content (empty when the entry
    is missing or the blob cannot be read); ``decoded_text`` is the
    best-effort UTF-8 / ``cp1251`` decode — ``None`` when the blob
    is binary (callers must not display ``decoded_text`` in that
    case, otherwise the user sees garbage). ``is_binary`` follows
    git's NUL-byte heuristic.
    """
    if entry is None:
        return b"", None, False
    try:
        blob = repo[entry.id]
    except (KeyError, pygit2.GitError):
        return b"", None, False
    data = bytes(blob.data)
    if _is_binary_blob(data):
        return data, None, True
    return data, _decode_text(data), False


class ConflictResolutionDialog(QDialog):
    """Modal dialog for resolving a single conflicted file.

    The four panels are arranged 3-up on top (Ours / Base / Theirs)
    and 1-up on the bottom (Result). The action row lets the user
    copy a side into Result, or accept both halves, and then mark
    the file as resolved.

    For **text** conflicts the panels render decoded text and the
    user can edit the result freely before clicking "Mark Resolved".
    For **binary** conflicts the panels are read-only (decoding the
    bytes would only show garbage), the placeholder text is updated
    to make the binary nature explicit, and the dialog emits the raw
    blob bytes through :attr:`resolved_bytes` instead of
    :attr:`resolved`. The caller is expected to listen for either
    signal and route the payload to
    :meth:`MainViewModel.resolve_conflict`.

    Signals
    -------
    resolved(str)
        Emitted for **text** conflicts when the user clicks "Mark
        Resolved". The payload is the contents of the Result panel
        at that moment.
    resolved_bytes(bytes)
        Emitted for **binary** conflicts when the user clicks "Mark
        Resolved". The payload is the raw bytes of the chosen side
        — never a ``"<binary>"`` placeholder string.
    """

    resolved = Signal(str)
    resolved_bytes = Signal(bytes)

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
        # Raw blob bytes for each side. Empty when the side is
        # missing (e.g. file added on one branch only) or the dialog
        # has not been bound to a conflict yet. ``_is_binary`` is
        # ``True`` iff any of the three sides decodes as binary
        # (see :func:`_is_binary_blob`) — the result panel and the
        # text-side panels become read-only in that case so the
        # user cannot corrupt the bytes via a stray text edit.
        self._base_bytes = b""
        self._ours_bytes = b""
        self._theirs_bytes = b""
        # Bytes selected by the Accept buttons on a binary conflict.
        # Empty until the user clicks one of them; ``Mark Resolved``
        # will then emit :attr:`resolved_bytes` with these bytes.
        self._result_bytes = b""
        # EOL of the text-mode conflict sides — preserved on the
        # resolved text so the merged file matches the surrounding
        # project's line endings instead of silently rewriting
        # CRLF→LF (or vice versa).
        self._eol = "\n"
        self._is_binary = False

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

        Binary blobs are detected via :func:`_is_binary_blob`; when
        any side is binary, all four text panels are forced to
        read-only and the placeholder text is updated to make the
        bytes-only path obvious to the user.
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

        self._base_bytes, base_text, base_bin = _index_entry_payload(repo.repo, base_entry)
        self._ours_bytes, ours_text, ours_bin = _index_entry_payload(repo.repo, ours_entry)
        self._theirs_bytes, theirs_text, theirs_bin = _index_entry_payload(repo.repo, theirs_entry)
        self._is_binary = base_bin or ours_bin or theirs_bin
        # ``_eol`` reflects whichever side provided text — when all
        # three sides are binary we keep the default ``\\n``. The
        # result panel keeps the same EOL when it writes back, so a
        # CRLF project does not silently end up with mixed endings
        # after a merge.
        self._eol = _detect_eol(self._ours_bytes or self._theirs_bytes or self._base_bytes)

        if self._is_binary:
            placeholder = (
                "(binary file — Accept Ours / Accept Theirs writes "
                "the raw bytes; the text editor is disabled)"
            )
            self.ours_view.setPlainText(placeholder)
            self.base_view.setPlainText(placeholder)
            self.theirs_view.setPlainText(placeholder)
            self._result_view.setPlainText("")
            self._ours_text = ""
            self._base_text = ""
            self._theirs_text = ""
        else:
            self._base_text = base_text or ""
            self._ours_text = ours_text or ""
            self._theirs_text = theirs_text or ""
            self.ours_view.setPlainText(self._ours_text)
            self.base_view.setPlainText(self._base_text)
            self.theirs_view.setPlainText(self._theirs_text)
            self._result_view.setPlainText("")

        # For binary conflicts the user has no useful way to edit
        # anything in the result panel — locking the editor down
        # removes the temptation to "fix up" the placeholder text
        # and accidentally save a literal ``<binary>`` string to disk.
        self._result_view.setReadOnly(self._is_binary)
        self._path_label.setText(path)

    def result_text(self) -> str:
        """Return the current contents of the Result panel."""
        return self._result_view.toPlainText()

    def set_result_text(self, text: str) -> None:
        """Programmatically replace the Result panel content (test helper)."""
        self._result_view.setPlainText(text)

    def result_bytes(self) -> bytes:
        """Return the bytes that would be written for the current resolution.

        For **binary** conflicts the bytes are the raw blob of the
        chosen side (set by :meth:`_accept_ours_bytes`,
        :meth:`_accept_theirs_bytes` or :meth:`_accept_both_bytes`).
        For **text** conflicts the bytes are the UTF-8 encoding of
        the Result panel, normalised to match the EOL of the
        surrounding conflict sides so the merged file does not get
        a surprise line-ending conversion.

        Empty bytes mean "nothing has been picked yet" — the user
        must press one of the Accept buttons (or type in the
        Result panel for a text conflict) before Mark Resolved.
        """
        if self._is_binary:
            return bytes(self._result_bytes)
        text = self._result_view.toPlainText()
        if self._eol == "\r\n":
            text = text.replace("\r\n", "\n").replace("\n", "\r\n")
        return text.encode("utf-8")

    def is_binary(self) -> bool:
        """Return ``True`` if the loaded conflict is binary."""
        return self._is_binary

    def accept_ours_bytes(self) -> None:
        """Select the raw bytes of the ``Ours`` side (binary helper)."""
        self._result_bytes = bytes(self._ours_bytes)

    def accept_theirs_bytes(self) -> None:
        """Select the raw bytes of the ``Theirs`` side (binary helper)."""
        self._result_bytes = bytes(self._theirs_bytes)

    def accept_both_bytes(self) -> None:
        """Select both sides concatenated in raw form (binary helper)."""
        self._result_bytes = bytes(self._ours_bytes) + bytes(self._theirs_bytes)

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
        if self._is_binary:
            self.accept_ours_bytes()
            return
        self._result_view.setPlainText(self._ours_text)

    def _accept_theirs(self) -> None:
        if self._is_binary:
            self.accept_theirs_bytes()
            return
        self._result_view.setPlainText(self._theirs_text)

    def _accept_both(self) -> None:
        if self._is_binary:
            self.accept_both_bytes()
            return
        sep = "" if (not self._ours_text or not self._theirs_text) else "\n"
        self._result_view.setPlainText(f"{self._ours_text}{sep}{self._theirs_text}")

    def _on_mark_resolved(self) -> None:
        # Emit the right signal for the conflict kind. Binary
        # conflicts always go through ``resolved_bytes`` so the
        # caller writes raw bytes to disk — the literal text
        # ``"<binary>"`` is never persisted.
        if self._is_binary:
            self.resolved_bytes.emit(bytes(self._result_bytes))
        else:
            self.resolved.emit(self._result_view.toPlainText())
        self.accept()


__all__ = ["ConflictResolutionDialog", "ConflictResolver"]
