"""Commit graph widget: renders the DAG of commits as a custom Qt scene.

Stage 2 implementation. The widget subscribes to a
:class:`src.viewmodels.graph_viewmodel.GraphViewModel` and rebuilds
its :class:`QGraphicsScene` whenever the layout changes.

Rendering layout (per commit at row ``r``, lane ``l``):

* The node is an ellipse of radius :data:`NODE_RADIUS`, centred at
  ``(LANE_OFFSET + l * LANE_WIDTH, ROW_HEIGHT * r + ROW_HEIGHT/2)``.
* The commit subject and short SHA are drawn to the right of the
  node, starting at ``x = lane_centre + LABEL_OFFSET``.
* Ref labels (``HEAD``, branch names, tags) are drawn as small
  rounded chips above the subject line, in the commit's colour.
* Edges to parents are drawn as straight vertical lines when the
  child and parent share a lane, otherwise as an L-shaped path
  that drops down, jogs across at the row midpoint, and continues
  down to the parent.

Selection is purely visual: clicking a node calls
:meth:`GraphViewModel.select_commit`, which propagates as a Qt
signal. The widget keeps a local ``_selected_sha`` so it can
re-paint the selection ring when the layout is rebuilt.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import md5

from PySide6.QtCore import QRectF, Qt
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
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from src.utils.theme import DARK_THEME, Theme
from src.viewmodels.graph_viewmodel import GraphViewModel


@dataclass(frozen=True)
class RenderConfig:
    """Visual constants for the graph widget.

    Spatial dimensions are stable across themes; **colours** default
    to :data:`src.utils.theme.DARK_THEME` so the scene background
    matches the surrounding :class:`QGraphicsView` (which is styled
    by the QSS in :mod:`src.utils.theme`). Stage 9 will introduce
    user-customisable colours; for now the graph respects whatever
    theme the rest of the app uses.
    """

    lane_width: int = 30
    lane_offset: int = 30
    branch_label_width: int = 130
    row_height: int = 43
    node_radius: int = 12
    label_offset: int = 18
    ref_chip_height: int = 14
    ref_chip_padding: int = 6
    ref_chip_gap: int = 4
    background_color: str = DARK_THEME.bg
    text_color: str = DARK_THEME.text
    dim_text_color: str = DARK_THEME.text_dim
    accent_color: str = DARK_THEME.accent
    selection_color: str = DARK_THEME.graph_selection
    edge_color: str = DARK_THEME.graph_edge
    edge_width: int = 2
    selection_ring_width: int = 2
    subject_max_chars: int = 60
    wip_color: str = DARK_THEME.graph_wip
    wip_node_radius: int = 12
    branch_icon_size: int = 12


def _config_for_theme(theme: Theme | None) -> RenderConfig:
    """Return a :class:`RenderConfig` whose colour fields come from ``theme``.

    ``None`` falls back to :data:`DARK_THEME` — the historical default
    that kept the graph looking the same as before the global
    theming pass. Spatial constants are unchanged.
    """
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
    """Thin rounded-line pen for checkmarks and monitor glyphs."""
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


class GraphWidget(QGraphicsView):
    """Renders a :class:`GraphViewModel`'s commit graph.

    The widget is intentionally passive: it reads
    ``graph_updated`` / ``commit_selected`` and forwards clicks via
    ``view_model.select_commit``. It does **not** query the
    repository on its own.

    The optional ``theme`` keyword lets the caller supply a
    :class:`src.utils.theme.Theme`; the scene background, text
    colours, edge colour, selection ring and WIP node colour are all
    pulled from it. Omitting ``theme`` (the historical default) is
    equivalent to passing :data:`DARK_THEME`.
    """

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
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor(self._cfg.background_color))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._node_items: dict[str, QGraphicsEllipseItem] = {}
        self._branch_label_items: dict[str, list[QGraphicsItem]] = {}
        self._selected_sha: str | None = None
        self._highlighted_shas: set[str] = set()
        self._placeholder: QGraphicsSimpleTextItem | None = None
        self._rows: list[dict] = []
        self._avatar_cache: dict[str, QPixmap] = {}

        self._view_model.graph_updated.connect(self._on_graph_updated)
        # Ctrl+Shift+C — copy graph structure to clipboard.
        self._shortcut_copy = QShortcut("Ctrl+Shift+C", self)
        self._shortcut_copy.activated.connect(self._copy_structure)

        # Pull the current state (in case the ViewModel emitted
        # before the widget was wired up — common during tests).
        self._on_graph_updated([])

    # ----- public API ---------------------------------------------------

    def selected_sha(self) -> str | None:
        """Return the SHA of the currently highlighted commit, if any."""
        return self._selected_sha

    def set_selected_sha(self, sha: str | None) -> None:
        """Force a selection without going through the ViewModel click path.

        Useful for tests and for syncing the highlight when the
        :attr:`GraphViewModel.commit_selected` signal fires from
        elsewhere (e.g. keyboard navigation in Stage 4).
        """
        self._selected_sha = sha
        self._refresh_selection_rings()

    def set_highlighted_shas(self, shas: set[str]) -> None:
        """Highlight the nodes with the given SHAs (search results).

        The highlight is a soft lime border around the node ellipse.
        When a node is both selected and highlighted, the selection
        ring wins (selection is the dominant visual state).
        """
        self._highlighted_shas = set(shas)
        self._refresh_selection_rings()
        # Auto-scroll to the first highlighted node.
        if self._highlighted_shas:
            for sha, node in self._node_items.items():
                if sha in self._highlighted_shas:
                    self.centerOn(node)
                    break

    # ----- signal handlers ---------------------------------------------

    def _on_graph_updated(self, rows: list[dict]) -> None:
        """Rebuild the scene from the ViewModel's new layout payload."""
        self._rows = rows
        self._scene.clear()
        self._node_items.clear()
        self._branch_label_items.clear()
        self._placeholder = None

        if not rows:
            self._draw_placeholder("No commits to display")
            return

        # Pass 1: connecting lines (drawn behind nodes).
        row_by_sha: dict[str, dict] = {r["sha"]: r for r in rows}
        for row in rows:
            for parent_sha in row["parents"]:
                parent = row_by_sha.get(parent_sha)
                if parent is None:
                    continue
                self._draw_edge(
                    child=row,
                    parent=parent,
                )

        # Pass 2: nodes, labels, and ref chips.
        for row in rows:
            self._draw_commit(row)

        # Pass 3: extend scene rect to fit everything.
        rect = self._scene.itemsBoundingRect().adjusted(
            -self._cfg.lane_offset, -self._cfg.row_height,
            self._cfg.lane_offset, self._cfg.row_height,
        )
        self._scene.setSceneRect(rect)
        self._refresh_selection_rings()

    # ----- drawing helpers ---------------------------------------------

    def _draw_placeholder(self, message: str) -> None:
        text = QGraphicsSimpleTextItem(message)
        text.setBrush(QColor(self._cfg.dim_text_color))
        self._scene.addItem(text)
        self._placeholder = text
        rect = self.viewport().rect()
        text.setPos(
            (rect.width() - text.boundingRect().width()) / 2,
            (rect.height() - text.boundingRect().height()) / 2,
        )
        self._scene.setSceneRect(self._scene.itemsBoundingRect())

    def _row_y(self, row: int) -> float:
        return self._cfg.row_height * row + self._cfg.row_height / 2

    def _lane_x(self, lane: int) -> float:
        """Return the scene-x of the centre of ``lane``.

        Lane 0 sits to the right of the left-hand branch-label column;
        the gap (``lane_offset``) keeps the leftmost node visually
        clear of the labels.
        """
        return (
            self._cfg.branch_label_width
            + self._cfg.lane_offset
            + lane * self._cfg.lane_width
        )

    def _draw_commit(self, row: dict) -> None:
        x = self._lane_x(row["lane"])
        y = self._row_y(row["row"])
        is_wip = row["sha"] == "WIP"
        if is_wip:
            color = QColor(self._cfg.wip_color)
            radius = self._cfg.wip_node_radius
            pen = QPen(color, 1, Qt.PenStyle.SolidLine)
            brush = QBrush(QColor(self._cfg.background_color))
        else:
            color = QColor(row["color"])
            radius = self._cfg.node_radius
            pen = QPen(QColor(self._cfg.background_color), 1)
            brush = QBrush(color)

        # Node ellipse.
        node = QGraphicsEllipseItem(
            x - radius,
            y - radius,
            radius * 2,
            radius * 2,
        )
        node.setBrush(brush)
        node.setPen(pen)
        node.setData(0, row["sha"])  # tag the item with its SHA for hit-testing
        node.setCursor(Qt.CursorShape.PointingHandCursor)
        self._scene.addItem(node)
        self._node_items[row["sha"]] = node

        # Author avatar inside the commit node (skip WIP).
        if not is_wip:
            av_size = max(10, radius * 2 - 8)
            av_pix = self._avatar_for(
                row.get("author_email") or row.get("author_name", ""),
                av_size,
                shape="circle",
            )
            av_item = QGraphicsPixmapItem(av_pix)
            av_item.setPos(x - av_size / 2, y - av_size / 2)
            av_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self._scene.addItem(av_item)

        # Branch labels drawn in the column to the left of the graph.
        # Branches (with their ``is_head`` / ``is_remote`` flags) are
        # rendered here so the user sees the branch name next to the
        # commit it points at. When a branch is also the currently
        # checked-out one, a checkmark is drawn to its left; local
        # branches get a small monitor glyph to their right.
        self._draw_branch_labels(row, y)

        # Ref chips (HEAD / tag) — drawn just above the subject.
        # Branches are no longer listed here; they live in the left
        # column. ``HEAD`` is suppressed whenever an ``is_head``
        # branch ref is already shown — the checkmark next to the
        # branch name conveys the same information.
        label_x = x + self._cfg.label_offset
        chip_y = y - self._cfg.ref_chip_height / 2 - 1
        has_head_branch = any(b.get("is_head") for b in row.get("branch_refs", []))
        for ref_label in row["refs"]:
            if ref_label == "HEAD" and has_head_branch:
                continue
            chip = self._draw_ref_chip(ref_label, label_x, chip_y, color)
            label_x += chip.rect().width() + self._cfg.ref_chip_gap

        # Subject line and short SHA.
        text_x = max(
            x + self._cfg.label_offset,
            label_x + (0 if row["refs"] else 0),
        )
        subject = row["subject"]
        if len(subject) > self._cfg.subject_max_chars:
            subject = subject[: self._cfg.subject_max_chars - 1] + "…"
        subject_item = QGraphicsSimpleTextItem(subject)
        subject_color = QColor(self._cfg.dim_text_color) if is_wip else QColor(self._cfg.text_color)
        subject_item.setBrush(subject_color)
        subject_item.setFont(self.font())
        subject_item.setPos(text_x, y - subject_item.boundingRect().height() / 2)
        self._scene.addItem(subject_item)

        sha_item = QGraphicsSimpleTextItem(row["short_sha"])
        sha_item.setBrush(QColor(self._cfg.dim_text_color))
        sha_item.setFont(self.font())
        sha_item.setPos(text_x, y + 4)
        self._scene.addItem(sha_item)

    def _draw_branch_labels(self, row: dict, y: float) -> list[QGraphicsItem]:
        """Render the branch-name column to the left of the graph.

        One coloured chip per branch. Inside the chip (left to right):
        checkmark (if active), branch name, monitor (if local), and
        an identicon avatar for the commit author — all in the same
        contrasting colour as the label text.

        When a local branch and one or more remote-tracking refs
        share the same display name (e.g. ``main`` + ``origin/main``
        both pointing at the same commit), the remote refs are
        suppressed so the name appears only once. The monitor icon
        on the local chip already conveys the "local" information.
        """
        items: list[QGraphicsItem] = []
        branch_refs = row.get("branch_refs", [])
        self._branch_label_items[row["sha"]] = items
        if not branch_refs:
            return items

        # Collect the set of local-branch names so we can skip
        # remote-tracking refs whose display name would be a
        # duplicate of a local one already drawn.
        local_names: set[str] = {
            b["name"] for b in branch_refs if not b.get("is_remote")
        }

        icon_size = 10
        pad = 5
        gap = 3
        avatar_size = icon_size + 4
        commit_color = QColor(row["color"])
        chip_text_color = QColor("#FFFFFF")

        fm = self.fontMetrics()
        avatar = self._avatar_for(
            row.get("author_email") or row.get("author_name", ""), avatar_size,
        )

        column_margin = 6
        cursor_x = column_margin

        for branch in branch_refs:
            is_head = branch.get("is_head")
            is_remote = branch.get("is_remote")
            display = branch["name"]
            if is_remote:
                parts = display.split("/", 1)
                if len(parts) == 2:
                    display = parts[1]
                if display in local_names:
                    continue  # suppress — local variant already covers this name

            # Elide if needed.
            reserved = pad * 2 + (icon_size + gap if is_head else 0) + \
                       (gap + icon_size if not is_remote else 0) + \
                       gap + avatar_size
            max_text_w = self._cfg.branch_label_width - column_margin - reserved
            if max_text_w < 20:
                max_text_w = 60
            if fm.horizontalAdvance(display) > max_text_w:
                display = fm.elidedText(
                    display, Qt.TextElideMode.ElideRight, max_text_w,
                )

            text_w = fm.horizontalAdvance(display)

            # Chip layout: [ck?] [name] [mon?] [avatar]
            content_w = pad
            if is_head:
                content_w += icon_size + gap
            content_w += text_w
            if not is_remote:
                content_w += gap + icon_size
            content_w += gap + avatar_size + pad

            # Высота подложки ветки = диаметр ноды коммита
            chip_h = self._cfg.node_radius * 2
            chip_center_y = chip_h / 2

            # Rounded-rect chip.
            chip_path = QPainterPath()
            chip_path.addRoundedRect(QRectF(0, 0, content_w, chip_h), 4, 4)
            chip = QGraphicsPathItem(chip_path)
            chip.setBrush(QBrush(commit_color))
            chip.setPen(QPen(commit_color, 0))
            chip.setPos(cursor_x, y - chip_center_y)
            self._scene.addItem(chip)
            items.append(chip)

            inner_x = pad

            # Checkmark.
            if is_head:
                ck = self._make_icon_checkmark(icon_size)
                ck.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                ck.setPen(_icon_pen(chip_text_color, 1.6))
                ck.setParentItem(chip)
                ck.setPos(inner_x, chip_center_y - icon_size / 2)
                inner_x += icon_size + gap

            # Branch name.
            text_item = QGraphicsSimpleTextItem(display)
            text_item.setBrush(chip_text_color)
            text_item.setFont(self.font())
            text_item.setParentItem(chip)
            text_item.setPos(
                inner_x,
                chip_center_y - text_item.boundingRect().height() / 2
                - text_item.boundingRect().top(),
            )
            inner_x += text_w

            # Monitor icon for local branches.
            if not is_remote:
                mn = self._make_icon_monitor(icon_size)
                mn.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                mn.setPen(_icon_pen(chip_text_color, 1.2))
                mn.setParentItem(chip)
                mn.setPos(inner_x + gap, chip_center_y - icon_size / 2)
                inner_x += gap + icon_size

            # Author identicon.
            av_item = QGraphicsPixmapItem(avatar)
            av_item.setParentItem(chip)
            av_item.setPos(inner_x + gap, chip_center_y - avatar_size / 2)

            cursor_x += content_w + gap

        self._branch_label_items[row["sha"]] = items
        return items

    # ----- icon helpers (unparented, local coords) ---------------------

    @staticmethod
    def _make_icon_checkmark(size: int) -> QGraphicsPathItem:
        """Checkmark path in local ``(0, 0) .. (size, size)`` coords."""
        path = QPainterPath()
        path.moveTo(0, size * 0.55)
        path.lineTo(size * 0.32, size * 0.92)
        path.lineTo(size, size * 0.08)
        return QGraphicsPathItem(path)

    @staticmethod
    def _make_icon_monitor(size: int) -> QGraphicsPathItem:
        """Monitor glyph path in local ``(0, 0) .. (size, size)`` coords."""
        path = QPainterPath()
        screen_h = size * 0.7
        path.addRoundedRect(QRectF(0, 0, size, screen_h), 1.2, 1.2)
        nx = size / 2
        ntop = screen_h
        nbot = screen_h + size * 0.18
        path.moveTo(nx, ntop)
        path.lineTo(nx, nbot)
        base_h = size * 0.32
        path.moveTo(nx - base_h, nbot)
        path.lineTo(nx + base_h, nbot)
        return QGraphicsPathItem(path)

    # ----- avatars ------------------------------------------------------

    # A small palette of semi-transparent colours for user-avatar circles.
    # Picked to look distinct from the 12 branch palette colours.
    _AVATAR_COLORS: tuple[str, ...] = (
        "#C44A2B", "#B85C8C", "#9A6E3A", "#5B7FA5",
        "#8B5CF6", "#3B82A0", "#D97706", "#6D8EA0",
    )

    def _avatar_for(
        self, seed: str, size: int = 14, *, shape: str = "square",
    ) -> QPixmap:
        """Return a small identicon pixmap (5×5 mirrored-block pattern).

        ``shape`` can be ``"square"`` (rounded rect clip) or
        ``"circle"`` (circular clip).
        """
        if not seed:
            seed = "?"
        cache_key = f"{seed}_{size}_{shape}"
        if cache_key not in self._avatar_cache:
            h_bytes = md5(seed.encode()).digest()  # noqa: S324
            fg = QColor(self._AVATAR_COLORS[h_bytes[0] % len(self._AVATAR_COLORS)])
            bg = QColor("#F4F4F4")

            # 5×5 symmetric grid — left 3 columns from hash, right 2
            # are the horizontal mirror.
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

            # Clip to the requested shape.
            clip = QPainterPath()
            margin = 1.0
            d = size - margin * 2
            if shape == "circle":
                clip.addEllipse(QRectF(margin, margin, d, d))
            else:
                clip.addRoundedRect(QRectF(margin, margin, d, d), 3, 3)
            painter.setClipPath(clip)

            # Background.
            painter.setBrush(QBrush(bg))
            painter.setPen(QPen(Qt.PenStyle.NoPen))
            painter.drawRect(QRectF(0, 0, size, size))

            # Filled cells.
            painter.setBrush(QBrush(fg))
            for row in range(5):
                for col in range(5):
                    if grid[row][col]:
                        painter.drawRect(
                            QRectF(col * cell, row * cell, cell, cell),
                        )

            # Outline.
            painter.setClipping(False)
            outline = QPen(fg.darker(120), 1)
            painter.setPen(outline)
            painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
            if shape == "circle":
                painter.drawEllipse(QRectF(margin, margin, d, d))
            else:
                painter.drawRoundedRect(QRectF(margin, margin, d, d), 3, 3)

            painter.end()
            self._avatar_cache[cache_key] = pix
        return self._avatar_cache[cache_key]

    def _draw_ref_chip(
        self, label: str, x: float, y: float, color: QColor,
    ) -> QGraphicsRectItem:
        text = QGraphicsSimpleTextItem(label)
        text.setBrush(QColor(self._cfg.text_color))
        text.setFont(self.font())
        # First lay out the text to measure it, then place the chip.
        chip_padding = self._cfg.ref_chip_padding
        chip_height = self._cfg.ref_chip_height
        width = text.boundingRect().width() + chip_padding * 2
        chip = QGraphicsRectItem(QRectF(x, y, width, chip_height))
        chip.setBrush(QBrush(color))
        chip.setPen(QPen(color, 0))
        self._scene.addItem(chip)
        text.setParentItem(chip)
        text.setPos(
            (width - text.boundingRect().width()) / 2,
            (chip_height - text.boundingRect().height()) / 2 + 2,
        )
        return chip

    def _draw_edge(self, child: dict, parent: dict) -> None:
        cx, cy = self._lane_x(child["lane"]), self._row_y(child["row"])
        px, py = self._lane_x(parent["lane"]), self._row_y(parent["row"])
        r = self._cfg.node_radius
        if abs(cx - px) < 0.5:
            # Straight vertical: from below the child to above the parent.
            path = QPainterPath()
            path.moveTo(cx, cy + r)
            path.lineTo(px, py - r)
        else:
            # Rounded L-shape: from child center, with rounded corners, to parent center.
            mid_y = (cy + py) / 2
            cr = 8
            k = 0.5522847498
            path = QPainterPath()
            path.moveTo(cx, cy)
            path.lineTo(cx, mid_y - cr)
            if px > cx:
                path.cubicTo(
                    cx, mid_y - cr * (1 - k),
                    cx + cr * (1 - k), mid_y,
                    cx + cr, mid_y,
                )
            else:
                path.cubicTo(
                    cx, mid_y - cr * (1 - k),
                    cx - cr * (1 - k), mid_y,
                    cx - cr, mid_y,
                )
            if px > cx:
                path.lineTo(px - cr, mid_y)
                path.cubicTo(
                    px - cr * (1 - k), mid_y,
                    px, mid_y + cr * (1 - k),
                    px, mid_y + cr,
                )
            else:
                path.lineTo(px + cr, mid_y)
                path.cubicTo(
                    px + cr * (1 - k), mid_y,
                    px, mid_y + cr * (1 - k),
                    px, mid_y + cr,
                )
            path.lineTo(px, py)
        if child["sha"] == "WIP":
            edge_color = QColor(self._cfg.wip_color)
        elif parent["sha"] == child["parents"][0]:
            # First parent, same lineage or a branch-off: the child's
            # branch colour is the one that defines this edge.
            edge_color = QColor(child["color"])
        else:
            # Merge from a different branch: the parent's colour
            # represents the branch being merged in.
            edge_color = QColor(parent["color"])
        pen = QPen(edge_color, self._cfg.edge_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setZValue(-1)  # lines below nodes
        self._scene.addItem(item)

    def _copy_structure(self) -> None:
        """Copy a human-readable graph layout to the clipboard (Ctrl+Shift+C)."""
        if not self._rows:
            return
        lines: list[str] = []
        for row in self._rows:
            branch_lines: list[str] = []
            for b in row.get("branch_refs", []):
                flags = ", ".join(
                    f for f in ("head" if b.get("is_head") else None,
                                "local" if not b.get("is_remote") else "remote")
                    if f
                )
                branch_lines.append(f"  {b['name']} ({flags})")
            branch_block = "\n".join(branch_lines) if branch_lines else "  (none)"
            parents = ", ".join(p[:7] for p in row.get("parents", [])) or "(root)"
            lines.append(
                f"Row {row['row']:>3} | {row['sha'][:12]:>12} | lane {row['lane']} | "
                f"{row['color']} | refs={row.get('refs', [])!r} | parents={parents}\n"
                f"{branch_block}"
            )
        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        parent = self.parentWidget()
        if parent is not None:
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(
                self.mapToGlobal(self.rect().center()),
                f"Copied {len(self._rows)} rows to clipboard",
                self,
            )

    def _refresh_selection_rings(self) -> None:
        for sha, node in self._node_items.items():
            if sha == self._selected_sha:
                node.setPen(
                    QPen(QColor(self._cfg.selection_color), self._cfg.selection_ring_width),
                )
            elif sha in self._highlighted_shas:
                node.setPen(QPen(QColor("#A3BE8C"), 2))
            else:
                node.setPen(QPen(QColor(self._cfg.background_color), 1))

    # ----- input --------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            # Only accept clicks on the actual node ellipse, not the
            # chip or label, so we can use a generous bounding box
            # based on the node's rect.
            for sha, node in self._node_items.items():
                if node.rect().adjusted(-4, -4, 4, 4).contains(
                    node.mapFromScene(scene_pos),
                ):
                    self._selected_sha = sha
                    self._refresh_selection_rings()
                    self._view_model.select_commit(sha)
                    event.accept()
                    return
        super().mousePressEvent(event)

    # ----- layout on resize --------------------------------------------

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if self._placeholder is not None:
            rect = self.viewport().rect()
            bounds = self._placeholder.boundingRect()
            self._placeholder.setPos(
                (rect.width() - bounds.width()) / 2,
                (rect.height() - bounds.height()) / 2,
            )


__all__ = ["GraphWidget", "RenderConfig"]
