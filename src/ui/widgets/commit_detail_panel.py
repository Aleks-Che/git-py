"""Right-panel commit-detail view: read-only summary for a selected commit.

Shown when the user clicks a real (non-WIP) commit in the graph.
Structure (top-to-bottom):

* **Message** — the commit subject on the first line; the body in a
  monospace block under it.
* **Info** — author, committer, time, full SHA, parents.
* **Changed files** — one row per file the commit touched, with the
  usual ``M`` / ``A`` / ``D`` / ``R`` / ``C`` / ``T`` badge.

Clicking a file in the **Changed Files** list emits
:attr:`selected_file_changed` and :attr:`diff_ready` so the
:class:`MainWindow` can swap the graph for a diff view in place.
Clicking the same file again deselects it (toggle behaviour, mirroring
the WIP panel).

The widget is bound to :class:`MainViewModel` for the commit's
``CommitInfo`` and to :class:`RepositoryManager` (through the VM) for
the list of changed files and the per-file diff.
"""
from __future__ import annotations

import pygit2
from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from src.core.exceptions import GitError
from src.core.models import CommitInfo, FileChange
from src.ui.widgets.file_list_model import (
    DEFAULT_PATH_TEXT_COLOR,
    PATH_TEXT_COLOR,
    STATUS_BADGE,
    STATUS_TOOLTIP,
)
from src.utils.avatar import make_avatar_pixmap
from src.viewmodels.main_viewmodel import MainViewModel

# When generating the "full document" variant of a diff we want
# enough context on either side of every change to span the entire
# file. ``2**31 - 1`` is the maximum value libgit2 will accept for
# ``context_lines`` and is large enough for any realistic file size.
_FULL_DOCUMENT_CONTEXT_LINES = 2**31 - 1

# Status badge + path colours are imported from :mod:`file_list_model`
# so the commit-detail view and the WIP panel stay visually identical.

# Selection background colour for the chosen file — matches the WIP
# panel so the visual language is identical on both sides.
_SELECTION_BG = "#264F78"

# Default background colour for an unselected row. Matches the WIP
# panel's :class:`FileListDelegate` so the two panels look identical.
_ROW_BG = "#1E1E1E"

# Custom role carrying the :class:`FileChange` payload on each row.
# The :class:`_FileRowDelegate` reads this to paint badge + path.
_FILE_CHANGE_ROLE = Qt.ItemDataRole.UserRole + 1


# ---------------------------------------------------------------------------
# Per-row painter for the changed-files list
# ---------------------------------------------------------------------------


class _FileRowDelegate(QStyledItemDelegate):
    """Paint a single changed-file row: badge | path.

    Mirrors :class:`FileListDelegate` so the WIP panel and the
    commit-detail panel use the same two-tone layout (badge in the
    strong status colour, path in the lighter / neutral shade).
    ``QListWidget`` does not parse HTML in ``setText`` by default, so
    we paint the row ourselves instead of stuffing rich text into the
    item.
    """

    ROW_HEIGHT = 24
    BADGE_SIZE = 16
    MARGIN = 4

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: Qt.ModelIndex,
    ) -> QSize:
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(  # noqa: N802
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: Qt.ModelIndex,
    ) -> None:
        change = index.data(_FILE_CHANGE_ROLE)
        if change is None:
            super().paint(painter, option, index)
            return

        painter.save()
        rect = option.rect

        # -- background ---------------------------------------------------
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor(_SELECTION_BG))
        else:
            painter.fillRect(rect, QColor(_ROW_BG))

        # -- status badge -------------------------------------------------
        badge, badge_color = STATUS_BADGE.get(change.status, ("?", "#8B8B8B"))
        x = rect.left() + self.MARGIN
        badge_y = rect.top() + (self.ROW_HEIGHT - self.BADGE_SIZE) // 2
        badge_rect = QRect(x, badge_y, self.BADGE_SIZE, self.BADGE_SIZE)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(badge_color)))
        painter.drawRoundedRect(badge_rect, 3, 3)

        painter.setPen(QColor("white"))
        font = QFont("Segoe UI", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge)

        # -- file path ----------------------------------------------------
        x += self.BADGE_SIZE + self.MARGIN
        path_color = PATH_TEXT_COLOR.get(change.status, DEFAULT_PATH_TEXT_COLOR)
        path_width = rect.right() - x - self.MARGIN
        path_rect = QRect(x, rect.top(), max(path_width, 0), self.ROW_HEIGHT)

        painter.setPen(QColor(path_color))
        path_font = QFont("Segoe UI", 9)
        painter.setFont(path_font)
        fm = QFontMetrics(path_font)
        elided = fm.elidedText(change.path, Qt.TextElideMode.ElideRight, path_rect.width())
        painter.drawText(
            path_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided,
        )

        painter.restore()


class CommitDetailPanel(QWidget):
    """Read-only view of a single commit, bound to :class:`MainViewModel`."""

    selected_file_changed = Signal(object)
    """Emitted with the new selected path (or ``None``)."""

    diff_ready = Signal(str)
    """Emitted with the unified-diff text for the selected file."""

    diff_pair_ready = Signal(str, str)
    """Emitted with the (changes-only, full-document) diff pair.

    Mirrors the WIP-side signal so the :class:`MainWindow` can hand
    both variants to :class:`DiffViewWidget` at once. See
    :attr:`CommitPanelViewModel.diff_pair_ready` for the full
    contract."""

    error_occurred = Signal(str)

    def __init__(self, main_view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._main_vm = main_view_model
        self._selected_file: str | None = None
        self._current_sha: str | None = None

        self._build_ui()
        self._render_empty()

    # ----- construction -----------------------------------------------

    def _build_ui(self) -> None:
        # --- message (subject) — always visible ---
        self._message = QLabel(self)
        self._message.setWordWrap(True)
        self._message.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        self._message.setStyleSheet(
            "font-size: 14px; font-weight: 600; padding: 4px 0;",
        )

        # --- body (description) — scrollable when too long ---
        self._body = QLabel(self)
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignmentFlag.AlignTop)
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

        body_container = QWidget()
        body_layout = QVBoxLayout(body_container)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.addWidget(self._body)

        self._body_scroll = QScrollArea(self)
        self._body_scroll.setWidgetResizable(True)
        self._body_scroll.setWidget(body_container)
        self._body_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        self._body_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._body_scroll.setVisible(False)
        self._body_scroll.setStyleSheet(
            "QScrollArea { background: transparent; } "
            "QScrollArea > QWidget > QWidget { background: transparent; } "
            "QScrollBar:vertical { background: #2A2A2A; width: 10px; } "
            "QScrollBar::handle:vertical { background: #555; border-radius: 4px; } "
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }",
        )

        # --- info block — always visible ---
        self._info = QLabel(self)
        self._info.setWordWrap(True)
        self._info.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard,
        )
        self._info.setStyleSheet("color: #8B8B8B; font-size: 11px; padding: 0;")
        self._info.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        # --- author avatar badge (square, height = 2 info-text lines) ---
        # Sits to the left of the info block. The avatar is rendered by
        # the same identicon helper that powers the graph-node chips so
        # the same author is rendered identically everywhere — only
        # the shape/clip differs (square here, circular inside the
        # graph node circle). The actual badge size is computed lazily
        # in :meth:`_set_avatar_for` because the info stylesheet
        # (font-size: 11px) only resolves after the panel is shown —
        # measuring in ``_build_ui`` would lock the size to the
        # pre-show default font.
        self._avatar_size = 0
        self._avatar_label = QLabel(self)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._avatar_label.setVisible(False)
        # Cached per-call so repeated renders of the same commit do
        # not regenerate the pixmap. Keyed by (seed, size, shape).
        self._avatar_cache: dict[tuple[str, int, str], object] = {}

        info_row = QWidget()
        info_row_layout = QHBoxLayout(info_row)
        info_row_layout.setContentsMargins(0, 0, 0, 0)
        # Tight spacing (5 px) keeps the avatar and the first text line
        # visually connected; the previous 8 px was wide enough to read
        # as a divider rather than a margin between badge and label.
        info_row_layout.setSpacing(5)
        info_row_layout.addWidget(
            self._avatar_label, 0, Qt.AlignmentFlag.AlignTop,
        )
        info_row_layout.addWidget(self._info, 1)

        # --- changed files ---
        self._files_header = QLabel("Changed Files (0)", self)
        self._files_header.setStyleSheet(
            "font-weight: bold; padding: 6px 0 2px 0; color: #D4D4D4;",
        )

        self._files = QListWidget(self)
        self._files.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._files.setUniformItemSizes(True)
        self._files.setAlternatingRowColors(False)
        self._files.setItemDelegate(_FileRowDelegate(self._files))
        self._files.setStyleSheet(
            f"QListWidget::item:selected {{ background: {_SELECTION_BG}; }}",
        )
        self._files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._files.customContextMenuRequested.connect(self._on_file_context_menu)
        self._files.itemClicked.connect(self._on_files_item_clicked)

        # --- splitter: 60% message+info / 40% files ---
        top_container = QWidget()
        top_container.setObjectName("commit-detail-top")
        top_layout = QVBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(4)
        top_layout.addWidget(self._message)
        top_layout.addWidget(self._body_scroll, stretch=1)
        top_layout.addSpacing(6)
        top_layout.addWidget(info_row)
        top_layout.addStretch()

        files_container = QWidget()
        files_container.setObjectName("commit-detail-files")
        files_layout = QVBoxLayout(files_container)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(2)
        files_layout.addWidget(self._files_header)
        files_layout.addWidget(self._files, stretch=1)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.addWidget(top_container)
        self._splitter.addWidget(files_container)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setChildrenCollapsible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        layout.addWidget(self._splitter, stretch=1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        """Enforce 60/40 split on first resize."""
        super().resizeEvent(event)
        h = self._splitter.height()
        if h > 0 and self._splitter.sizes()[0] == 0:
            self._splitter.setSizes([int(h * 0.6), int(h * 0.4)])

    # ----- public API --------------------------------------------------

    def selected_file(self) -> str | None:
        """Return the path of the currently selected file, or ``None``."""
        return self._selected_file

    def select_file(self, path: str | None) -> None:
        """Set (or clear) the file whose diff should be displayed.

        Emits :attr:`selected_file_changed` and
        :attr:`diff_ready`. ``path=None`` clears both signals so the
        MainWindow switches back to the graph.
        """
        self._selected_file = path
        self._highlight_selected_file()
        if path is None:
            self._files.clearSelection()
        self.selected_file_changed.emit(path)
        if path is None or self._current_sha is None:
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")
            return
        self._compute_and_emit_diff(self._current_sha, path)

    def show_commit(self, sha: str) -> None:
        """Populate the panel for the commit at ``sha``.

        Any previously selected file is cleared: each commit has its
        own file list, and carrying the selection across would
        highlight a path that no longer exists in the new commit.
        """
        self._current_sha = sha
        self._selected_file = None
        self._highlight_selected_file()
        self.selected_file_changed.emit(None)
        self.diff_ready.emit("")
        self.diff_pair_ready.emit("", "")

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
        self._current_sha = None
        self._selected_file = None
        self._highlight_selected_file()
        self.selected_file_changed.emit(None)
        self.diff_ready.emit("")
        self.diff_pair_ready.emit("", "")
        self._render_empty()

    # ----- rendering ---------------------------------------------------

    def _render_empty(self) -> None:
        self._message.setText("Select a commit")
        self._message.setStyleSheet(
            "color: #8B8B8B; font-style: italic; padding: 4px 0;",
        )
        self._body.setVisible(False)
        self._body.clear()
        self._body_scroll.verticalScrollBar().setValue(0)
        self._body_scroll.setVisible(False)
        self._info.clear()
        self._avatar_label.clear()
        self._avatar_label.setVisible(False)
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
            self._body_scroll.verticalScrollBar().setValue(0)
            self._body_scroll.setVisible(True)
        else:
            self._body.clear()
            self._body.setVisible(False)
            self._body_scroll.verticalScrollBar().setValue(0)
            self._body_scroll.setVisible(False)

        self._info.setText(_format_info(info))
        self._set_avatar_for(info)

        # File list.
        self._files.clear()
        for change in changes:
            self._append_change_item(change)
        self._files_header.setText(
            f"Changed Files ({len(changes)})"
            if len(changes) != 1
            else "Changed Files (1)",
        )

    def _set_avatar_for(self, info: CommitInfo) -> None:
        """Render and display the author avatar next to the info block.

        The avatar uses the same identicon algorithm as the graph-node
        chips so the same author is recognisable across the UI. The
        ``square`` shape (rounded-corner square) is used here; the
        graph-node interior uses ``circle``. The pixmap is cached on
        ``self._avatar_cache`` so re-renders of the same commit skip
        the painter work.
        """
        seed = info.author_email or info.author_name or "?"
        # Re-measure the info font here — the stylesheet only resolves
        # after the panel is shown, and we want the badge height to
        # match the actual rendered line height (not the pre-show
        # default font).
        line_height = QFontMetrics(self._info.font()).height()
        size = line_height * 2
        if size != self._avatar_size:
            self._avatar_size = size
            self._avatar_label.setFixedSize(size, size)
        shape = "square"
        cache_key = (seed, size, shape)
        pix = self._avatar_cache.get(cache_key)
        if pix is None:
            pix = make_avatar_pixmap(seed, size, shape=shape)
            self._avatar_cache[cache_key] = pix
        self._avatar_label.setPixmap(pix)
        self._avatar_label.setVisible(True)

    def _append_change_item(self, change: FileChange) -> None:
        item = QListWidgetItem(self._files)
        item.setData(Qt.ItemDataRole.UserRole, change.path)
        # The :class:`_FileRowDelegate` reads the FileChange from a
        # dedicated role and paints badge + path with the shared
        # status palette, mirroring the WIP panel.
        item.setData(_FILE_CHANGE_ROLE, change)
        item.setText(change.path)
        tip = STATUS_TOOLTIP.get(change.status, "")
        if tip:
            item.setToolTip(tip)

    # ----- file selection (click to show diff in place) ---------------

    def _on_file_context_menu(self, position) -> None:
        item = self._files.itemAt(position)
        if item is None or self._current_sha is None:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return

        menu = self._build_file_context_menu(path)
        if menu is None:
            return
        menu.exec(self._files.viewport().mapToGlobal(position))

    def _build_file_context_menu(self, path: str) -> QMenu | None:
        """Return the right-click menu for ``path`` in the current commit.

        Always exposes *Copy Diff* (works for regular commits and stash
        entries alike). For stash entries it also exposes
        *Apply stashed file*. Returns ``None`` when no menu is
        appropriate (e.g. no commit selected).

        Factored out of :meth:`_on_file_context_menu` so tests can
        inspect the menu structure without going through
        :meth:`QMenu.exec`.
        """
        if self._current_sha is None:
            return None
        is_stash = self._main_vm.is_stash_sha(self._current_sha)

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background-color: #2D2D30; color: #D4D4D4; border: 1px solid #3F3F46; "
            "padding: 4px 0; } "
            "QMenu::item { padding: 6px 24px 6px 12px; } "
            "QMenu::item:selected { background-color: #094771; }",
        )

        copy_action = menu.addAction("Copy Diff")
        copy_action.triggered.connect(
            lambda checked=False, p=path: self._main_vm.copy_commit_file_diff(
                self._current_sha, p,
            ),
        )

        if is_stash:
            apply_action = menu.addAction("Apply stashed file")
            apply_action.triggered.connect(
                lambda checked=False, p=path: self._main_vm.apply_stash_file(
                    self._current_sha, p,
                ),
            )

        return menu

    def _on_files_item_clicked(self, item: QListWidgetItem) -> None:
        """Toggle the file selection for diff view.

        Clicking the same file again deselects it. The diff view is
        driven by :attr:`selected_file_changed` and
        :attr:`diff_ready` — same contract as the WIP panel.
        """
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if self._selected_file == path:
            self.select_file(None)
            self._files.clearSelection()
        else:
            self.select_file(path)

    def _highlight_selected_file(self) -> None:
        """Apply the ``:selected`` state to the chosen file row.

        Uses the native ``QListWidget`` selection mechanism (subject
        to the ``QListWidget::item:selected`` stylesheet above) so
        the platform's hover indicator never paints over the
        selected-file highlight.
        """
        selected = self._selected_file
        for i in range(self._files.count()):
            item = self._files.item(i)
            if item is None:
                continue
            is_match = (
                selected is not None
                and item.data(Qt.ItemDataRole.UserRole) == selected
            )
            item.setSelected(is_match)

    # ----- diff computation -------------------------------------------

    def _compute_and_emit_diff(self, sha: str, path: str) -> None:
        """Compute the commit-vs-parent diff for ``path`` and emit the
        diff signals.

        Emits :attr:`diff_ready` with the changes-only text and
        :attr:`diff_pair_ready` with the changes-only + full-document
        pair, so the centre-column :class:`DiffViewWidget` has both
        variants cached when the user toggles its toolbar.
        """
        repo = self._main_vm.repository_manager()
        if repo is None or not repo.is_open:
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")
            return
        try:
            changes_only = self._build_commit_diff_text(
                repo, sha, path, context_lines=3,
            )
            full_document = self._build_commit_diff_text(
                repo, sha, path, context_lines=_FULL_DOCUMENT_CONTEXT_LINES,
            )
        except GitError as exc:
            self.error_occurred.emit(f"Failed to diff {path!r}: {exc}")
            self.diff_ready.emit("")
            self.diff_pair_ready.emit("", "")
            return
        self.diff_ready.emit(changes_only)
        self.diff_pair_ready.emit(changes_only, full_document)

    def _build_commit_diff_text(
        self,
        repo,  # noqa: ANN001 - RepositoryManager
        sha: str,
        path: str,
        context_lines: int = 3,
    ) -> str:
        """Return the unified diff for ``path`` (commit tree vs its
        first parent's tree).

        For a root commit (no parents) the diff is against the empty
        tree, so every file it introduces is reported as
        ``new file``. We pick the patch for ``path`` out of a
        multi-file diff via :meth:`_extract_patch_for`.

        ``context_lines`` controls how many unchanged lines surround
        each change: ``3`` (the default) produces a compact diff for
        review; the *Full document* viewer mode passes a value large
        enough to cover the whole file.
        """
        try:
            obj = repo.repo.revparse_single(sha).peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError) as exc:
            raise GitError(f"Unknown revision: {sha!r}") from exc
        if obj.parent_ids:
            try:
                parent_tree = obj.parents[0].tree
            except (KeyError, ValueError):
                parent_tree = repo.repo.TreeBuilder().write()
        else:
            parent_tree = repo.repo.TreeBuilder().write()
        try:
            diff = repo.repo.diff(parent_tree, obj.tree, context_lines=context_lines)
        except (pygit2.GitError, KeyError, ValueError) as exc:
            raise GitError(f"Failed to diff {sha!r}: {exc}") from exc
        return self._extract_patch_for(diff, path)

    @staticmethod
    def _extract_patch_for(diff, path: str) -> str:  # noqa: ANN001 - pygit2.Diff
        """Return the patch text for ``path`` from a multi-file ``pygit2.Diff``."""
        pieces: list[str] = []
        for patch in diff:
            delta = patch.delta
            if (delta.new_file.path == path) or (delta.old_file.path == path):
                pieces.append(patch.text or "")
        return "".join(pieces)


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
