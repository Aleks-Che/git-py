"""Graph table widget: unified commit graph with header row and three columns.

The widget renders the entire commit graph as a single table:
row 0 is a fixed header (Branches | Graph | Commit Message) with
visible borders; rows 1+ are commit data without grid lines but
aligned to the same column boundaries.

Column dividers are draggable; their positions are persisted
per-repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import md5

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QScrollBar,
    QWidget,
)

from src.utils.theme import DARK_THEME, Theme
from src.viewmodels.graph_viewmodel import GraphViewModel


@dataclass(frozen=True)
class RenderConfig:
    """Visual constants for the graph table."""

    row_height: int = 36
    node_radius: int = 13
    edge_width: int = 2
    selection_ring_width: int = 1
    subject_max_chars: int = 80
    wip_node_radius: int = 5
    branch_icon_size: int = 10
    ref_chip_height: int = 14
    ref_chip_padding: int = 5
    ref_chip_gap: int = 3
    header_height: int = 28
    divider_width: int = 3
    background_color: str = DARK_THEME.bg
    header_bg_color: str = "#1e1e2e"
    text_color: str = DARK_THEME.text
    dim_text_color: str = DARK_THEME.text_dim
    accent_color: str = DARK_THEME.accent
    selection_color: str = DARK_THEME.graph_selection
    edge_color: str = DARK_THEME.graph_edge
    wip_color: str = DARK_THEME.graph_wip
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
    )


def _icon_pen(color: QColor, width: float) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class GraphTableWidget(QWidget):
    """Unified table-like commit graph.

    Renders three columns (branches | graph | commit message) in a
    single paint widget. Row 0 is a fixed header with visible column
    borders. Rows 1+ are commit data without grid lines.

    Column dividers are draggable to resize columns.
    """

    commit_selected = Signal(str)

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
        self._avatar_cache: dict[str, QPixmap] = {}

        # Column divider positions in widget-local pixels.
        # [div1_x, div2_x] — boundaries after branches and after graph.
        self._dividers: list[int] = [180, 500]

        # Dragging state.
        self._dragging_divider: int = -1
        self._drag_start_x: int = 0
        self._drag_start_div: int = 0

        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.valueChanged.connect(self._on_scroll)
        self._scrollbar.setRange(0, 0)

        self.setMouseTracking(True)
        self.setMinimumHeight(100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._view_model.graph_updated.connect(self._on_graph_updated)
        self._view_model.commit_selected.connect(self._on_external_select)

    # ----- public API ---------------------------------------------------

    def divider_positions(self) -> list[int]:
        return list(self._dividers)

    def set_divider_positions(self, positions: list[int]) -> None:
        if len(positions) == 2:
            self._dividers[0] = max(80, min(positions[0], positions[1] - 60))
            self._dividers[1] = max(self._dividers[0] + 60, positions[1])
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

    # ----- signal handlers ---------------------------------------------

    def _on_graph_updated(self, rows: list[dict]) -> None:
        self._rows = rows
        self._update_scrollbar()
        self.update()

    def _on_scroll(self, value: int) -> None:
        self._scroll_offset = value
        self.update()

    def _on_external_select(self, sha: str) -> None:
        self._selected_sha = sha
        self.update()

    # ----- layout helpers ----------------------------------------------

    def _update_scrollbar(self) -> None:
        total_h = self._cfg.header_height + len(self._rows) * self._cfg.row_height
        visible_h = self.height()
        self._scrollbar.setRange(0, max(0, total_h - visible_h))
        self._scrollbar.setPageStep(max(1, visible_h))
        self._scrollbar.setSingleStep(self._cfg.row_height // 2)

    def _col_ranges(self) -> list[tuple[int, int]]:
        """Return (left_x, right_x) for each column."""
        ranges: list[tuple[int, int]] = []
        start = 0
        for d in self._dividers:
            ranges.append((start, d))
            start = d
        ranges.append((start, self.width()))
        return ranges

    def _divider_at(self, x: int) -> int:
        """Return divider index if *x* is within drag zone, else -1."""
        zone = 6
        for i, dx in enumerate(self._dividers):
            if abs(x - dx) <= zone:
                return i
        return -1

    # ----- painting ----------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()

        # ── background ──
        painter.fillRect(self.rect(), QColor(self._cfg.background_color))

        hh = self._cfg.header_height

        # ── header ──
        painter.fillRect(0, 0, w, hh, QColor(self._cfg.header_bg_color))
        painter.setPen(QPen(QColor(self._cfg.divider_color), 1))

        col_starts = [0] + self._dividers
        col_labels = ["Branches", "Graph", "Commit Message"]

        for i, (x_start, _label) in enumerate(
            zip(col_starts, col_labels, strict=True),
        ):
            x_end = col_starts[i + 1] if i + 1 < len(col_starts) else w
            # Right border of header column.
            if i < len(self._dividers):
                painter.drawLine(x_end - 1, 0, x_end - 1, hh)
            # Bottom border.
            painter.drawLine(x_start, hh - 1, x_end, hh - 1)

        # Header text.
        painter.setPen(QPen(QColor(self._cfg.text_color)))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        for i, (x_start, label) in enumerate(
            zip(col_starts, col_labels, strict=True),
        ):
            x_end = col_starts[i + 1] if i + 1 < len(col_starts) else w
            avail = x_end - x_start - 12
            if avail < 20:
                continue
            painter.drawText(
                x_start + 6, 0, avail, hh,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
            )

        # ── clip to data area ──
        painter.save()
        painter.setClipRect(0, hh, w, self.height() - hh)

        # ── row hover / selection backgrounds ──
        self._draw_row_backgrounds(painter, hh)

        # ── edges (drawn behind nodes) ──
        self._draw_edges(painter, hh)

        # ── row content (branch chips, nodes, text) ──
        self._draw_row_content(painter, hh)

        painter.restore()

        # ── subtle column guide lines in data area ──
        painter.setPen(QPen(QColor("#2a2a3a"), 1, Qt.PenStyle.DotLine))
        for dx in self._dividers:
            painter.drawLine(dx, hh, dx, self.height())

        # ── divider handles (drawn on top) ──
        for dx in self._dividers:
            rect_w = self._cfg.divider_width
            painter.fillRect(
                dx - rect_w // 2, 0, rect_w, self.height(),
                QColor(self._cfg.divider_color).darker(120),
            )

    def _row_y(self, row: int) -> float:
        return self._cfg.header_height + self._cfg.row_height * row - self._scroll_offset

    def _column_center_x(self) -> float:
        """X-center of the graph (middle) column."""
        return (self._dividers[0] + self._dividers[1]) / 2

    def _column_width(self) -> float:
        return self._dividers[1] - self._dividers[0]

    def _draw_edges(self, painter: QPainter, header_h: int) -> None:
        row_by_sha: dict[str, dict] = {r["sha"]: r for r in self._rows}
        dh = self._cfg.row_height
        r = self._cfg.node_radius
        col_cx = self._column_center_x()
        lane_w = self._cfg.node_radius * 2 + 8

        for row_data in self._rows:
            child_col = row_data.get("display_column", row_data.get("lane", 0))
            child_cx = self._lane_x(child_col, col_cx, lane_w)
            child_cy = self._row_y(row_data["row"]) + dh / 2

            for parent_sha in row_data.get("parents", []):
                parent = row_by_sha.get(parent_sha)
                if parent is None:
                    continue
                parent_col = parent.get("display_column", parent.get("lane", 0))
                parent_cx = self._lane_x(parent_col, col_cx, lane_w)
                parent_cy = self._row_y(parent["row"]) + dh / 2

                path = QPainterPath()
                if abs(child_cx - parent_cx) < 0.5:
                    path.moveTo(child_cx, child_cy + r)
                    path.lineTo(parent_cx, parent_cy - r)
                else:
                    mid_y = (child_cy + parent_cy) / 2
                    cr = 8
                    k = 0.5522847498
                    path.moveTo(child_cx, child_cy)
                    path.lineTo(child_cx, mid_y - cr)
                    if parent_cx > child_cx:
                        path.cubicTo(
                            child_cx, mid_y - cr * (1 - k),
                            child_cx + cr * (1 - k), mid_y,
                            child_cx + cr, mid_y,
                        )
                    else:
                        path.cubicTo(
                            child_cx, mid_y - cr * (1 - k),
                            child_cx - cr * (1 - k), mid_y,
                            child_cx - cr, mid_y,
                        )
                    if parent_cx > child_cx:
                        path.lineTo(parent_cx - cr, mid_y)
                        path.cubicTo(
                            parent_cx - cr * (1 - k), mid_y,
                            parent_cx, mid_y + cr * (1 - k),
                            parent_cx, mid_y + cr,
                        )
                    else:
                        path.lineTo(parent_cx + cr, mid_y)
                        path.cubicTo(
                            parent_cx + cr * (1 - k), mid_y,
                            parent_cx, mid_y + cr * (1 - k),
                            parent_cx, mid_y + cr,
                        )
                    path.lineTo(parent_cx, parent_cy)

                if row_data["sha"] == "WIP":
                    edge_color = QColor(self._cfg.wip_color)
                elif parent_sha == row_data["parents"][0]:
                    edge_color = QColor(row_data["color"])
                else:
                    edge_color = QColor(parent["color"])

                pen = QPen(edge_color, self._cfg.edge_width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawPath(path)

    def _lane_x(self, column: int, center_x: float, lane_w: float) -> float:
        offset = column - 1  # center around column 1
        return center_x + offset * lane_w

    def _draw_row_backgrounds(self, painter: QPainter, header_h: int) -> None:
        dh = self._cfg.row_height
        for row_data in self._rows:
            row = row_data["row"]
            y = self._row_y(row)
            if y + dh < header_h or y > self.height():
                continue
            sha = row_data["sha"]
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

    def _draw_row_content(self, painter: QPainter, header_h: int) -> None:
        dh = self._cfg.row_height
        col_ranges = self._col_ranges()
        col_cx = self._column_center_x()
        lane_w = self._cfg.node_radius * 2 + 8
        fm = self.fontMetrics()

        for row_data in self._rows:
            row = row_data["row"]
            y = self._row_y(row)
            y_center = y + dh / 2

            if y + dh < header_h or y > self.height():
                continue

            self._draw_branch_chips(painter, row_data, col_ranges[0], y_center, fm)
            self._draw_graph_node(painter, row_data, col_cx, lane_w, y_center)
            self._draw_commit_text(painter, row_data, col_ranges[2], y_center, fm)

    def _draw_branch_chips(
        self, painter: QPainter, row_data: dict,
        col_range: tuple[int, int], y_center: float, fm,
    ) -> None:
        branch_refs = row_data.get("branch_refs", [])
        if not branch_refs:
            return

        local_names: set[str] = {b["name"] for b in branch_refs if not b.get("is_remote")}
        visible = [
            b for b in branch_refs
            if not (b.get("is_remote") and b["name"].split("/", 1)[-1] in local_names)
        ]
        if not visible:
            return

        icon_size = self._cfg.branch_icon_size
        pad = 5
        gap = 3
        avatar_size = icon_size + 4
        col_left, col_right = col_range
        avail_w = col_right - col_left - 10
        if avail_w < 40:
            return

        commit_color = QColor(row_data["color"])
        chip_text_color = QColor("#FFFFFF")
        cursor_x = col_left + 6

        for branch in visible:
            is_head = branch.get("is_head")
            is_remote = branch.get("is_remote")
            display = branch["name"]
            if is_remote:
                parts = display.split("/", 1)
                if len(parts) == 2:
                    display = parts[1]

            reserved = (
                pad * 2 + (icon_size + gap if is_head else 0)
                + (gap + icon_size if not is_remote else 0)
                + gap + avatar_size
            )
            max_text_w = max(20, avail_w - (cursor_x - col_left) - reserved)
            display = fm.elidedText(display, Qt.TextElideMode.ElideRight, max_text_w)
            text_w = fm.horizontalAdvance(display)
            text_h = fm.height()

            content_w = pad
            if is_head:
                content_w += icon_size + gap
            content_w += text_w
            if not is_remote:
                content_w += gap + icon_size
            content_w += gap + avatar_size + pad

            chip_h = max(text_h, icon_size, avatar_size) + pad * 2
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
                row_data.get("author_email") or row_data.get("author_name", ""),
                avatar_size,
            )
            painter.drawPixmap(int(inner_x + gap), int(inner_cy - avatar_size / 2), avatar)

            cursor_x += content_w + gap
            if cursor_x > col_right:
                break

    def _draw_graph_node(
        self, painter: QPainter, row_data: dict,
        col_cx: float, lane_w: float, y_center: float,
    ) -> None:
        painter.save()
        column = row_data.get("display_column", row_data.get("lane", 0))
        cx = self._lane_x(column, col_cx, lane_w)
        is_wip = row_data["sha"] == "WIP"
        is_selected = row_data["sha"] == self._selected_sha

        if is_wip:
            color = QColor(self._cfg.wip_color)
            radius = self._cfg.wip_node_radius
            painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(self._cfg.background_color))
            painter.drawEllipse(
                int(cx - radius), int(y_center - radius),
                int(radius * 2), int(radius * 2),
            )
        elif is_selected:
            color = QColor(row_data["color"])
            radius = self._cfg.node_radius
            painter.setBrush(color)
            painter.setPen(QPen(
                QColor(self._cfg.selection_color), self._cfg.selection_ring_width,
            ))
            painter.drawEllipse(
                int(cx - radius), int(y_center - radius),
                int(radius * 2), int(radius * 2),
            )
        else:
            color = QColor(row_data["color"])
            radius = self._cfg.node_radius
            painter.setBrush(color)
            painter.setPen(QPen(color, 0))
            painter.drawEllipse(
                int(cx - radius), int(y_center - radius),
                int(radius * 2), int(radius * 2),
            )

        painter.restore()

        if not is_wip:
            av_size = max(6, int(radius * 2.5 - 8))
            av_pix = self._avatar_for(
                row_data.get("author_email") or row_data.get("author_name", ""),
                av_size, shape="circle",
            )
            painter.drawPixmap(
                int(cx - av_size / 2), int(y_center - av_size / 2), av_pix,
            )

        chip_y = y_center - self._cfg.ref_chip_height / 2 - 2
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
        painter.setPen(QPen(QColor(self._cfg.background_color)))
        painter.setFont(self.font())
        painter.drawText(int(x + pad), int(y + h - 4), label)
        return w, h

    def _draw_commit_text(
        self, painter: QPainter, row_data: dict,
        col_range: tuple[int, int], y_center: float, fm,
    ) -> None:
        col_left, col_right = col_range
        pad_x = 8
        avail_w = col_right - col_left - pad_x * 2
        if avail_w < 20:
            return

        is_wip = row_data["sha"] == "WIP"
        subject = row_data["subject"]
        if fm.horizontalAdvance(subject) > avail_w:
            subject = fm.elidedText(subject, Qt.TextElideMode.ElideRight, avail_w)

        text_color = (
            QColor(self._cfg.dim_text_color) if is_wip
            else QColor(self._cfg.text_color)
        )
        subject_x = col_left + pad_x
        subject_y = int(y_center + fm.ascent() / 2 - 1)

        painter.setPen(QPen(text_color))
        painter.setFont(self.font())
        painter.drawText(subject_x, subject_y, subject)

    # ----- avatars ------------------------------------------------------

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

    # ----- input --------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        x, y = event.pos().x(), event.pos().y()
        hh = self._cfg.header_height

        # Check divider drag.
        di = self._divider_at(x)
        if di >= 0:
            self._dragging_divider = di
            self._drag_start_x = x
            self._drag_start_div = self._dividers[di]
            self.setCursor(Qt.CursorShape.SplitHCursor)
            event.accept()
            return

        # Check click on commit row (in data area).
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

        x = event.pos().x()
        y = event.pos().y()
        hh = self._cfg.header_height

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
        delta = event.angleDelta().y()
        self._scrollbar.setValue(self._scrollbar.value() - delta // 3)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        w = self.width()
        bar_w = self._scrollbar.sizeHint().width()
        self._scrollbar.setGeometry(w - bar_w, 0, bar_w, self.height())
        self._update_scrollbar()

    def _move_divider(self, index: int, new_x: int) -> None:
        if index == 0:
            self._dividers[0] = max(80, min(new_x, self._dividers[1] - 60))
        else:
            self._dividers[1] = max(self._dividers[0] + 60, new_x)

    def _hit_test_commit(self, x: int, y: int) -> str | None:
        """Return SHA if *y* falls within a commit row, else None."""
        hh = self._cfg.header_height
        dh = self._cfg.row_height
        scroll_y = y - hh + self._scroll_offset

        for row_data in self._rows:
            row = row_data["row"]
            row_top = dh * row
            if row_top <= scroll_y < row_top + dh:
                return row_data["sha"]
        return None


__all__ = ["GraphTableWidget", "RenderConfig"]
