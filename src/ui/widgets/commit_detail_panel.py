"""Right-panel commit-detail view: read-only summary for a selected commit.

Shown when the user clicks a real (non-WIP) commit in the graph.
Structure (top-to-bottom):

* **Message** — the commit subject on the first line; the body in a
  monospace block under it.
* **Info** — author, committer, time, full SHA, parents.
* **Changed files** — one row per file the commit touched, with the
  usual ``M`` / ``A`` / ``D`` / ``R`` / ``C`` / ``T`` badge.

The widget is bound to :class:`MainViewModel` for the commit's
``CommitInfo`` and to :class:`RepositoryManager` (through the VM) for
the list of changed files. It does not render a unified diff — Stage 3
kept a diff preview here, but the new layout surfaces file lists in
both the commit view and the WIP view, so a side-by-side diff is
redundant.

The widget is read-only: there are no editing controls and no
verb-method calls. The user returns to the WIP / commit-input view by
clicking the WIP node in the graph.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.exceptions import GitError
from src.core.models import CommitInfo, FileChange, FileStatus
from src.viewmodels.main_viewmodel import MainViewModel

# Short status letter + colour for the changed-files list. Reused from
# the old :class:`CommitPanel` so the visual language stays consistent.
_STATUS_BADGE: dict[FileStatus, tuple[str, str]] = {
    FileStatus.NEW: ("A", "#43BCCD"),
    FileStatus.MODIFIED: ("M", "#F5B947"),
    FileStatus.DELETED: ("D", "#E8685A"),
    FileStatus.RENAMED: ("R", "#5B8FF9"),
    FileStatus.COPIED: ("C", "#A371F7"),
    FileStatus.UNTRACKED: ("U", "#3FB950"),
    FileStatus.TYPE_CHANGED: ("T", "#F0883E"),
    FileStatus.CONFLICTED: ("!", "#FF6B6B"),
    FileStatus.IGNORED: ("I", "#8B8B8B"),
}


class CommitDetailPanel(QWidget):
    """Read-only view of a single commit, bound to :class:`MainViewModel`."""

    def __init__(self, main_view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._main_vm = main_view_model

        self._build_ui()
        self._render_empty()

    # ----- construction -----------------------------------------------

    def _build_ui(self) -> None:
        # --- message block ---
        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        self._message.setStyleSheet(
            "font-size: 14px; font-weight: 600; padding: 4px 0;",
        )

        self._body = QLabel(self)
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        self._body.setStyleSheet(
            "font-family: Consolas, monospace; font-size: 11pt; color: #D4D4D4; "
            "background-color: #1E1E1E; border: 1px solid #3F3F46; border-radius: 3px; "
            "padding: 6px;",
        )
        self._body.setVisible(False)
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # --- info block ---
        self._info = QLabel(self)
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        self._info.setStyleSheet("color: #8B8B8B; font-size: 11px; padding: 2px 0;")
        self._info.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # --- changed files ---
        self._files_header = QLabel("Changed Files (0)", self)
        self._files_header.setStyleSheet(
            "font-weight: bold; padding: 6px 0 2px 0; color: #D4D4D4;",
        )

        self._files = QListWidget(self)
        self._files.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._files.setUniformItemSizes(True)
        self._files.setAlternatingRowColors(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addWidget(self._message)
        layout.addWidget(self._body)
        layout.addSpacing(6)
        layout.addWidget(self._info)
        layout.addSpacing(6)
        layout.addWidget(self._files_header)
        layout.addWidget(self._files, stretch=1)

    # ----- public API --------------------------------------------------

    def show_commit(self, sha: str) -> None:
        """Populate the panel for the commit at ``sha``.

        Errors are swallowed — the user already clicked a valid graph
        node; if the repo state has drifted (race with a background
        op) we just leave the panel empty rather than pop a dialog.
        """
        repo = self._main_vm.repository_manager()
        if repo is None or not repo.is_open:
            self._render_empty()
            return
        try:
            info = repo.get_commit(sha)
        except GitError:
            self._render_empty()
            return
        if info is None:
            self._render_empty()
            return
        try:
            changes = repo.get_commit_changes(sha)
        except GitError:
            changes = []
        self._populate(info, changes)

    def clear(self) -> None:
        """Reset the panel to the empty state."""
        self._render_empty()

    # ----- rendering ---------------------------------------------------

    def _render_empty(self) -> None:
        self._message.setText("Select a commit")
        self._message.setStyleSheet(
            "color: #8B8B8B; font-style: italic; padding: 4px 0;",
        )
        self._body.setVisible(False)
        self._body.clear()
        self._info.clear()
        self._files_header.setText("Changed Files (0)")
        self._files.clear()

    def _populate(self, info: CommitInfo, changes: list[FileChange]) -> None:
        subject, body = _split_message(info.message or "")

        # Subject on the first line; if there's no subject, fall back
        # to the short SHA. The body is rendered as a monospace block
        # when it's non-empty.
        if subject:
            self._message.setText(subject)
            self._message.setStyleSheet(
                "font-size: 14px; font-weight: 600; padding: 4px 0; color: #D4D4D4;",
            )
        else:
            self._message.setText(info.short_sha or info.sha[:7])
            self._message.setStyleSheet(
                "font-size: 14px; font-weight: 600; padding: 4px 0; "
                "color: #8B8B8B; font-style: italic;",
            )
        if body:
            self._body.setText(body)
            self._body.setVisible(True)
        else:
            self._body.clear()
            self._body.setVisible(False)

        self._info.setText(_format_info(info))

        # File list.
        self._files.clear()
        for change in changes:
            self._append_change_item(change)
        self._files_header.setText(
            f"Changed Files ({len(changes)})"
            if len(changes) != 1
            else "Changed Files (1)",
        )

    def _append_change_item(self, change: FileChange) -> None:
        badge, color_hex = _STATUS_BADGE.get(change.status, ("?", "#8B8B8B"))
        item = QListWidgetItem(f"[{badge}]  {change.path}", self._files)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        item.setForeground(QBrush(QColor(color_hex)))


__all__ = ["CommitDetailPanel"]


# ----- module helpers (kept private) ----------------------------------


def _split_message(message: str) -> tuple[str, str]:
    """Split a commit message into ``(subject, body)``.

    The subject is the first non-empty line; the body is everything
    after the first blank line that follows the subject (the standard
    git layout). If there is no body, the second tuple element is
    an empty string.
    """
    text = message.replace("\r\n", "\n")
    lines = text.split("\n")
    # First non-empty line is the subject.
    subject = ""
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx < len(lines):
        subject = lines[idx].rstrip()
        idx += 1
    # Skip the blank line that separates subject from body (if any).
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    body_lines = lines[idx:]
    body = "\n".join(line.rstrip() for line in body_lines).rstrip()
    return subject, body


def _format_info(info: CommitInfo) -> str:
    """Format the info block (author, committer, time, SHA, parents).

    Times are rendered as ``YYYY-MM-DD HH:MM:SS`` from the unix
    timestamp — we don't pull the system locale in here because the
    surrounding UI is intentionally English.
    """
    parts: list[str] = []
    if info.author_name or info.author_email:
        author = info.author_name or "(unknown)"
        email = f" <{info.author_email}>" if info.author_email else ""
        parts.append(f"<b>Author:</b> {author}{email}")
    if info.committer_name and (
        info.committer_name != info.author_name
        or info.committer_email != info.author_email
    ):
        committer = info.committer_name
        cemail = f" <{info.committer_email}>" if info.committer_email else ""
        parts.append(f"<b>Committer:</b> {committer}{cemail}")
    if info.author_time:
        parts.append(f"<b>Committed:</b> {_format_time(info.author_time)}")
    if info.sha:
        short = info.short_sha or info.sha[:7]
        parts.append(f"<b>SHA:</b> <code>{info.sha}</code> ({short})")
    if info.parents:
        parents = ", ".join(p[:7] for p in info.parents)
        parts.append(f"<b>Parents:</b> {parents}")
    else:
        parts.append("<b>Parents:</b> (root commit)")
    return "<br/>".join(parts)


def _format_time(unix_ts: int) -> str:
    """Render a unix timestamp as ``YYYY-MM-DD HH:MM:SS`` in UTC.

    We don't need user-local time here — git itself stores and prints
    commit times in the committer's zone, but for the side panel the
    user just needs a recognisable, unambiguous string.
    """
    import datetime

    try:
        dt = datetime.datetime.fromtimestamp(int(unix_ts), tz=datetime.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return str(unix_ts)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
