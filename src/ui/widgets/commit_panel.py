"""Right-panel commit-input view: WIP / staging area for the next commit.

Shown when the user clicks the WIP node in the graph (or otherwise
selects the WIP state). Structure (top-to-bottom):

* **Unstaged Files (N)** — collapsible list, with a green *Stage All
  Changes* button on the right of the header. Each row shows the
  file's status badge and path; hovering the row reveals a green
  *Stage File* button on the right.
* **Staged Files (N)** — collapsible list. Each row shows the badge
  and path (no stage button — the row is already staged). The user
  can unstage a file by clicking its row (routed to
  :meth:`MainViewModel.unstage_file`).
* **Commit block** — sticky at the bottom:
    * Commit Summary (single-line ``QLineEdit``)
    * Description (multi-line ``QPlainTextEdit``)
    * Green *Commit Changes to (N) File(s)* button, enabled once at
      least one of the two fields is non-empty **and** there is at
      least one staged file.

The widget is bound to :class:`MainViewModel` for verbs
(``stage_file`` / ``unstage_file`` / ``stage_all_unstaged`` /
``commit_changes``) and to :class:`CommitPanelViewModel` for state
(staged set, commit message, file list). It is a passive view: it
never holds Git state and never calls ``pygit2`` directly.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.models import FileChange, FileStatus
from src.viewmodels.commit_panel_viewmodel import CommitPanelViewModel
from src.viewmodels.main_viewmodel import MainViewModel

# Short status letter + colour shared with :class:`CommitDetailPanel`
# so the visual vocabulary is consistent across the two right-panel
# views.
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

_STATUS_TOOLTIP: dict[FileStatus, str] = {
    FileStatus.NEW: "Added (new file)",
    FileStatus.MODIFIED: "Modified",
    FileStatus.DELETED: "Deleted",
    FileStatus.RENAMED: "Renamed",
    FileStatus.COPIED: "Copied",
    FileStatus.UNTRACKED: "Untracked",
    FileStatus.TYPE_CHANGED: "Type changed",
    FileStatus.CONFLICTED: "Conflicted",
    FileStatus.IGNORED: "Ignored",
}

_GREEN = "#3FB950"
_GREEN_HOVER = "#46C75A"
_GREEN_PRESSED = "#2F8B3B"
_RED = "#E8685A"
_RED_HOVER = "#ED7A6E"
_RED_PRESSED = "#C04D40"
_SELECTION_BG = "#264F78"


class CommitPanel(QWidget):
    """WIP / commit-input view bound to :class:`MainViewModel`."""

    def __init__(self, view_model: MainViewModel, parent=None) -> None:
        super().__init__(parent)
        self._main_vm = view_model
        self._vm: CommitPanelViewModel = view_model.commit_panel_view_model()

        self._build_ui()
        self._wire_signals()
        self._vm.set_repository(view_model.repository_manager())
        self._refresh_all()

    # ----- construction -----------------------------------------------

    def _build_ui(self) -> None:
        # --- Unstaged Files block ---
        self._unstaged_expander = QToolButton(self)
        self._unstaged_expander.setCheckable(True)
        self._unstaged_expander.setChecked(True)
        self._unstaged_expander.setStyleSheet(
            "QToolButton { border: none; background: transparent; "
            "font-weight: bold; padding: 4px 0; } "
            "QToolButton:hover { color: #D4D4D4; }",
        )
        self._unstaged_expander.setArrowType(Qt.ArrowType.DownArrow)
        self._unstaged_expander.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
        )
        self._unstaged_expander.toggled.connect(self._on_unstaged_toggled)

        self._unstaged_header = QLabel("Unstaged Files (0)", self)
        self._unstaged_header.setStyleSheet(
            "font-weight: bold; padding: 4px 0; color: #D4D4D4;",
        )
        # The expander and the label are visually one row; we install
        # the expander as the actual clickable target but keep the
        # label updated with the current count.
        self._unstaged_header.setVisible(False)

        self._stage_all_button = QPushButton("Stage All Changes", self)
        self._stage_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stage_all_button.setStyleSheet(
            f"QPushButton {{ background-color: { _GREEN_PRESSED }; "
            f"color: white; border: none; border-radius: 3px; padding: 4px 10px; "
            f"font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: { _GREEN_HOVER }; }} "
            f"QPushButton:pressed {{ background-color: { _GREEN_PRESSED }; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._stage_all_button.setEnabled(False)
        self._stage_all_button.clicked.connect(self._on_stage_all_clicked)

        unstaged_header_row = QHBoxLayout()
        unstaged_header_row.setContentsMargins(0, 0, 0, 0)
        unstaged_header_row.setSpacing(8)
        # Use the QToolButton as the visible "Unstaged Files" header
        # (clickable, with the arrow indicator). Push the Stage All
        # button to the right.
        self._unstaged_expander.setSizePolicy(
            self._unstaged_expander.sizePolicy().horizontalPolicy(),
            self._unstaged_expander.sizePolicy().verticalPolicy(),
        )
        unstaged_header_row.addWidget(self._unstaged_expander, stretch=1)
        unstaged_header_row.addWidget(self._stage_all_button)

        self._unstaged_list = FileListWidget(staged=False, parent=self)
        self._unstaged_list.itemClicked.connect(self._on_unstaged_item_clicked)
        self._unstaged_list.stage_file_requested.connect(self._on_stage_file)

        # --- Staged Files block ---
        self._staged_expander = QToolButton(self)
        self._staged_expander.setCheckable(True)
        self._staged_expander.setChecked(True)
        self._staged_expander.setStyleSheet(self._unstaged_expander.styleSheet())
        self._staged_expander.setArrowType(Qt.ArrowType.DownArrow)
        self._staged_expander.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon,
        )
        self._staged_expander.toggled.connect(self._on_staged_toggled)

        self._unstage_all_button = QPushButton("Unstage All Changes", self)
        self._unstage_all_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._unstage_all_button.setStyleSheet(
            f"QPushButton {{ background-color: { _RED }; "
            f"color: white; border: none; border-radius: 3px; padding: 4px 10px; "
            f"font-weight: 600; }} "
            f"QPushButton:hover {{ background-color: { _RED_HOVER }; }} "
            f"QPushButton:pressed {{ background-color: { _RED_PRESSED }; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._unstage_all_button.setEnabled(False)
        self._unstage_all_button.clicked.connect(self._on_unstage_all_clicked)

        staged_header_row = QHBoxLayout()
        staged_header_row.setContentsMargins(0, 0, 0, 0)
        staged_header_row.setSpacing(8)
        self._staged_expander.setSizePolicy(
            self._staged_expander.sizePolicy().horizontalPolicy(),
            self._staged_expander.sizePolicy().verticalPolicy(),
        )
        staged_header_row.addWidget(self._staged_expander, stretch=1)
        staged_header_row.addWidget(self._unstage_all_button)

        self._staged_list = FileListWidget(staged=True, parent=self)
        self._staged_list.itemClicked.connect(self._on_staged_item_clicked)
        self._staged_list.stage_file_requested.connect(self._on_unstage_file)

        # --- Commit block (sticky at the bottom) ---
        self._summary = QLineEdit(self)
        self._summary.setPlaceholderText("Commit Summary")
        self._summary.setClearButtonEnabled(True)

        self._description = QPlainTextEdit(self)
        self._description.setPlaceholderText("Description")
        self._description.setTabChangesFocus(True)
        self._description.setMaximumHeight(120)

        self._commit_button = QPushButton("Commit Changes to 0 File", self)
        self._commit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._commit_button.setStyleSheet(
            f"QPushButton {{ background-color: { _GREEN }; color: white; "
            f"border: none; border-radius: 3px; padding: 8px 14px; "
            f"font-weight: 600; font-size: 12px; }} "
            f"QPushButton:hover {{ background-color: { _GREEN_HOVER }; }} "
            f"QPushButton:pressed {{ background-color: { _GREEN_PRESSED }; }} "
            f"QPushButton:disabled {{ background-color: #2A2A2A; color: #6A6A6A; }}",
        )
        self._commit_button.setEnabled(False)
        self._commit_button.clicked.connect(self._on_commit_clicked)

        commit_layout = QVBoxLayout()
        commit_layout.setContentsMargins(0, 0, 0, 0)
        commit_layout.setSpacing(4)
        commit_layout.addWidget(self._summary)
        commit_layout.addWidget(self._description, stretch=1)
        commit_layout.addWidget(self._commit_button)

        commit_container = QWidget(self)
        commit_container.setObjectName("commit-block")
        commit_container.setStyleSheet(
            "QWidget#commit-block { background-color: #252526; "
            "border-top: 1px solid #3F3F46; }",
        )
        commit_container.setLayout(commit_layout)
        self._commit_container = commit_container

        # --- Outer layout: scrollable lists on top, commit block pinned ---
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        outer.addLayout(unstaged_header_row)
        outer.addWidget(self._unstaged_list, stretch=1)
        outer.addLayout(staged_header_row)
        outer.addWidget(self._staged_list, stretch=1)
        outer.addSpacing(4)
        outer.addWidget(commit_container)

    def _wire_signals(self) -> None:
        self._summary.textChanged.connect(self._on_summary_changed)
        self._description.textChanged.connect(self._on_description_changed)
        self._vm.file_changes_changed.connect(self._refresh_file_lists)
        self._vm.staged_files_changed.connect(self._refresh_file_lists)
        self._vm.commit_summary_changed.connect(self._on_summary_from_vm)
        self._vm.commit_description_changed.connect(self._on_description_from_vm)
        self._vm.selected_file_changed.connect(self._on_selected_file_changed)
        # Commit-button enabled state depends on the *combined* input
        # plus the staged set. We refresh the button whenever either
        # input field changes, in addition to the file-list refreshes
        # above. The summary/description field handlers call
        # ``_refresh_commit_button`` directly so we don't need a
        # separate signal connection here — the file-list refreshes
        # already call it.

    # ----- VM -> UI ---------------------------------------------------

    def _refresh_all(self) -> None:
        """Populate every section from the current VM state."""
        self._refresh_file_lists()
        self._refresh_commit_button()

    def _refresh_file_lists(self) -> None:
        """Rebuild the Unstaged / Staged lists from the VM."""
        unstaged = self._vm.unstaged_files()
        staged = self._vm.staged_files_detailed()

        # Block signals during the rebuild so itemChanged feedback
        # doesn't trigger stray stage/unstage calls.
        self._unstaged_list.blockSignals(True)
        self._staged_list.blockSignals(True)
        try:
            self._unstaged_list.populate(unstaged)
            self._staged_list.populate(staged)
        finally:
            self._unstaged_list.blockSignals(False)
            self._staged_list.blockSignals(False)

        n_unstaged = len(unstaged)
        n_staged = len(staged)
        self._unstaged_expander.setText(f"  Unstaged Files ({n_unstaged})")
        self._staged_expander.setText(f"  Staged Files ({n_staged})")
        self._unstaged_header.setText(f"Unstaged Files ({n_unstaged})")
        self._stage_all_button.setEnabled(n_unstaged > 0)
        self._unstage_all_button.setEnabled(n_staged > 0)
        self._highlight_selected_file()
        self._refresh_commit_button()

    def _on_unstaged_toggled(self, checked: bool) -> None:
        self._unstaged_list.setVisible(checked)
        self._unstaged_expander.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow,
        )

    def _on_staged_toggled(self, checked: bool) -> None:
        self._staged_list.setVisible(checked)
        self._staged_expander.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow,
        )

    def _on_summary_changed(self, text: str) -> None:
        self._vm.set_commit_summary(text)
        self._refresh_commit_button()

    def _on_description_changed(self) -> None:
        self._vm.set_commit_description(self._description.toPlainText())
        self._refresh_commit_button()

    def _on_summary_from_vm(self, text: str) -> None:
        if self._summary.text() == text:
            return
        self._summary.blockSignals(True)
        try:
            self._summary.setText(text)
        finally:
            self._summary.blockSignals(False)

    def _on_description_from_vm(self, text: str) -> None:
        if self._description.toPlainText() == text:
            return
        self._description.blockSignals(True)
        try:
            self._description.setPlainText(text)
        finally:
            self._description.blockSignals(False)

    # ----- UI -> VM ---------------------------------------------------

    def _on_stage_all_clicked(self) -> None:
        self._main_vm.stage_all_unstaged()

    def _on_stage_file(self, path: str) -> None:
        self._main_vm.stage_file(path)
        self._vm.select_file(None)

    def _on_unstage_file(self, path: str) -> None:
        self._main_vm.unstage_file(path)
        self._vm.select_file(None)

    def _on_unstaged_item_clicked(self, item: QListWidgetItem) -> None:
        """Click on a row in the Unstaged list = show diff for that file.

        Clicking the same file again deselects it and returns the
        graph view. The *Stage File* hover button is the dedicated
        way to stage — this click only previews the diff.
        """
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        current = self._vm.selected_file()
        if current == path:
            self._vm.select_file(None)
            self._unstaged_list.clearSelection()
            self._staged_list.clearSelection()
        else:
            self._vm.select_file(path)

    def _on_staged_item_clicked(self, item: QListWidgetItem) -> None:
        """Click on a row in the Staged list = show diff for that file.

        Clicking the same file again deselects it and returns the
        graph view. The *Unstage File* hover button is the dedicated
        way to unstage — this click only previews the diff.
        """
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        current = self._vm.selected_file()
        if current == path:
            self._vm.select_file(None)
            self._unstaged_list.clearSelection()
            self._staged_list.clearSelection()
        else:
            self._vm.select_file(path, staged=True)

    def _on_unstage_all_clicked(self) -> None:
        self._main_vm.unstage_all_staged()

    def _on_selected_file_changed(self, path: str | None) -> None:
        self._highlight_selected_file()

    def _highlight_selected_file(self) -> None:
        selected = self._vm.selected_file()
        for list_widget in (self._unstaged_list, self._staged_list):
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item is None:
                    continue
                if selected is not None and item.data(Qt.ItemDataRole.UserRole) == selected:
                    item.setBackground(QBrush(QColor(_SELECTION_BG)))
                else:
                    item.setBackground(QBrush())

    def _on_commit_clicked(self) -> None:
        message = self._vm.combined_commit_message()
        if not message.strip():
            return
        if not self._vm.staged_files():
            return
        self._main_vm.commit_changes(message)

    def _refresh_commit_button(self) -> None:
        staged_count = len(self._vm.staged_files())
        has_input = self._vm.has_commit_input()
        label = (
            f"Commit Changes to {staged_count} File"
            if staged_count == 1
            else f"Commit Changes to {staged_count} Files"
        )
        self._commit_button.setText(label)
        self._commit_button.setEnabled(has_input and staged_count > 0)


# ---------------------------------------------------------------------------
# File list widget with hover-to-reveal action button
# ---------------------------------------------------------------------------


class FileListWidget(QListWidget):
    """A ``QListWidget`` that supports per-item widgets.

    For ``staged=False`` (the Unstaged list) each row widget manages
    its own *Stage File* button via native ``enterEvent`` / ``leaveEvent``
    — no signal forwarding, no coordinate tracking.  For ``staged=True``
    the button is never created.

    When the file list exceeds ``MAX_ITEMS`` rows, population is limited
    to the first ``MAX_ITEMS`` entries and a placeholder item is
    appended.  For very large lists (e.g. 20 000+ files) the items are
    added in batches of ``CHUNK`` via :meth:`_populate_chunk` so the
    event loop stays responsive.
    """

    MAX_ITEMS = 1000
    CHUNK = 400

    stage_file_requested = Signal(str)
    """Emitted when the user clicks the *Stage File* hover button on a row."""

    populate_finished = Signal()
    """Emitted after the last chunk of :meth:`populate` has been added."""

    def __init__(self, *, staged: bool, parent=None) -> None:
        super().__init__(parent)
        self._staged = staged
        self.setMouseTracking(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setAlternatingRowColors(True)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        # Internal state for chunked population.
        self._pending_changes: list[FileChange] = []
        self._pending_index: int = 0
        self._populate_generation: int = 0

    def populate(self, changes: list[FileChange]) -> None:
        """Rebuild the list to match the supplied *changes*.

        For very large change sets the items are created in batches
        of :attr:`CHUNK` so the Qt event loop can process paint and
        input events between chunks.
        """
        self.clear()
        self._populate_generation += 1  # invalidate any pending chunk timer
        self._pending_changes = []
        if not changes:
            return
        self._pending_changes = changes
        self._pending_index = 0
        gen = self._populate_generation
        QTimer.singleShot(0, lambda g=gen: self._populate_chunk(g))

    def _populate_chunk(self, generation: int) -> None:
        if self._populate_generation != generation:
            return  # another populate() call invalidated this run
        # Guard against the C++ object being deleted between timer ticks.
        import shiboken6
        if not shiboken6.isValid(self):
            return
        changes = self._pending_changes
        start = self._pending_index
        end = min(start + self.CHUNK, len(changes), self.MAX_ITEMS)
        for i in range(start, end):
            change = changes[i]
            item = QListWidgetItem(self)
            item.setData(Qt.ItemDataRole.UserRole, change.path)
            self.addItem(item)
            row_widget = _RowWidget(change, staged=self._staged, parent=self)
            row_widget.stage_file_requested.connect(self.stage_file_requested)
            item.setSizeHint(row_widget.sizeHint())
            self.setItemWidget(item, row_widget)
        self._pending_index = end
        remaining = len(changes)
        if self.MAX_ITEMS < remaining:
            if end >= self.MAX_ITEMS:
                self._append_truncation_item(remaining)
                self._pending_changes = []
                self.populate_finished.emit()
                return
        if end < remaining:
            QTimer.singleShot(0, lambda g=generation: self._populate_chunk(g))
        else:
            self._pending_changes = []
            self.populate_finished.emit()

    def _append_truncation_item(self, total: int) -> None:
        shown = self.MAX_ITEMS
        item = QListWidgetItem(self)
        item.setText(f"  … showing {shown} of {total} files.  "
                      "Commit or stash to shrink the list.")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(QColor("#8B8B8B"))
        self.addItem(item)


class _RowWidget(QWidget):
    """Single-row widget inside :class:`FileListWidget`.

    Renders the status badge + path; for the unstaged variant a
    green *Stage File* button is positioned on the right and is
    always visible (it's small and the row is short, so the visual
    noise is minimal). Clicking the button emits
    :attr:`stage_file_requested` with the file path.
    """

    stage_file_requested = Signal(str)

    def __init__(self, change: FileChange, *, staged: bool, parent=None) -> None:
        super().__init__(parent)
        self._change = change
        self._staged = staged
        self._build_ui()

    def _build_ui(self) -> None:
        badge, color_hex = _STATUS_BADGE.get(self._change.status, ("?", "#8B8B8B"))

        self._badge = QLabel(f"[{badge}]", self)
        self._badge.setStyleSheet(
            f"color: {color_hex}; font-weight: bold; padding: 0 6px 0 2px;",
        )
        self._badge.setFixedWidth(28)
        tip = _STATUS_TOOLTIP.get(self._change.status, "")
        if tip:
            self._badge.setToolTip(tip)

        self._path = QLabel(self._change.path, self)
        self._path.setStyleSheet("color: #D4D4D4;")
        self._path.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(4)
        layout.addWidget(self._badge)
        layout.addWidget(self._path, stretch=1)

        if not self._staged:
            self._stage_button = QPushButton("Stage File", self)
            self._stage_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._stage_button.setFixedHeight(16)
            self._stage_button.setStyleSheet(
                f"QPushButton {{ background-color: { _GREEN }; color: white; "
                f"border: none; border-radius: 3px; padding: 0px 8px 0px 8px; "
                f"font-size: 10px; font-weight: 600; }} "
                f"QPushButton:hover {{ background-color: { _GREEN_HOVER }; }} "
                f"QPushButton:pressed {{ background-color: { _GREEN_PRESSED }; }}",
            )
            self._stage_button.clicked.connect(
                lambda: self.stage_file_requested.emit(self._change.path),
            )
            self._stage_button.setVisible(False)
            layout.addWidget(self._stage_button, alignment=Qt.AlignmentFlag.AlignRight)
        else:
            self._stage_button = QPushButton("Unstage File", self)
            self._stage_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._stage_button.setFixedHeight(16)
            self._stage_button.setStyleSheet(
                f"QPushButton {{ background-color: { _RED }; color: white; "
                f"border: none; border-radius: 3px; padding: 0px 8px 0px 8px; "
                f"font-size: 10px; font-weight: 600; }} "
                f"QPushButton:hover {{ background-color: { _RED_HOVER }; }} "
                f"QPushButton:pressed {{ background-color: { _RED_PRESSED }; }}",
            )
            self._stage_button.clicked.connect(
                lambda: self.stage_file_requested.emit(self._change.path),
            )
            self._stage_button.setVisible(False)
            layout.addWidget(self._stage_button, alignment=Qt.AlignmentFlag.AlignRight)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Show the Stage File button when the mouse enters the row."""
        if hasattr(self, "_stage_button"):
            self._stage_button.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Hide the Stage File button when the mouse leaves the row."""
        if hasattr(self, "_stage_button"):
            self._stage_button.setVisible(False)
        super().leaveEvent(event)

    def set_stage_button_visible(self, visible: bool) -> None:
        """Show or hide the Stage File button on this row."""
        if hasattr(self, "_stage_button"):
            self._stage_button.setVisible(visible)


__all__ = ["CommitPanel"]
