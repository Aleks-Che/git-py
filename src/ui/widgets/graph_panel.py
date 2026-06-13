"""Graph table widget: unified commit graph with header row and three columns.

The widget renders the entire commit graph as a single table:
row 0 is a fixed header (Branches | Graph | Commit Message) with
visible borders; rows 1+ are commit data without grid lines but
aligned to the same column boundaries.

Column dividers are draggable; their positions are persisted
per-repository.

Uses the cell-based layout from :mod:`src.core.graph_v2` — each row
carries a ``cells`` list of :class:`CellInfo` dicts that describe the
exact geometry to draw.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import md5

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QMenu,
    QScrollBar,
    QWidget,
)

from src.core.graph_v2 import BRANCH_PALETTE, UNCOMMITTED_COLOR_INDEX
from src.utils.theme import DARK_THEME, Theme
from src.viewmodels.graph_viewmodel import GraphViewModel


@dataclass(frozen=True)
class RenderConfig:
    """Visual constants for the graph table."""

    row_height: int = 32
    node_radius: int = 11
    edge_width: int = 2
    selection_ring_width: int = 1
    subject_max_chars: int = 80
    wip_node_radius: int = 11
    branch_icon_size: int = 10
    ref_chip_height: int = 13
    ref_chip_padding: int = 5
    ref_chip_gap: int = 3
    header_height: int = 28
    divider_width: int = 3
    graph_left_padding: int = 24
    background_color: str = DARK_THEME.bg
    header_bg_color: str = "#1e1e2e"
    text_color: str = DARK_THEME.text
    dim_text_color: str = DARK_THEME.text_dim
    accent_color: str = DARK_THEME.accent
    selection_color: str = DARK_THEME.graph_selection
    edge_color: str = DARK_THEME.graph_edge
    wip_color: str = DARK_THEME.graph_wip
    stash_color: str = DARK_THEME.graph_stash
    divider_color: str = "#444"
    hover_bg_color: str = "#1a2744"
    selected_bg_color: str = "#2a4370"


def _config_for_theme(theme: Theme | None) -> RenderConfig:
    if theme is None:
        return RenderConfig()
    return RenderConfig(
        background_color=theme.bg,
        text_color=theme.text,
        dim_text_color=theme.text_dim,
        accent_color=theme.accent,
        selection_color=theme.graph_selection,
        edge_color=theme.graph_edge,
        wip_color=theme.graph_wip,
        stash_color=theme.graph_stash,
    )


def _icon_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


# Mapping from CellType integer to cell-type name (for debugging).
_CELL_TYPE_NAMES: dict[int, int] = {
    0: "EMPTY", 1: "PIPE", 2: "COMMIT",
    3: "BRANCH_RIGHT", 4: "BRANCH_LEFT", 5: "MERGE_RIGHT",
    6: "MERGE_LEFT", 7: "HORIZONTAL", 8: "HORIZONTAL_PIPE",
    9: "TEE_RIGHT", 10: "TEE_LEFT", 11: "TEE_UP",
}

# Cell types as local constants for readability.
_T_EMPTY = 0
_T_PIPE = 1
_T_COMMIT = 2
_T_BRANCH_RIGHT = 3
_T_BRANCH_LEFT = 4
_T_MERGE_RIGHT = 5
_T_MERGE_LEFT = 6
_T_HORIZONTAL = 7
_T_HORIZONTAL_PIPE = 8
_T_TEE_RIGHT = 9
_T_TEE_LEFT = 10
_T_TEE_UP = 11


def _cell_color(index: int) -> QColor:
    """Map a colour index (0..24) to a QColor."""
    if index == UNCOMMITTED_COLOR_INDEX:
        return QColor(DARK_THEME.graph_wip)
    if 0 <= index < len(BRANCH_PALETTE):
        return QColor(BRANCH_PALETTE[index])
    return QColor(BRANCH_PALETTE[0])


def _lighten_color(color: QColor, factor: float) -> QColor:
    """Lighten *color* by *factor* (0..1) toward white."""
    r = min(255, int(color.red() + (255 - color.red()) * factor))
    g = min(255, int(color.green() + (255 - color.green()) * factor))
    b = min(255, int(color.blue() + (255 - color.blue()) * factor))
    return QColor(r, g, b, color.alpha())


class GraphTableWidget(QWidget):
    """Unified table-like commit graph.

    Renders three columns (branches | graph | commit message) in a
    single paint widget.  The graph column now uses the cell-based
    layout from :mod:`src.core.graph_v2`.
    """

    commit_selected = Signal(str)
    checkout_commit_requested = Signal(str)
    copy_diff_requested = Signal(str)
    stash_apply_requested = Signal(str)
    stash_pop_requested = Signal(str)
    stash_drop_requested = Signal(str)
    discard_changes_requested = Signal(str)

    _AVATAR_COLORS: tuple[str, ...] = (
        "#C44A2B", "#B85C8C", "#9A6E3A", "#5B7FA5",
        "#8B5CF6", "#3B82A0", "#D97706", "#6D8EA0",
    )

    def __init__(
        self,
        view_model: GraphViewModel,
        parent=None,
        *,
        theme: Theme | None = None,
    ) -> None:
        super().__init__(parent)
        self._view_model = view_model
        self._cfg = _config_for_theme(theme)

        self._rows: list[dict] = []
        self._selected_sha: str | None = None
        self._hovered_sha: str | None = None
        self._scroll_offset = 0
        self._h_scrolls: list[int] = [0, 0, 0]
        self._active_col: int = 1
        self._avatar_cache: dict[str, QPixmap] = {}

        self._dividers: list[int] = [180, 500]
        self._dragging_divider: int = -1
        self._drag_start_x: int = 0
        self._drag_start_div: int = 0

        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.valueChanged.connect(self._on_scroll)
        self._scrollbar.setRange(0, 0)

        self._h_scrollbars: list[QScrollBar] = [
            QScrollBar(Qt.Orientation.Horizontal, self),
            QScrollBar(Qt.Orientation.Horizontal, self),
            QScrollBar(Qt.Orientation.Horizontal, self),
        ]
        for i, bar in enumerate(self._h_scrollbars):
            bar.valueChanged.connect(lambda value, idx=i: self._on_h_scroll(idx, value))
            bar.setRange(0, 0)
            bar.hide()

        self.setMouseTracking(True)
        self.setMinimumHeight(100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._view_model.graph_updated.connect(self._on_graph_updated)
        self._view_model.commit_selected.connect(self._on_external_select)
        self._view_model.scroll_to_commit_requested.connect(self.scroll_to_commit)

        self._dump_shortcut = QShortcut("Ctrl+Shift+D", self)
        self._dump_shortcut.activated.connect(self._dump_graph)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def divider_positions(self) -> list[int]:
        return list(self._dividers)

    def set_divider_positions(self, positions: list[int]) -> None:
        if len(positions) == 2:
            self._dividers[0] = max(80, min(positions[0], positions[1] - 60))
            self._dividers[1] = max(self._dividers[0] + 60, positions[1])
            self._update_scrollbar()
            self.update()

    def selected_sha(self) -> str | None:
        return self._selected_sha

    def set_selected_sha(self, sha: str | None) -> None:
        self._selected_sha = sha
        self.update()

    def row_count(self) -> int:
        return len(self._rows)

    def scrollbar(self) -> QScrollBar:
        return self._scrollbar

    def scroll_to_commit(self, sha: str) -> None:
        """Center *sha* vertically and bring its lane into the graph column.

        Used by the search bar, the left-panel branch/tag click handler,
        and by :attr:`GraphViewModel.scroll_to_commit_requested` (which
        the widget subscribes to). The horizontal scroll only operates
        on the graph column (index 1) — the branch chip in column 0
        and the commit message in column 2 are left as the user left
        them, since they are decorated / scrolled by their own
        scrollbars when their text overflows.

        The commit is considered "out of view" if its lane's centre
        falls outside the visible portion of the graph column (with a
        one-node-radius margin on each side). When that happens the
        graph column's horizontal scrollbar is moved just enough to
        bring the lane to roughly the column's centre; otherwise the
        scroll is left untouched so we don't jerk the view around when
        the commit is already visible.
        """
        for idx, row in enumerate(self._rows):
            row_sha = _row_sha(row)
            if row_sha == sha:
                row_center_y = (
                    self._cfg.header_height
                    + idx * self._cfg.row_height
                    + self._cfg.row_height // 2
                )
                viewport_center = self.height() // 2
                target = max(0, row_center_y - viewport_center)
                self._scrollbar.setValue(target)
                self._scroll_horizontal_to_lane(row)
                self.update()
                return

    def _scroll_horizontal_to_lane(self, row: dict) -> None:
        """Adjust the graph column's horizontal scroll so *row*'s lane is visible.

        No-op when the row is a connector (no node, no lane) or when the
        graph column has no horizontal overflow (the bar is hidden, so
        the user can already see the whole column). The new value
        clamps to ``[0, bar.maximum()]`` so a too-narrow viewport never
        lands us in an invalid scroll state.
        """
        commit = row.get("commit")
        is_uncommitted = row.get("is_uncommitted", False)
        if commit is None and not is_uncommitted:
            return
        bar = self._h_scrollbars[1]
        if bar.maximum() <= 0:
            return
        col_ranges = self._col_ranges()
        col1_left, col1_right = col_ranges[1]
        col1_width = col1_right - col1_left
        if col1_width <= 0:
            return
        lane = row.get("lane", 0)
        lane_w = self._cfg.node_radius * 2 + 8
        col_left = self._column_left_x()
        node_cx = col_left + lane * lane_w
        margin = self._cfg.node_radius
        # Visible region in *content* coordinates (i.e. accounting for
        # the current horizontal scroll). The painter translates by
        # ``-value``, so a content-x of X appears at widget-x
        # ``col1_left + (X - col1_left - value) = X - value``.
        current_value = self._h_scrolls[1]
        visible_left = col1_left + current_value + margin
        visible_right = col1_left + current_value + col1_width - margin
        if visible_left <= node_cx <= visible_right:
            return
        # Centre the lane in the column. The column's left edge in
        # content coordinates is ``col1_left`` (no padding inside the
        # content), so a scroll value of ``node_cx - col1_center``
        # brings the lane to the middle of the column.
        col1_center = col1_left + col1_width // 2
        new_value = node_cx - col1_center
        new_value = max(0, min(int(bar.maximum()), int(new_value)))
        bar.setValue(new_value)

    # ------------------------------------------------------------------
    # signal handlers
    # ------------------------------------------------------------------

    def _on_graph_updated(self, rows: list[dict]) -> None:
        self._rows = rows
        self._update_scrollbar()
        self.update()

    def _on_scroll(self, value: int) -> None:
        self._scroll_offset = value
        self.update()

    def _on_h_scroll(self, col: int, value: int) -> None:
        self._h_scrolls[col] = value
        self.update()

    def _on_external_select(self, sha: str) -> None:
        self._selected_sha = sha
        self.update()

    # ------------------------------------------------------------------
    # layout helpers
    # ------------------------------------------------------------------

    def _update_scrollbar(self) -> None:
        total_h = self._cfg.header_height + len(self._rows) * self._cfg.row_height
        visible_h = self.height()
        self._scrollbar.setRange(0, max(0, total_h - visible_h))
        self._scrollbar.setPageStep(max(1, visible_h))
        self._scrollbar.setSingleStep(self._cfg.row_height // 2)

        bar_h = self._h_scrollbars[0].sizeHint().height()
        ranges = self._col_ranges()
        overflows = self._compute_column_overflows(ranges, bar_h)
        for col, (overflow, (left, right)) in enumerate(zip(overflows, ranges, strict=True)):
            visible_w = max(1, right - left)
            bar = self._h_scrollbars[col]
            bar.setRange(0, max(0, overflow))
            bar.setPageStep(max(1, visible_w))
            bar.setSingleStep(20)
            if overflow > 0:
                bar.setGeometry(left, self.height() - bar_h, visible_w, bar_h)
                bar.show()
            else:
                bar.hide()
            self._h_scrolls[col] = bar.value()
        if self._active_col >= 0 and overflows[self._active_col] == 0:
            self._active_col = next(
                (i for i, o in enumerate(overflows) if o > 0),
                1,
            )

    def _compute_column_overflows(
        self, ranges: list[tuple[int, int]], bar_h: int,
    ) -> list[int]:
        del bar_h
        nr = self._cfg.node_radius
        lane_w = nr * 2 + 8
        pad = self._cfg.graph_left_padding
        col1_left, col1_right = ranges[1]
        col1_width = col1_right - col1_left

        max_lane = 0
        for row in self._rows:
            cells = row.get("cells", [])
            if cells:
                max_lane = max(max_lane, (len(cells) - 1) // 2)
            lane = row.get("lane", 0)
            max_lane = max(max_lane, lane)
        graph_needed = max_lane * lane_w + nr * 2 + pad
        graph_overflow = graph_needed - (col1_width - pad)

        fm = self.fontMetrics()
        max_branch_w = 0
        max_text_w = 0
        for row in self._rows:
            branch_refs = row.get("branch_refs", [])
            if branch_refs:
                w = self._measure_branch_row(branch_refs, fm)
                if w > max_branch_w:
                    max_branch_w = w
            commit = row.get("commit")
            if commit is not None:
                subject = commit.get("subject", "") or ""
                w = fm.horizontalAdvance(subject)
                if w > max_text_w:
                    max_text_w = w
        col0_left, col0_right = ranges[0]
        col0_width = col0_right - col0_left
        col2_left, col2_right = ranges[2]
        col2_width = col2_right - col2_left
        branch_overflow = max_branch_w - (col0_width - 10)
        text_overflow = max_text_w - (col2_width - 24)

        return [max(0, branch_overflow), max(0, graph_overflow), max(0, text_overflow)]

    def _measure_branch_row(self, branch_refs: list, fm) -> int:
        icon_size = self._cfg.branch_icon_size
        pad = 5
        gap = 3
        avatar_size = icon_size + 4
        cursor = 6
        for branch in branch_refs:
            display = branch["name"]
            if branch.get("is_remote"):
                parts = display.split("/", 1)
                if len(parts) == 2:
                    display = parts[1]
            reserved = (
                pad * 2 + (icon_size + gap if branch.get("is_head") else 0)
                + (gap + icon_size if not branch.get("is_remote") else 0)
                + gap + avatar_size
            )
            cursor += fm.horizontalAdvance(display) + reserved
        return cursor

    def _col_ranges(self) -> list[tuple[int, int]]:
        ranges: list[tuple[int, int]] = []
        start = 0
        for d in self._dividers:
            ranges.append((start, d))
            start = d
        ranges.append((start, self.width()))
        return ranges

    def _divider_at(self, x: int) -> int:
        zone = 6
        for i, dx in enumerate(self._dividers):
            if abs(x - dx) <= zone:
                return i
        return -1

    # ------------------------------------------------------------------
    # context menu
    # ------------------------------------------------------------------

    def _on_context_menu(self, position) -> None:
        sha = self._hit_test_commit(position.x(), position.y())
        if sha is None:
            return
        row_data = self._row_by_sha(sha)
        kind = _row_kind(row_data) if row_data else "commit"

        menu = QMenu(self)
        if kind == "stash":
            apply_action = menu.addAction("Apply Stash")
            apply_action.triggered.connect(lambda: self.stash_apply_requested.emit(sha))
            pop_action = menu.addAction("Pop Stash")
            pop_action.triggered.connect(lambda: self.stash_pop_requested.emit(sha))
            menu.addSeparator()
            copy_diff_action = menu.addAction("Copy diff")
            copy_diff_action.triggered.connect(
                lambda checked=False, s=sha: self.copy_diff_requested.emit(s),
            )
            menu.addSeparator()
            drop_action = menu.addAction("Delete Stash")
            drop_action.triggered.connect(lambda: self.stash_drop_requested.emit(sha))
        elif kind == "wip":
            discard_action = menu.addAction("Discard changes")
            discard_action.triggered.connect(
                lambda checked=False, s=sha: self.discard_changes_requested.emit(s),
            )
            copy_diff_action = menu.addAction("Copy diff")
            copy_diff_action.triggered.connect(
                lambda checked=False, s=sha: self.copy_diff_requested.emit(s),
            )
        else:
            checkout_action = menu.addAction("Checkout this commit")
            checkout_action.triggered.connect(
                lambda checked=False, s=sha: self.checkout_commit_requested.emit(s),
            )
            copy_diff_action = menu.addAction("Copy diff")
            copy_diff_action.triggered.connect(
                lambda checked=False, s=sha: self.copy_diff_requested.emit(s),
            )
        menu.exec(self.mapToGlobal(position))

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()

        painter.fillRect(self.rect(), QColor(self._cfg.background_color))
        hh = self._cfg.header_height

        # header
        painter.fillRect(0, 0, w, hh, QColor(self._cfg.header_bg_color))
        painter.setPen(QPen(QColor(self._cfg.divider_color), 1))

        col_starts = [0] + self._dividers
        col_labels = ["Branches", "Graph", "Commit Message"]
        for i, (x_start, _label) in enumerate(zip(col_starts, col_labels, strict=True)):
            x_end = col_starts[i + 1] if i + 1 < len(col_starts) else w
            if i < len(self._dividers):
                painter.drawLine(x_end - 1, 0, x_end - 1, hh)
            painter.drawLine(x_start, hh - 1, x_end, hh - 1)

        painter.setPen(QPen(QColor(self._cfg.text_color)))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        for i, (x_start, label) in enumerate(zip(col_starts, col_labels, strict=True)):
            x_end = col_starts[i + 1] if i + 1 < len(col_starts) else w
            avail = x_end - x_start - 12
            if avail < 20:
                continue
            painter.drawText(
                x_start + 6, 0, avail, hh,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label,
            )

        # data area
        painter.save()
        painter.setClipRect(0, hh, w, self.height() - hh)
        self._draw_row_backgrounds(painter, hh)
        painter.restore()

        col_ranges = self._col_ranges()
        for col, (left, right) in enumerate(col_ranges):
            if right <= left:
                continue
            painter.save()
            painter.setClipRect(left, hh, right - left, self.height() - hh)
            painter.translate(-self._h_scrolls[col], 0)
            if col == 0:
                self._draw_branch_column(painter, hh, left, right)
            elif col == 1:
                self._draw_graph_column(painter, hh, left, right)
            else:
                self._draw_commit_column(painter, hh, left, right)
            painter.restore()

        # column guide lines
        painter.setPen(QPen(QColor("#2a2a3a"), 1, Qt.PenStyle.DotLine))
        for dx in self._dividers:
            painter.drawLine(dx, hh, dx, self.height())

        # divider handles
        for dx in self._dividers:
            rect_w = self._cfg.divider_width
            painter.fillRect(
                dx - rect_w // 2, 0, rect_w, self.height(),
                QColor(self._cfg.divider_color).darker(120),
            )

    def _row_y(self, row: int) -> float:
        return self._cfg.header_height + self._cfg.row_height * row - self._scroll_offset

    def _column_left_x(self) -> float:
        return self._dividers[0] + self._cfg.graph_left_padding

    def _column_center_x(self) -> float:
        return (self._dividers[0] + self._dividers[1]) / 2

    def _column_width(self) -> float:
        return self._dividers[1] - self._dividers[0]

    # ------------------------------------------------------------------
    # cell-based graph rendering
    # ------------------------------------------------------------------

    def _lane_x(self, lane: int, lane_w: float) -> float:
        """X coordinate for *lane* (0-indexed), aligned to the left edge of
        the graph column with ``graph_left_padding`` indent."""
        return self._column_left_x() + lane * lane_w

    def _draw_cells(self, painter: QPainter, header_h: int) -> None:
        """Draw the graph using cell data from each row."""
        dh = self._cfg.row_height
        nr = self._cfg.node_radius
        ew = max(3, self._cfg.edge_width)
        col_left = self._column_left_x()
        lane_w = nr * 2 + 8

        prev_occupied: set[int] = set()

        for row_idx, row_data in enumerate(self._rows):
            y = self._row_y(row_idx)
            y_center = y + dh / 2
            if y + dh < header_h or y > self.height():
                continue

            cells = row_data.get("cells", [])
            lane = row_data.get("lane", 0)

            cur_occupied: set[int] = set()
            for ci, cell in enumerate(cells):
                if cell.get("t", _T_EMPTY) != _T_EMPTY:
                    cur_occupied.add(ci // 2)
            if row_data.get("commit") is not None or row_data.get("is_uncommitted"):
                cur_occupied.add(lane)

            if row_idx > 0:
                prev_y_center = self._row_y(row_idx - 1) + dh / 2
                common = prev_occupied & cur_occupied
                for li in common:
                    x = self._lane_x(li, lane_w)
                    clr_idx = 0
                    for ci, cell in enumerate(cells):
                        if ci // 2 == li and cell.get("t", _T_EMPTY) != _T_EMPTY:
                            t = cell.get("t", _T_EMPTY)
                            if t in (_T_HORIZONTAL_PIPE, _T_TEE_RIGHT, _T_TEE_LEFT, _T_TEE_UP):
                                clr_idx = cell.get("p", cell.get("c", 0))
                            else:
                                clr_idx = cell.get("c", 0)
                            break
                    if clr_idx == 0 and li == lane:
                        clr_idx = row_data.get("color_index", 0)
                    clr = _cell_color(clr_idx)
                    pen = QPen(clr, ew, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
                    painter.setPen(pen)
                    painter.drawLine(
                        int(x), int(prev_y_center + nr),
                        int(x), int(y_center - nr),
                    )

            prev_occupied = cur_occupied

            _draw_cell_row(painter, cells, col_left, lane_w, y_center, dh, ew, nr)

    def _draw_row_backgrounds(self, painter: QPainter, header_h: int) -> None:
        dh = self._cfg.row_height
        for row_idx, row_data in enumerate(self._rows):
            y = self._row_y(row_idx)
            if y + dh < header_h or y > self.height():
                continue
            sha = _row_sha(row_data)
            is_selected = sha == self._selected_sha
            is_hovered = sha == self._hovered_sha
            if is_selected or is_hovered:
                bg_color = (
                    self._cfg.selected_bg_color if is_selected
                    else self._cfg.hover_bg_color
                )
                y_center = y + dh / 2
                painter.fillRect(
                    self._dividers[0],
                    int(y_center - self._cfg.node_radius),
                    self.width() - self._dividers[0],
                    self._cfg.node_radius * 2,
                    QColor(bg_color),
                )

    def _draw_branch_column(
        self, painter: QPainter, header_h: int, left: int, right: int,
    ) -> None:
        dh = self._cfg.row_height
        fm = self.fontMetrics()
        for row_idx, row_data in enumerate(self._rows):
            y = self._row_y(row_idx)
            y_center = y + dh / 2
            if y + dh < header_h or y > self.height():
                continue
            self._draw_branch_chips(painter, row_data, (left, right), y_center, fm)

    def _draw_graph_column(
        self, painter: QPainter, header_h: int, left: int, right: int,
    ) -> None:
        self._draw_cells(painter, header_h)
        dh = self._cfg.row_height
        col_cx = self._column_center_x()
        lane_w = self._cfg.node_radius * 2 + 8
        del left, right
        for row_idx, row_data in enumerate(self._rows):
            y = self._row_y(row_idx)
            y_center = y + dh / 2
            if y + dh < header_h or y > self.height():
                continue
            self._draw_graph_node(painter, row_data, col_cx, lane_w, y_center)

    def _draw_commit_column(
        self, painter: QPainter, header_h: int, left: int, right: int,
    ) -> None:
        dh = self._cfg.row_height
        fm = self.fontMetrics()
        for row_idx, row_data in enumerate(self._rows):
            y = self._row_y(row_idx)
            y_center = y + dh / 2
            if y + dh < header_h or y > self.height():
                continue
            self._draw_commit_text(painter, row_data, (left, right), y_center, fm)

    # ------------------------------------------------------------------
    # branch chips (unchanged from old code)
    # ------------------------------------------------------------------

    def _draw_branch_chips(
        self, painter: QPainter, row_data: dict,
        col_range: tuple[int, int], y_center: float, fm,
    ) -> None:
        branch_refs = row_data.get("branch_refs", [])
        if not branch_refs:
            return

        icon_size = self._cfg.branch_icon_size
        pad = 5
        gap = 3
        avatar_size = icon_size + 4
        col_left, col_right = col_range
        avail_w = col_right - col_left - 10
        if avail_w < 20:
            return

        commit_color = _row_color(row_data)
        chip_text_color = QColor("#FFFFFF")
        cursor_x = col_left + 6

        for branch in branch_refs:
            is_head = branch.get("is_head")
            is_remote = branch.get("is_remote")
            display = branch["name"]
            if is_remote:
                parts = display.split("/", 1)
                if len(parts) == 2:
                    display = parts[1]

            text_w = fm.horizontalAdvance(display)
            text_h = fm.height()

            content_w = pad
            if is_head:
                content_w += icon_size + gap
            content_w += text_w
            if not is_remote:
                content_w += gap + icon_size
            content_w += gap + avatar_size + pad

            chip_h = self._cfg.node_radius * 2
            chip_top = y_center - chip_h / 2

            chip_path = QPainterPath()
            chip_path.addRoundedRect(cursor_x, chip_top, content_w, chip_h, 4, 4)
            painter.fillPath(chip_path, QBrush(commit_color))

            inner_x = cursor_x + pad
            inner_cy = y_center

            if is_head:
                ck = QPainterPath()
                ck.moveTo(inner_x, inner_cy - icon_size * 0.15)
                ck.lineTo(inner_x + icon_size * 0.35, inner_cy + icon_size * 0.25)
                ck.lineTo(inner_x + icon_size, inner_cy - icon_size * 0.45)
                painter.setPen(_icon_pen(chip_text_color, 1.6))
                painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                painter.drawPath(ck)
                inner_x += icon_size + gap

            painter.setPen(QPen(chip_text_color))
            painter.setFont(self.font())
            text_y = inner_cy + text_h / 2 - fm.descent()
            painter.drawText(int(inner_x), int(text_y), display)
            inner_x += text_w

            if not is_remote:
                mn = QPainterPath()
                mn_x = inner_x + gap
                mn_y = inner_cy - icon_size / 2
                sh = icon_size * 0.7
                mn.addRoundedRect(mn_x, mn_y, icon_size, sh, 1.2, 1.2)
                nx = mn_x + icon_size / 2
                ntop = mn_y + sh
                nbot = ntop + icon_size * 0.18
                mn.moveTo(nx, ntop)
                mn.lineTo(nx, nbot)
                bh = icon_size * 0.32
                mn.moveTo(nx - bh, nbot)
                mn.lineTo(nx + bh, nbot)
                painter.setPen(_icon_pen(chip_text_color, 1.2))
                painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                painter.drawPath(mn)
                inner_x += gap + icon_size

            avatar = self._avatar_for(
                _row_author(row_data), avatar_size,
            )
            painter.drawPixmap(int(inner_x + gap), int(inner_cy - avatar_size / 2), avatar)
            cursor_x += content_w + gap

    # ------------------------------------------------------------------
    # graph node rendering
    # ------------------------------------------------------------------

    def _draw_graph_node(
        self, painter: QPainter, row_data: dict,
        col_cx: float, lane_w: float, y_center: float,
    ) -> None:
        commit = row_data.get("commit")
        is_uncommitted = row_data.get("is_uncommitted", False)
        is_connector = commit is None and not is_uncommitted

        if is_connector:
            # Fork connector row — only cells are drawn, no node
            return

        lane = row_data.get("lane", 0)
        cx = self._lane_x(lane, lane_w)
        color_index = row_data.get("color_index", 0)
        color = _cell_color(color_index) if not is_uncommitted else QColor(self._cfg.wip_color)
        sha = _row_sha(row_data)
        kind = _row_kind(row_data)
        is_selected = sha == self._selected_sha
        is_stash = kind == "stash"

        painter.save()

        if is_uncommitted:
            radius = self._cfg.wip_node_radius
            painter.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine))
            fill = QColor(self._cfg.background_color)
            fill.setAlpha(80)
            painter.setBrush(fill)
            painter.drawEllipse(
                int(cx - radius), int(y_center - radius),
                int(radius * 2), int(radius * 2),
            )
        elif is_stash:
            radius = self._cfg.wip_node_radius
            stash_c = color if color.isValid() else QColor(self._cfg.stash_color)
            if is_selected:
                stash_c = _lighten_color(stash_c, 0.4)
            painter.setPen(QPen(stash_c, 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(self._cfg.background_color))
            painter.drawEllipse(
                int(cx - radius), int(y_center - radius),
                int(radius * 2), int(radius * 2),
            )
            bar_w = int(radius * 1.0)
            bar_h = max(2, int(radius * 0.22))
            gap = max(1, int(radius * 0.1))
            total_h = bar_h * 3 + gap * 2
            start_y = int(y_center - total_h / 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(stash_c)
            for i in range(3):
                w = bar_w - abs(i - 1) * int(radius * 0.14)
                bx = int(cx - w / 2)
                by = start_y + i * (bar_h + gap)
                painter.drawRoundedRect(bx, by, w, bar_h, 2, 2)
        elif is_selected:
            radius = self._cfg.node_radius
            painter.setBrush(color)
            painter.setPen(QPen(QColor(self._cfg.selection_color), self._cfg.selection_ring_width))
            painter.drawEllipse(
                int(cx - radius), int(y_center - radius),
                int(radius * 2), int(radius * 2),
            )
        else:
            radius = self._cfg.node_radius
            painter.setBrush(color)
            painter.setPen(QPen(color, 0))
            painter.drawEllipse(
                int(cx - radius), int(y_center - radius),
                int(radius * 2), int(radius * 2),
            )

        painter.restore()

        if not is_uncommitted and not is_stash:
            av_size = max(6, int(radius * 2.6 - 8))
            av_pix = self._avatar_for(
                _row_author(row_data), av_size, shape="circle",
            )
            painter.drawPixmap(
                int(cx - av_size / 2), int(y_center - av_size / 2), av_pix,
            )

        chip_y = y_center - self._cfg.ref_chip_height / 2 - 1
        label_x = cx + radius + 4
        has_head_branch = any(b.get("is_head") for b in row_data.get("branch_refs", []))
        for ref_label in row_data.get("refs", []):
            if ref_label == "HEAD" and has_head_branch:
                continue
            chip = self._draw_ref_chip(painter, ref_label, label_x, chip_y, color)
            label_x += chip[0] + self._cfg.ref_chip_gap

    def _draw_ref_chip(
        self, painter: QPainter, label: str, x: float, y: float, color: QColor,
    ) -> tuple[float, float]:
        text_w = self.fontMetrics().horizontalAdvance(label)
        pad = self._cfg.ref_chip_padding
        w = text_w + pad * 2
        h = self._cfg.ref_chip_height
        chip_path = QPainterPath()
        chip_path.addRoundedRect(x, y, w, h, 3, 3)
        painter.fillPath(chip_path, QBrush(color))
        painter.setPen(QPen(QColor(self._cfg.text_color)))
        painter.setFont(self.font())
        painter.drawText(int(x + pad), int(y + h - 2), label)
        return w, h

    def _draw_commit_text(
        self, painter: QPainter, row_data: dict,
        col_range: tuple[int, int], y_center: float, fm,
    ) -> None:
        col_left, col_right = col_range
        if col_right - col_left < 20:
            return

        subject = _row_subject(row_data)
        if not subject:
            return

        kind = _row_kind(row_data)
        text_color = (
            QColor(self._cfg.dim_text_color) if kind in ("wip", "stash")
            else QColor(self._cfg.text_color)
        )
        subject_x = col_left + 8
        subject_y = int(y_center + fm.ascent() / 2 - 1)

        painter.setPen(QPen(text_color))
        painter.setFont(self.font())
        painter.drawText(subject_x, subject_y, subject)

    # ------------------------------------------------------------------
    # avatars
    # ------------------------------------------------------------------

    def _avatar_for(
        self, seed: str, size: int = 14, *, shape: str = "square",
    ) -> QPixmap:
        if not seed:
            seed = "?"
        cache_key = f"{seed}_{size}_{shape}"
        if cache_key not in self._avatar_cache:
            h_bytes = md5(seed.encode()).digest()
            fg = QColor(self._AVATAR_COLORS[h_bytes[0] % len(self._AVATAR_COLORS)])
            bg = QColor("#F4F4F4")

            grid = [[False] * 5 for _ in range(5)]
            bits = int.from_bytes(h_bytes[3:6], "big")
            for row in range(5):
                for col in range(3):
                    if bits & (1 << (row * 3 + col)):
                        grid[row][col] = True
                        grid[row][4 - col] = True

            cell = size / 5.0
            pix = QPixmap(size, size)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            margin = 1.0
            d = size - margin * 2
            clip = QPainterPath()
            if shape == "circle":
                clip.addEllipse(margin, margin, d, d)
            else:
                clip.addRoundedRect(margin, margin, d, d, 3, 3)
            painter.setClipPath(clip)

            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            painter.drawRect(0, 0, size, size)

            painter.setBrush(QBrush(fg))
            for row in range(5):
                for col in range(5):
                    if grid[row][col]:
                        painter.drawRect(col * cell, row * cell, cell, cell)

            painter.setClipping(False)
            painter.end()
            self._avatar_cache[cache_key] = pix
        return self._avatar_cache[cache_key]

    # ------------------------------------------------------------------
    # input
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x, y = event.pos().x(), event.pos().y()
        hh = self._cfg.header_height

        di = self._divider_at(x)
        if di >= 0:
            self._dragging_divider = di
            self._drag_start_x = x
            self._drag_start_div = self._dividers[di]
            self.setCursor(Qt.CursorShape.SplitHCursor)
            event.accept()
            return

        if y >= hh:
            sha = self._hit_test_commit(x, y)
            if sha is not None:
                self._selected_sha = sha
                self.update()
                self._view_model.select_commit(sha)
                self.commit_selected.emit(sha)
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging_divider >= 0:
            dx = event.pos().x() - self._drag_start_x
            new_pos = self._drag_start_div + dx
            self._move_divider(self._dragging_divider, new_pos)
            self.update()
            event.accept()
            return

        x, y = event.pos().x(), event.pos().y()
        hh = self._cfg.header_height

        self._active_col = self._column_at(x)

        if self._divider_at(x) >= 0:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

        if y >= hh:
            sha = self._hit_test_commit(x, y)
            if sha != self._hovered_sha:
                self._hovered_sha = sha
                self.update()
        elif self._hovered_sha is not None:
            self._hovered_sha = None
            self.update()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging_divider = -1
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        mods = event.modifiers()
        delta_y = event.angleDelta().y()
        delta_x = event.angleDelta().x()
        if mods & Qt.KeyboardModifier.ShiftModifier or delta_x != 0:
            col = self._column_at(event.position().x())
            bar = self._h_scrollbars[col]
            delta = delta_x or delta_y
            bar.setValue(bar.value() - delta // 3)
            event.accept()
            return
        delta = delta_y
        self._scrollbar.setValue(self._scrollbar.value() - delta // 3)

    def _column_at(self, x: float) -> int:
        for col, (left, right) in enumerate(self._col_ranges()):
            if left <= x < right:
                return col
        return min(self._active_col, 2)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            col = min(max(self._active_col, 0), 2)
            bar = self._h_scrollbars[col]
            if bar.maximum() == 0:
                col = next(
                    (i for i, b in enumerate(self._h_scrollbars) if b.maximum() > 0),
                    col,
                )
                bar = self._h_scrollbars[col]
            step = 20 if key == Qt.Key.Key_Right else -20
            bar.setValue(bar.value() + step)
            event.accept()
            return
        if key == Qt.Key.Key_Home:
            col = min(max(self._active_col, 0), 2)
            self._h_scrollbars[col].setValue(0)
            event.accept()
            return
        if key == Qt.Key.Key_End:
            col = min(max(self._active_col, 0), 2)
            self._h_scrollbars[col].setValue(self._h_scrollbars[col].maximum())
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        w = self.width()
        h = self.height()
        bar_w = self._scrollbar.sizeHint().width()
        self._scrollbar.setGeometry(w - bar_w, 0, bar_w, h)
        self._update_scrollbar()

    def _move_divider(self, index: int, new_x: int) -> None:
        if index == 0:
            self._dividers[0] = max(80, min(new_x, self._dividers[1] - 60))
        else:
            self._dividers[1] = max(self._dividers[0] + 60, new_x)
        self._update_scrollbar()

    def _hit_test_commit(self, x: int, y: int) -> str | None:
        hh = self._cfg.header_height
        dh = self._cfg.row_height
        scroll_y = y - hh + self._scroll_offset
        for row_idx, row_data in enumerate(self._rows):
            row_top = dh * row_idx
            if row_top <= scroll_y < row_top + dh:
                return _row_sha(row_data)
        return None

    def _row_by_sha(self, sha: str) -> dict | None:
        for row_data in self._rows:
            if _row_sha(row_data) == sha:
                return row_data
        return None

    def _dump_graph(self) -> None:
        """Save a diagnostic JSON dump of the current graph layout (Ctrl+Shift+D)."""
        rows_data: list[dict] = []
        for idx, row_data in enumerate(self._rows):
            commit = row_data.get("commit")
            cells_out: list[dict] = []
            for ci, cell in enumerate(row_data.get("cells", [])):
                t = cell.get("t", 0)
                cells_out.append({
                    "idx": ci,
                    "lane": ci // 2,
                    "type": t,
                    "color": cell.get("c", 0),
                    "pipe_color": cell.get("p", 0),
                })
            rows_data.append({
                "row": idx,
                "lane": row_data.get("lane", 0),
                "color_index": row_data.get("color_index", 0),
                "branch_names": row_data.get("branch_names", []),
                "is_head": row_data.get("is_head", False),
                "is_uncommitted": row_data.get("is_uncommitted", False),
                "sha": commit["sha"] if commit else None,
                "short_sha": commit["short_sha"] if commit else None,
                "subject": row_data.get("commit", {}).get("subject", ""),
                "cells": cells_out,
            })

        palette_map = {i: c for i, c in enumerate(BRANCH_PALETTE)}
        palette_map[UNCOMMITTED_COLOR_INDEX] = self._cfg.wip_color

        dump = {
            "timestamp": datetime.now().isoformat(),
            "row_count": len(self._rows),
            "max_lane": max(
                (row_data.get("lane", 0) for row_data in self._rows), default=0,
            ),
            "palette": palette_map,
            "wip_color_index": UNCOMMITTED_COLOR_INDEX,
            "stash_color": self._cfg.stash_color,
            "rows": rows_data,
        }

        default_name = f"graph_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Graph Dump", default_name, "JSON (*.json)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dump, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
# Standalone cell drawing (used by _draw_cells and reusable for tests)
# --------------------------------------------------------------------------

def _draw_cell_row(
    painter: QPainter,
    cells: list[dict],
    col_left: float,
    lane_w: float,
    y_center: float,
    row_height: float,
    edge_width: float,
    node_radius: float,
) -> None:
    """Draw one row of graph cells at *y_center*.

    Vertical lines (PIPE, COMMIT) span *node_radius* above and below
    the centre; the inter-row gap is bridged by ``_draw_cells``.
    """
    half_h = row_height / 2.0

    for idx, cell in enumerate(cells):
        t = cell.get("t", 0)
        c = cell.get("c", 0)
        p = cell.get("p", 0)

        lane = idx // 2
        is_even = (idx % 2 == 0)

        if is_even:
            x = col_left + lane * lane_w
        else:
            x = col_left + lane * lane_w + lane_w / 2

        color = _cell_color(c)
        p_color = _cell_color(p) if p else color

        if t == _T_EMPTY:
            continue
        elif t == _T_PIPE:
            _draw_vert_line(painter, x, y_center, node_radius, edge_width, color)
        elif t == _T_COMMIT:
            _draw_vert_line(painter, x, y_center, node_radius, edge_width, color)
        elif t == _T_BRANCH_RIGHT:
            _draw_branch_right(painter, x, y_center, half_h, edge_width, color)
        elif t == _T_BRANCH_LEFT:
            _draw_branch_left(painter, x, y_center, half_h, edge_width, color)
        elif t == _T_MERGE_RIGHT:
            _draw_merge_right(painter, x, y_center, half_h, edge_width, color)
        elif t == _T_MERGE_LEFT:
            _draw_merge_left(painter, x, y_center, half_h, edge_width, color)
        elif t == _T_HORIZONTAL:
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
        elif t == _T_HORIZONTAL_PIPE:
            _draw_vert_line(painter, x, y_center, half_h, edge_width, p_color)
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
        elif t == _T_TEE_RIGHT:
            vert_color = p_color if p else color
            _draw_vert_line(painter, x, y_center, half_h, edge_width, vert_color)
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
        elif t == _T_TEE_LEFT:
            vert_color = p_color if p else color
            _draw_vert_line(painter, x, y_center, half_h, edge_width, vert_color)
            _draw_horiz_line(painter, x, y_center, -lane_w, edge_width, color)
        elif t == _T_TEE_UP:
            vert_color = p_color if p else color
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
            _draw_vert_line(painter, x, y_center, half_h, edge_width, vert_color, upward_only=True)


def _draw_vert_line(
    painter: QPainter, x: float, y_center: float,
    half_h: float, width: float, color: QColor, *, upward_only: bool = False,
) -> None:
    """Draw a vertical line segment centred at *y_center*, spanning *half_h*
    pixels above and below (or only above when *upward_only*)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    top = y_center - half_h
    bot = y_center + half_h
    if upward_only:
        bot = y_center
    painter.drawLine(int(x), int(top), int(x), int(bot))


def _draw_horiz_line(
    painter: QPainter, x: float, y_center: float,
    lane_w: float, width: float, color: QColor,
) -> None:
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    sign = 1 if lane_w > 0 else -1
    abs_w = abs(lane_w)
    painter.drawLine(int(x), int(y_center), int(x + sign * abs_w), int(y_center))


def _draw_branch_right(
    painter: QPainter, x: float, y_center: float,
    radius: float, width: float, color: QColor,
) -> None:
    """Branch starting here, going down and right (╭)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center + radius)
    path.lineTo(x, y_center + cr)
    path.cubicTo(x, y_center, x, y_center, x + cr, y_center)
    painter.drawPath(path)


def _draw_branch_left(
    painter: QPainter, x: float, y_center: float,
    radius: float, width: float, color: QColor,
) -> None:
    """Branch starting here, going down and left (╮)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center + radius)
    path.lineTo(x, y_center + cr)
    path.cubicTo(x, y_center, x, y_center, x - cr, y_center)
    painter.drawPath(path)


def _draw_merge_right(
    painter: QPainter, x: float, y_center: float,
    radius: float, width: float, color: QColor,
) -> None:
    """Merge from below, going up and right (╰)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center - radius)
    path.lineTo(x, y_center - cr)
    path.cubicTo(x, y_center, x, y_center, x + cr, y_center)
    painter.drawPath(path)


def _draw_merge_left(
    painter: QPainter, x: float, y_center: float,
    radius: float, width: float, color: QColor,
) -> None:
    """Merge from below, going up and left (╯)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center - radius)
    path.lineTo(x, y_center - cr)
    path.cubicTo(x, y_center, x, y_center, x - cr, y_center)
    painter.drawPath(path)


# --------------------------------------------------------------------------
# Row data access helpers
# --------------------------------------------------------------------------

def _row_sha(row: dict) -> str:
    """Extract SHA from a row dict (works with both old and new formats)."""
    commit = row.get("commit")
    if commit is not None:
        return commit.get("sha", "")
    # Old format fallback
    return row.get("sha", "")


def _row_kind(row: dict) -> str:
    """Extract kind from a row dict."""
    if row.get("is_uncommitted"):
        return "wip"
    commit = row.get("commit")
    if commit is not None:
        return commit.get("kind", "commit")
    return row.get("kind", "commit")


def _row_subject(row: dict) -> str:
    """Extract subject from a row dict."""
    if row.get("is_uncommitted"):
        return "WIP: Uncommitted changes"
    commit = row.get("commit")
    if commit is not None:
        return commit.get("subject", "")
    return row.get("subject", "")


def _row_author(row: dict) -> str:
    """Extract author info for avatar seed."""
    commit = row.get("commit")
    if commit is not None:
        return commit.get("author_email") or commit.get("author_name", "")
    return row.get("author_email") or row.get("author_name", "")


def _row_color(row: dict) -> QColor:
    """Extract colour as QColor."""
    if row.get("is_uncommitted"):
        return QColor(DARK_THEME.graph_wip)
    ci = row.get("color_index", 0)
    return _cell_color(ci)


__all__ = ["GraphTableWidget", "RenderConfig"]
