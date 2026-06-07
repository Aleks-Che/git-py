"""Model and delegate for the file-list view (Unstaged / Staged).

Replaces ``QListWidget`` + per-row ``QWidget`` with a pure data model
and QPainter-based delegate so the view only pays for visible rows.
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from src.core.models import FileChange, FileStatus

# ---------------------------------------------------------------------------
# Custom model roles
# ---------------------------------------------------------------------------

FileChangeRole = Qt.ItemDataRole.UserRole + 1
FileStatusRole = Qt.ItemDataRole.UserRole + 2

# ---------------------------------------------------------------------------
# Status badge colours — shared with CommitDetailPanel
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class FileListModel(QAbstractListModel):
    """Thin list model holding :class:`FileChange` items.

    The model is intentionally lightweight — no QObject per row, no
    widget allocation.  ``set_changes()`` resets the entire list via
    ``beginResetModel`` / ``endResetModel`` so the view reacts once.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._changes: list[FileChange] = []

    # -- QAbstractListModel interface -------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        if parent is not None and parent.isValid():
            return 0
        return len(self._changes)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._changes):
            return None
        change = self._changes[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return change.path
        if role == FileChangeRole:
            return change
        if role == FileStatusRole:
            return change.status
        return None

    # -- Public helpers ---------------------------------------------------

    def set_changes(self, changes: list[FileChange]) -> None:
        self.beginResetModel()
        self._changes = list(changes)
        self.endResetModel()

    def change_at(self, row: int) -> FileChange | None:
        if 0 <= row < len(self._changes):
            return self._changes[row]
        return None

    def count(self) -> int:
        return len(self._changes)


# ---------------------------------------------------------------------------
# Delegate
# ---------------------------------------------------------------------------


class FileListDelegate(QStyledItemDelegate):
    """Paint a single file row: badge | path | stage/unstage button.

    Everything is drawn with ``QPainter`` — zero widgets per row.
    Hover/click on the button area is detected via ``editorEvent`` and
    the view's ``mouseMoveEvent`` forwarding.
    """

    ROW_HEIGHT = 28
    BADGE_SIZE = 16
    BUTTON_SIZE = 20
    MARGIN = 4

    stage_file_requested = Signal(str)
    """Emitted when the user clicks the painted stage/unstage button."""

    def __init__(self, staged: bool, parent=None) -> None:
        super().__init__(parent)
        self._staged = staged
        self._hovered_index: QModelIndex | None = None
        self._button_row: int | None = None

    # -- hover helpers (called from the owning QListView) ------------------

    def set_hovered_index(self, index: QModelIndex) -> None:
        """Remember which item the mouse is currently over."""
        self._hovered_index = QModelIndex(index) if index.isValid() else None

    def set_button_row(self, row: int | None) -> None:
        """Remember which row's button area the mouse is over."""
        if self._button_row != row:
            self._button_row = row

    # -- QStyledItemDelegate interface ------------------------------------

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex,
    ) -> QSize:
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex,
    ) -> None:
        change = index.data(FileChangeRole)
        if not change:
            return

        painter.save()
        rect = option.rect

        # -- background ---------------------------------------------------
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(rect, QColor("#264F78"))
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.fillRect(rect, QColor("#2A2D2E"))
        else:
            painter.fillRect(rect, QColor("#1E1E1E"))

        x = rect.left() + self.MARGIN
        badge_y = rect.top() + (self.ROW_HEIGHT - self.BADGE_SIZE) // 2

        # -- status badge -------------------------------------------------
        letter, color_hex = _STATUS_BADGE.get(change.status, ("?", "#8B8B8B"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(color_hex)))
        badge_rect = QRect(x, badge_y, self.BADGE_SIZE, self.BADGE_SIZE)
        painter.drawRoundedRect(badge_rect, 3, 3)

        painter.setPen(QColor("white"))
        font = QFont("Segoe UI", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, letter)

        # -- file path ----------------------------------------------------
        x += self.BADGE_SIZE + self.MARGIN
        path_width = (
            rect.right() - x - self.MARGIN - self.BUTTON_SIZE - self.MARGIN
        )
        path_rect = QRect(x, rect.top(), max(path_width, 0), self.ROW_HEIGHT)

        painter.setPen(QColor("#D4D4D4"))
        path_font = QFont("Segoe UI", 9)
        painter.setFont(path_font)
        fm = QFontMetrics(path_font)
        elided = fm.elidedText(change.path, Qt.TextElideMode.ElideRight, path_rect.width())
        painter.drawText(
            path_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided,
        )

        # -- stage / unstage button (painted) -----------------------------
        btn_x = rect.right() - self.MARGIN - self.BUTTON_SIZE
        btn_y = rect.top() + (self.ROW_HEIGHT - self.BUTTON_SIZE) // 2
        btn_rect = QRect(btn_x, btn_y, self.BUTTON_SIZE, self.BUTTON_SIZE)

        is_button_hovered = (
            self._hovered_index is not None
            and self._hovered_index.row() == index.row()
            and self._button_row == index.row()
        )

        if self._staged:
            btn_color = QColor("#ED7A6E") if is_button_hovered else QColor("#E8685A")
        else:
            btn_color = QColor("#46C75A") if is_button_hovered else QColor("#3FB950")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(btn_color))
        painter.drawRoundedRect(btn_rect, 3, 3)

        painter.setPen(QColor("white"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        symbol = "−" if self._staged else "+"
        painter.drawText(btn_rect, Qt.AlignmentFlag.AlignCenter, symbol)

        painter.restore()

    def editorEvent(  # noqa: N802
        self, event, model, option: QStyleOptionViewItem, index: QModelIndex,
    ) -> bool:
        if event.type() in (
            event.Type.MouseButtonPress,
            event.Type.MouseButtonRelease,
        ):
            btn_x = option.rect.right() - self.MARGIN - self.BUTTON_SIZE
            btn_y = option.rect.top() + (self.ROW_HEIGHT - self.BUTTON_SIZE) // 2
            btn_rect = QRect(btn_x, btn_y, self.BUTTON_SIZE, self.BUTTON_SIZE)

            if btn_rect.contains(event.pos()):
                if event.type() == event.Type.MouseButtonRelease:
                    change = index.data(FileChangeRole)
                    if change:
                        self.stage_file_requested.emit(change.path)
                return True

        return super().editorEvent(event, model, option, index)
