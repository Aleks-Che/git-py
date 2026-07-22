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

Branch chip interaction
-----------------------
The leftmost column carries the branch chips (one per ref pointing
at the row's commit). Clicks **on** a chip are routed to the branch
verbs (double-click → checkout, right-click → context menu with
"Merge X into Y" + "Rebase X onto Y"); clicks elsewhere in the row
keep the historical commit-selection behaviour. The hit-test lives
in :meth:`_branch_chip_at` and relies on the rect cache populated
during :meth:`_draw_branch_chips`.

Branch chip drag-and-drop
-------------------------
A press-and-drag on a branch chip starts a native :class:`QDrag` that
carries the chip's full ref name as text payload. Dropping the chip
on **another** branch chip opens a context menu with "Merge
{source} into {target}" and "Rebase {source} onto {target}"; this
mirrors the left panel's drag-and-drop semantics and is the
graph-side counterpart to the chip right-click menu (which always
targets the current HEAD). ``drag_start_threshold_px`` defines how
far the cursor has to move before a press is promoted to a drag —
this keeps short clicks on chips from accidentally starting a
drag. ``chip_drag_mime`` is the custom MIME type the chip uses so
the drop handler can distinguish branch drags from any other drop
the widget might one day accept.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QPointF, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QScrollBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.graph_v2 import BRANCH_PALETTE, UNCOMMITTED_COLOR_INDEX, _pick_branch_color
from src.utils.avatar import make_avatar_pixmap
from src.utils.theme import DARK_THEME, Theme
from src.viewmodels.graph_viewmodel import GraphViewModel

# Custom MIME type for branch-chip drags. Using a non-empty custom
# type alongside the plain-text fallback lets the drop handler tell a
# chip drag from any other drag the widget might one day accept (a
# future "drag a file from a commit row to the terminal" gesture, for
# example). The plain text is also set so external consumers — the
# OS clipboard, an ``xdnd`` listener in another app — still get a
# useful payload.
_CHIP_MIME = "application/x-git-py-branch-chip"

# Press-to-drag promotion threshold, in widget pixels. Anything
# shorter is treated as a click (single / double), anything longer
# starts a :class:`QDrag`. ``QStyle`` exposes ``startDragDistance``
# which is the platform default, but the cell-sized chips benefit
# from a slightly lower threshold so the user does not have to
# fling the cursor to start a drag.
_DRAG_START_THRESHOLD_PX = 6

# Delay (ms) between the cursor parking on a multi-branch chip and
# the branch-stack popup appearing. A non-zero value prevents the
# popup from flickering on / off when the cursor whips across a
# column of chips; ``QStyle.PM_ToolTipLabelDelay`` (typically 700ms)
# is too sluggish for a list the user expects to scan quickly, so
# we pick a snappier default.
_HOVER_POPUP_DELAY_MS = 220


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
    0: "EMPTY",
    1: "PIPE",
    2: "COMMIT",
    3: "BRANCH_RIGHT",
    4: "BRANCH_LEFT",
    5: "MERGE_RIGHT",
    6: "MERGE_LEFT",
    7: "HORIZONTAL",
    8: "HORIZONTAL_PIPE",
    9: "TEE_RIGHT",
    10: "TEE_LEFT",
    11: "TEE_UP",
    12: "CROSS",
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
_T_CROSS = 12


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
    stash_push_requested = Signal(str)
    discard_changes_requested = Signal(str)
    # "Create Branch Here" gesture: the user picked a context-menu
    # item on a branch chip with an optional pre-typed name. The
    # host window (``MainWindow``) routes this to
    # ``MainViewModel.create_branch(name, target_sha=sha)``. ``name``
    # is empty when the user simply picked the menu item and the
    # inline editor is what collects the actual text; the signal
    # fires only when the editor commits (Enter pressed).
    create_branch_here_requested = Signal(str, str)  # sha, name
    branch_chip_hover_changed = Signal(str, bool)  # (row_sha, is_hovered)
    # Branch chip signals — emitted from clicks on the leftmost column's
    # branch chips. ``name`` is the ref name as the user sees it
    # (``"main"`` for ``refs/heads/main``, ``"base_features"`` for
    # ``refs/remotes/origin/base_features``). ``checkout_branch_requested``
    # fires on double-click; ``merge_branch_requested`` and
    # ``rebase_branch_requested`` fire from the context menu and carry
    # the source branch (``name``) and the current HEAD as the target.
    checkout_branch_requested = Signal(str)
    merge_branch_requested = Signal(str, str)  # source, target
    rebase_branch_requested = Signal(str, str)  # source, target
    # "Copy branch name" / "Copy commit sha" — emitted from the
    # branch-chip context menu. ``name`` is the chip's *full* ref
    # name (``"main"`` for a local chip, ``"origin/main"`` for a
    # remote chip) so the receiving slot can route the value to
    # ``MainViewModel.copy_to_clipboard`` without having to re-derive
    # the prefix. ``sha`` is the commit the chip points at (the
    # row's SHA); the slot forwards it through the same clipboard
    # helper. Mirrors the equivalent actions on the left panel.
    copy_branch_name_requested = Signal(str)
    copy_commit_sha_requested = Signal(str)
    # Drop signal — fires when the user drops one branch chip on
    # another.  Both ``source`` and ``target`` carry the chip's
    # *display* name (the user-visible label) so the ``MainWindow``
    # wiring can pass them straight to ``merge_branch`` /
    # ``rebase_branch`` without having to resolve ``refs/heads/`` or
    # ``refs/remotes/`` prefixes first.
    branch_dropped_on_branch = Signal(str, str)  # source, target

    _AVATAR_COLORS: tuple[str, ...] = (
        "#C44A2B",
        "#B85C8C",
        "#9A6E3A",
        "#5B7FA5",
        "#8B5CF6",
        "#3B82A0",
        "#D97706",
        "#6D8EA0",
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
        # Branch chip geometry cache, repopulated on every paint.  Keyed
        # by ``(row_sha, display)`` so two commits can each have a
        # branch with the same display name (e.g. ``main`` at HEAD and
        # ``main`` at an older commit that a feature branch forked
        # from) without colliding; within a single row the
        # local-vs-remote suppression in :meth:`_draw_branch_chips`
        # guarantees at most one entry per display name, so callers
        # that look up by ``(sha, display)`` always get the chip the
        # user can actually see and click on.
        #
        # Each value is a dict with the chip's :class:`QRect` (in
        # content coordinates — x needs ``_h_scrolls[0]`` added back
        # at hit-test time) plus a few flags (``is_remote``,
        # ``is_head``) for callers that want to customise the menu
        # per branch kind.
        self._branch_chip_rects: dict[tuple[str, str], dict] = {}

        # Drag state for press-and-drag on a branch chip. The widget
        # only starts a :class:`QDrag` once the cursor has moved more
        # than ``_DRAG_START_THRESHOLD_PX`` from the press point, so
        # short clicks are still treated as clicks.  When a drag is
        # in flight, ``_drag_active_chip`` carries the chip dict
        # (display + full_name + flags) so the drop handler can read
        # the source without re-running the hit-test.
        self._drag_press_chip: dict | None = None
        self._drag_press_pos: QPoint | None = None
        self._drag_active_chip: dict | None = None

        self._dividers: list[int] = [180, 500]
        self._dragging_divider: int = -1
        self._drag_start_x: int = 0
        self._drag_start_div: int = 0

        # Branches created in the current session — used to rank
        # branches that share a commit (a just-created branch keeps
        # a lower visual priority so the *source* branch keeps the
        # prominent chip). Refreshed via
        # :attr:`MainViewModel.recently_created_changed`. Cleared
        # automatically when the VM emits an empty set on repo change.
        self._recently_created_branches: set[str] = set()

        # Row SHAs whose branch group is currently in "expanded"
        # state (showing a hover-popup with all branches at this
        # commit). Tracks only the SHA — the popup is built on demand
        # from the latest ``_rows`` snapshot so the data is always
        # live even after a `graph_updated` rebuild.
        self._expanded_branch_rows: set[str] = set()

        # Inline branch-name editor that pops up over a chip when
        # the user picks "Create Branch Here". Owned by the widget
        # so we can delete/close it on layout rebuilds.
        self._inline_editor: QLineEdit | None = None
        self._inline_editor_row_sha: str | None = None
        self._inline_editor_anchor: QRect | None = None

        # Hover popup showing all branches at a commit. There is at
        # most one popup at a time; selecting a branch (or clicking
        # outside / moving the cursor away) hides it again. The
        # popup is a separate toplevel window so it can render over
        # the divider and the graph without clipping.
        # Forward reference: ``BranchStackPopup`` is defined further
        # down in the same module. Keep the annotation as a quoted
        # string so the name is resolved lazily — that way the
        # forward reference works under ``from __future__ import
        # annotations`` without needing ``if TYPE_CHECKING``.
        self._branch_popup: "BranchStackPopup | None" = None  # noqa: UP037
        self._branch_popup_row_sha: str | None = None
        self._branch_popup_anchor: QRect | None = None

        # Debounce timer for "self-expanding list" hover behaviour:
        # the popup only opens after the user has paused on a
        # multi-branch chip for ``_HOVER_POPUP_DELAY_MS`` ms.  We
        # store the candidate chip + row while we wait, so the
        # timer slot knows what to show when it fires.
        self._popup_show_timer = QTimer(self)
        self._popup_show_timer.setSingleShot(True)
        self._popup_show_timer.setInterval(_HOVER_POPUP_DELAY_MS)
        self._popup_show_timer.timeout.connect(self._on_hover_popup_timer)
        self._popup_hover_chip: dict | None = None
        self._popup_hover_row_sha: str | None = None

        self._scrollbar = QScrollBar(Qt.Orientation.Vertical, self)
        self._scrollbar.valueChanged.connect(self._on_scroll)
        self._scrollbar.setRange(0, 0)

        # R3.1 (P2): a small overlay label that surfaces the
        # "showing N of M (Load more)" indicator when the visible
        # history is smaller than the full DAG.  The label is a
        # real QWidget child so it participates in the focus
        # chain and respects the theme's text colour.  We hide it
        # by default and only show it when
        # :attr:`GraphViewModel.truncated_count` is positive — see
        # :meth:`_on_graph_updated` for the wiring.
        self._truncation_label = QLabel("", self)
        self._truncation_label.setStyleSheet(
            f"color: {DARK_THEME.text_dim}; padding: 0 8px;",
        )
        self._truncation_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self._truncation_label.hide()

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

        # Accept drag-and-drop payloads. ``QWidget`` defaults to
        # ``acceptDrops=False``; without this call Qt's drag pipeline
        # never delivers ``dragEnterEvent`` / ``dropEvent`` to the
        # widget, so the press on a branch chip starts a drag but
        # the drop is silently ignored — the user sees the cursor
        # change but no menu appears on release.  ``setAcceptDrops``
        # must be called before the first drag enters the widget's
        # bounds for the very first time; the constructor is the
        # right place.
        self.setAcceptDrops(True)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        self._view_model.graph_updated.connect(self._on_graph_updated)
        self._view_model.commit_selected.connect(self._on_external_select)
        self._view_model.scroll_to_commit_requested.connect(self.scroll_to_commit)

        # ``MainViewModel`` notifies us whenever a branch is created
        # so the chip-priority logic can demote it; the signal is
        # forwarded through :class:`GraphViewModel` so this widget
        # does not need a direct reference to ``MainViewModel``.
        rcc_signal = getattr(self._view_model, "recently_created_changed", None)
        if rcc_signal is not None:
            rcc_signal.connect(self._on_recently_created_changed)

        # Debug graph dumps are available only through explicit tooling;
        # do not ship a production keyboard shortcut for them.

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
                    self._cfg.header_height + idx * self._cfg.row_height + self._cfg.row_height // 2
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
        # The chip rect cache is repopulated by ``_draw_branch_chips``
        # during the next paint; the old entries are stale because
        # the row count, lane order and the chips themselves change
        # whenever the graph is rebuilt.
        self._branch_chip_rects.clear()
        # A graph rebuild typically means the row the user was about
        # to act on has moved or disappeared (e.g. after a successful
        # ``Create Branch Here``); close any overlay UI that was
        # anchored to a row that may no longer exist.
        self._close_inline_editor()
        self._hide_branch_popup()
        # R3.1 (P2): refresh the "showing N of M" indicator.  The
        # visible-row count is just ``len(rows)`` (rows are filtered
        # before emission); the truncated count lives on the
        # ViewModel.  ``truncated_count == 0`` hides the label.
        self._refresh_truncation_label()
        self._update_scrollbar()
        self.update()

    def _refresh_truncation_label(self) -> None:
        """Show / hide / update the R3.1 truncation indicator.

        Reads :attr:`GraphViewModel.truncated_count`; renders a
        ``"showing N of M (Load more)"`` string when the count is
        positive, hides the label otherwise.  The (out-of-scope
        for R3.1) "Load more" button is presented as plain text
        so the visual contract is already correct — wiring the
        button is a follow-up.
        """
        vm = self._view_model
        truncated = 0
        history_limit = 500
        try:
            truncated = int(getattr(vm, "truncated_count", 0) or 0)
            history_limit = int(getattr(vm, "history_limit", history_limit) or history_limit)
        except (TypeError, ValueError):
            # Defensive: a custom VM that does not implement the
            # new contract is treated as "no truncation", which
            # is the same as the pre-R3.1 behaviour.
            truncated = 0
        visible = min(len(self._rows), history_limit)
        if truncated <= 0:
            self._truncation_label.hide()
            return
        total = visible + truncated
        self._truncation_label.setText(
            f"showing {visible} of {total} (Load more)",
        )
        self._truncation_label.adjustSize()
        # Re-anchor in the rightmost header column so the label
        # stays glued to the top-right corner after every layout
        # pass.
        self._layout_truncation_label()
        self._truncation_label.show()

    def _layout_truncation_label(self) -> None:
        """Position :attr:`_truncation_label` in the rightmost header cell.

        Called from :meth:`_refresh_truncation_label` and from
        :meth:`resizeEvent` so the label tracks both the column
        divider drags and the OS window-resize.
        """
        hh = self._cfg.header_height
        # Anchor to the rightmost column (Commit Message).  Leave a
        # small right margin so the text does not run into the
        # vertical scrollbar (the bar is hidden on wide widgets
        # but reserving the space is cheaper than querying its
        # geometry every paint).
        if len(self._dividers) >= 2:
            left = self._dividers[1]
            right = self.width() - 4
        else:
            left = 0
            right = self.width() - 4
        width = max(50, right - left)
        # The label sits on top of the column-label text drawn in
        # :meth:`paintEvent`, so we keep the height equal to the
        # header row and let ``AlignVCenter`` do the vertical
        # centring for us.
        self._truncation_label.setGeometry(left, 0, width, hh)

    def _on_scroll(self, value: int) -> None:
        self._scroll_offset = value
        self.update()

    def _on_h_scroll(self, col: int, value: int) -> None:
        self._h_scrolls[col] = value
        # The branch-stack popup is anchored to a chip's content-space
        # rect but is rendered as a toplevel window at widget coords.
        # Once the chip column scrolls, the popup's anchor point is
        # stale — close it rather than render it floating in mid-air.
        if col == 0 and self._branch_popup is not None:
            self._hide_branch_popup()
        # The inline ``Create Branch Here`` editor is also anchored in
        # content coordinates; if the user scrolls the chip column
        # while typing the editor would drift away from the chip.
        # Hide it the moment a horizontal scroll begins — the user
        # can re-open the menu if they still want to create a branch.
        if col == 0 and self._inline_editor is not None:
            self._close_inline_editor()
        self.update()

    def _on_external_select(self, sha: str) -> None:
        self._selected_sha = sha
        self.update()

    def _on_recently_created_changed(self, names: set) -> None:
        """Refresh the cached session-creation set and redraw.

        Empty payload (the MainViewModel clears the set on
        ``set_repository``) means "forget everything", which is the
        cue for the chip renderer to fall back to name-based
        ordering for every branch.
        """
        self._recently_created_branches = set(names or set())
        # Hide any hover-popup we were showing — branches inside
        # may have moved in the priority ordering and the popup
        # contents would now be stale.
        self._hide_branch_popup()
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
        self,
        ranges: list[tuple[int, int]],
        bar_h: int,
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
                pad * 2
                + (icon_size + gap if branch.get("is_head") else 0)
                + (gap + icon_size if not branch.get("is_remote") else 0)
                + gap
                + avatar_size
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
        x, y = position.x(), position.y()
        chip = self._branch_chip_at(x, y)
        if chip is not None:
            self._show_branch_context_menu(chip, position)
            return
        sha = self._hit_test_commit(x, y)
        if sha is None:
            return
        row_data = self._row_by_sha(sha)
        kind = _row_kind(row_data) if row_data else "commit"

        menu = self._build_node_menu(sha, kind)
        menu.exec(self.mapToGlobal(position))

    def _build_node_menu(self, sha: str, kind: str) -> QMenu:
        """Build the :class:`QMenu` for a node row (commit/stash/WIP).

        Exposed (single-underscore) so tests can inspect the menu
        synchronously without running ``QMenu.exec()`` (which would
        block on user input). Mirrors the contract of
        :meth:`_build_branch_menu_actions`: the helper produces a real
        ``QMenu`` parented to ``self`` and tests can read its
        :meth:`QMenu.actions` list (which excludes separators, so
        assertions simply look up the labels they care about).

        ``kind`` is one of ``"stash"``, ``"wip"`` or ``"commit"`` (the
        default). The signal-payload contract per row kind:

        * ``stash`` — :attr:`stash_apply_requested` /
          :attr:`stash_pop_requested` / :attr:`stash_drop_requested` /
          :attr:`copy_diff_requested` /
          :attr:`copy_commit_sha_requested` (the row's real OID —
          stash entries are backed by real commits).
        * ``wip`` — :attr:`stash_push_requested` (new; pushed onto
          the undo stack) / :attr:`discard_changes_requested` /
          :attr:`copy_diff_requested`. The WIP marker has no real
          SHA so the "Copy SHA" verb is intentionally absent.
        * ``commit`` — :attr:`checkout_commit_requested` /
          :attr:`copy_diff_requested` /
          :attr:`copy_commit_sha_requested` (the row's full SHA).
        """
        menu = QMenu(self)
        if kind == "stash":
            apply_action = menu.addAction("Apply Stash")
            apply_action.triggered.connect(
                lambda checked=False, s=sha: self.stash_apply_requested.emit(s),
            )
            pop_action = menu.addAction("Pop Stash")
            pop_action.triggered.connect(
                lambda checked=False, s=sha: self.stash_pop_requested.emit(s),
            )
            menu.addSeparator()
            copy_diff_action = menu.addAction("Copy diff")
            copy_diff_action.triggered.connect(
                lambda checked=False, s=sha: self.copy_diff_requested.emit(s),
            )
            copy_sha_action = menu.addAction("Copy SHA")
            copy_sha_action.triggered.connect(
                lambda checked=False, s=sha: self.copy_commit_sha_requested.emit(s),
            )
            menu.addSeparator()
            drop_action = menu.addAction("Delete Stash")
            drop_action.triggered.connect(
                lambda checked=False, s=sha: self.stash_drop_requested.emit(s),
            )
        elif kind == "wip":
            stash_action = menu.addAction("Stash Changes")
            stash_action.triggered.connect(
                lambda checked=False, s=sha: self.stash_push_requested.emit(s),
            )
            menu.addSeparator()
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
            copy_sha_action = menu.addAction("Copy SHA")
            copy_sha_action.triggered.connect(
                lambda checked=False, s=sha: self.copy_commit_sha_requested.emit(s),
            )
        return menu

    def _show_branch_context_menu(self, chip: dict, position) -> None:
        """Build and show the context menu for a branch chip.

        The menu is the branch-verb equivalent of the left panel's
        local-branch menu: double-click is not the only way to act on
        a branch, the user can also right-click and pick from a list.
        ``Checkout`` and ``Merge X into current`` are always present;
        ``Rebase X onto current`` is added as the symmetric counter-
        operation since the user could plausibly want either.  Actions
        on the current branch are disabled (merging / rebasing a
        branch onto itself is a no-op).
        """
        actions = self._build_branch_menu_actions(chip)
        menu = QMenu(self)
        for action in actions:
            menu.addAction(action)
        menu.exec(self.mapToGlobal(position))

    def _build_branch_menu_actions(self, chip: dict) -> list[QAction]:
        """Build the :class:`QAction` list for a branch chip's context menu.

        Exposed (single-underscore) so tests can inspect the actions
        synchronously without running ``QMenu.exec()`` (which would
        block on user input).

        The same builder is used by :meth:`_show_branch_context_menu`
        for the actual menu; splitting the two lets the tests pin the
        exact label / enabled-state / signal-payload contract without
        poking at the menu lifecycle.

        The trailing ``Create Branch Here`` action opens an inline
        :class:`QLineEdit` anchored to the chip — the action itself
        does not emit a signal because we still need to capture the
        user-typed name. Once the user presses Enter in the editor,
        :meth:`_commit_inline_editor` fires
        :attr:`create_branch_here_requested` and tears the editor down.
        """
        name = chip["display"]
        full_name = chip["full_name"]
        is_remote = chip["is_remote"]
        current = self._current_branch_name()
        is_current = bool(current) and name == current and not is_remote

        actions: list[QAction] = []

        if is_remote:
            checkout_label = f"Checkout {name} (from {full_name.split('/', 1)[0]})"
        else:
            checkout_label = f"Checkout {name}"
        checkout_action = QAction(checkout_label, self)
        checkout_action.triggered.connect(
            lambda checked=False, n=full_name: self.checkout_branch_requested.emit(n),
        )
        actions.append(checkout_action)

        if not is_remote:
            merge_label = f"Merge {name} into {current}" if current else f"Merge {name}"
            merge_action = QAction(merge_label, self)
            merge_action.setEnabled(bool(current) and not is_current)
            merge_action.triggered.connect(
                lambda checked=False, s=name, t=current: (
                    self.merge_branch_requested.emit(s, t) if t else None
                ),
            )
            actions.append(merge_action)

            rebase_label = f"Rebase {name} onto {current}" if current else f"Rebase {name}"
            rebase_action = QAction(rebase_label, self)
            rebase_action.setEnabled(bool(current) and not is_current)
            rebase_action.triggered.connect(
                lambda checked=False, s=name, t=current: (
                    self.rebase_branch_requested.emit(s, t) if t else None
                ),
            )
            actions.append(rebase_action)

            actions.append(self._make_separator())
            create_action = QAction("Create branch here", self)
            # Capture the chip on the lambda's closure — the menu
            # builder is called per-context-menu so we don't need the
            # caller to remember which chip was right-clicked.
            create_action.triggered.connect(
                lambda checked=False, c=chip: self._open_inline_editor(c),
            )
            actions.append(create_action)

        # ----- copy (matches the left panel's section) -------------------

        actions.append(self._make_separator())
        copy_name = QAction("Copy branch name", self)
        copy_name.triggered.connect(
            lambda checked=False, n=full_name: (self.copy_branch_name_requested.emit(n)),
        )
        actions.append(copy_name)

        row_sha = chip.get("row_sha") or ""
        if row_sha:
            copy_sha = QAction("Copy commit sha", self)
            copy_sha.triggered.connect(
                lambda checked=False, s=row_sha: (self.copy_commit_sha_requested.emit(s)),
            )
            actions.append(copy_sha)

        return actions

    def _make_separator(self) -> QAction:
        """Build a disabled QAction used as a visual menu separator."""
        sep = QAction(self)
        sep.setSeparator(True)
        return sep

    # ------------------------------------------------------------------
    # inline branch-name editor ("Create branch here")
    # ------------------------------------------------------------------

    def _open_inline_editor(self, chip: dict) -> None:
        """Show a :class:`QLineEdit` anchored to a branch chip.

        Called from the "Create branch here" menu action. The editor
        is positioned in widget coordinates over (or just below) the
        chip; pressing Enter fires
        :attr:`create_branch_here_requested` with the chip's commit
        SHA and the typed name; pressing Escape or losing focus just
        closes the editor without emitting anything.

        Only one editor can be open at a time — opening a new one
        implicitly closes any prior instance (the typical flow is
        right-click → pick action → type → Enter, but we also handle
        "right-click → pick → right-click another chip" by tearing
        down the previous editor first).
        """
        self._close_inline_editor()
        rect = chip.get("rect")
        if rect is None:
            return
        # Chip rects mix widget-y (the painter's vertical translation
        # is ``0``) with content-x (column 0 is translated by
        # ``-self._h_scrolls[0]``). ``setGeometry`` expects widget
        # coordinates, so convert x by subtracting the current
        # horizontal scroll offset of column 0. Without this the
        # editor appears displaced from the chip by however far the
        # column is scrolled.
        anchor_x = rect.x() - self._h_scrolls[0]
        anchor_y = rect.y()
        anchor_w = max(160, rect.width())
        anchor_h = max(self._cfg.row_height, rect.height())

        # Ensure the anchor stays inside the column on a small
        # viewport; the editor is wider than the chip and should not
        # spill into the graph column.
        col_left, col_right = self._col_ranges()[0]
        max_w = max(80, col_right - col_left - (anchor_x - col_left) - 6)
        anchor_w = min(anchor_w, max_w)
        # Drop the editor just below the chip so the cursor stays
        # near the user's right-click point. The row-height slot is
        # 32px so a 26px editor still leaves a few pixels of breathing
        # room above and below.
        editor_h = max(22, min(anchor_h, 26))
        editor_y = anchor_y + (anchor_h - editor_h) // 2

        editor = QLineEdit(self)
        editor.setPlaceholderText("New branch name")
        editor.setGeometry(anchor_x, editor_y, anchor_w, editor_h)
        editor.setClearButtonEnabled(True)
        editor.setFrame(True)
        editor.show()
        editor.setFocus(Qt.FocusReason.OtherFocusReason)
        editor.raise_()
        editor.selectAll()

        editor.returnPressed.connect(
            lambda e=editor: self._commit_inline_editor(e),
        )
        # ``editingFinished`` also fires on focus loss, but we use
        # ``returnPressed`` (Enter) for commit and ``Escape`` via
        # an event filter for cancellation so losing focus silently
        # closes the editor without re-firing on every redraw.
        editor.installEventFilter(self)
        self._inline_editor = editor
        self._inline_editor_row_sha = chip.get("row_sha")
        # Cache the *widget-coords* anchor rect for re-positioning
        # if the column scrolls; the editor follows the chip rather
        # than moving with the scrollbar (it would feel jarring to
        # have the input drift while typing).
        self._inline_editor_anchor = QRect(anchor_x, editor_y, anchor_w, editor_h)

    def _commit_inline_editor(self, editor: QLineEdit) -> None:
        """Finalise the inline branch-name editor (Enter pressed).

        Empty / whitespace-only names are treated as cancellation —
        the user might have hit Enter by accident. The check mirrors
        :meth:`src.ui.widgets.left_panel.LeftPanel._prompt_create_branch`,
        where ``name.strip() == ""`` short-circuits as well.
        """
        text = editor.text().strip()
        if not text:
            self._close_inline_editor()
            return
        sha = self._inline_editor_row_sha or ""
        # Detach the editor from the widget before emitting — the
        # receiving slot will rebuild the graph (and we tear down
        # the editor in :meth:`_close_inline_editor` anyway).
        self._close_inline_editor()
        if sha:
            self.create_branch_here_requested.emit(sha, text)

    def _close_inline_editor(self) -> None:
        """Remove the inline editor if one is currently open."""
        editor = self._inline_editor
        if editor is None:
            return
        # ``setParent(None)`` releases the editor from the widget
        # tree before ``deleteLater()`` schedules destruction; doing
        # only the latter would leave a dangling parent pointer in
        # the brief window between calls. Tests that poke at
        # ``self._inline_editor`` directly use the cleared state.
        try:
            editor.removeEventFilter(self)
        except Exception:
            pass
        editor.hide()
        editor.setParent(None)
        editor.deleteLater()
        self._inline_editor = None
        self._inline_editor_row_sha = None
        self._inline_editor_anchor = None

    # ------------------------------------------------------------------
    # branch-stack popup ("self-expanding list" on hover)
    # ------------------------------------------------------------------

    def _branch_group_size(self, row_sha: str) -> int:
        """How many branches share *row_sha* across all visible rows.

        Returns 0 if *row_sha* is not present (no chip to render).
        Used to decide whether a row gets the collapsed ``▼`` chip
        or the full multi-chip layout — a row with a single branch
        keeps the historical single-chip rendering.
        """
        count = 0
        for r in self._rows:
            if _row_sha(r) == row_sha:
                count = len(r.get("branch_refs", []))
                break
        return count

    def _branches_at_row(self, row_sha: str) -> list[dict]:
        """Return branch_refs for *row_sha* (or ``[]`` if absent).

        Each entry is the raw branch dict that comes from the
        ViewModel (``{name, is_head, is_remote, ...}``); the helper
        does not mutate the list so callers can sort freely.
        """
        for r in self._rows:
            if _row_sha(r) == row_sha:
                return list(r.get("branch_refs", []))
        return []

    def _branches_at_row_visible(self, row_sha: str) -> list[dict]:
        """Same as :meth:`_branches_at_row` but with same-name-remote
        and ``*/HEAD`` suppression applied.

        Callers that build a user-facing list of branches (the
        hover-popup, the context-menu, …) should consume *this*
        helper so the local-vs-remote de-duplication seen in the
        chip column extends to every other surface; otherwise the
        user sees the redundant remote copy show up only when they
        interact, which is what the ``main, HEAD, main`` report
        was about.
        """
        return _suppress_dup_remotes(self._branches_at_row(row_sha))

    def _branch_priority_key(self, branch: dict) -> tuple:
        """Sort key: lower tuple = more prominent.

        The first component is the priority bucket:

        - ``0``: current HEAD branch (always wins)
        - ``1``: "source" branch — the one the user was on before
          they created their new branches (we approximate this with
          a walk-back from HEAD; for repositories where HEAD is
          detached or where no branch in the group is reachable
          from HEAD's first-parent chain, we fall back to plain
          alphabetical order).
        - ``2``: branches flagged in the session-recent set (these
          were just created in this run of the application and have
          no commits ahead — the user just made them, so they keep
          the lowest priority).
        - ``3``: anything else (rare — for unreachable / detached
          refs the walk-back may fail).

        Ties break by name so the layout is deterministic across
        reloads.

        R3.2 (P7): the bucket-1 result is now read from
        :attr:`GraphViewModel.branch_priority_for` instead of being
        recomputed by walking HEAD's first-parent chain during
        paint.  This keeps the chip column O(chips) instead of
        O(chips * chain_walk) on every paint.
        """
        name = branch.get("name", "")
        if branch.get("is_head"):
            return (0, name)
        # R3.4 regression fix: the recently-created demotion must win
        # over the source-bucket check. A branch the user just created
        # in this session shares the first-parent tip with HEAD (so it
        # would naturally fall in bucket 1) but we still want to demote
        # it below the source — otherwise the prominent chip jumps to
        # the brand-new branch every time ``create_branch`` fires.
        if name in self._recently_created_branches:
            return (2, name)
        # Bucket 1 (source) — read from the VM cache.
        try:
            bucket, _ = self._view_model.branch_priority_for(name)
            if bucket == 1:
                return (1, name)
        except (AttributeError, RuntimeError):
            # Defensive: a custom VM that does not implement the
            # new contract is treated as "no source info", which
            # is the same as the pre-R3.2 behaviour.
            pass
        return (3, name)

    def _is_branch_reachable_from_head(self, branch: dict) -> bool:
        """Heuristic for "this is the *source* branch" — DEPRECATED in R3.2.

        Kept as a private helper for back-compat with tests that
        exercise it directly.  Production code now reads the
        precomputed ``branch_priority_cache`` via
        :meth:`GraphViewModel.branch_priority_for`, which avoids
        hitting pygit2 during paint (R3.2 P7).  New code should
        use the VM API instead of this method.
        """
        repo = self._view_model.repository()
        if repo is None or not repo.is_open:
            return False
        head_target = branch.get("target_sha")
        if not head_target:
            return False
        try:
            branch_tips = {b.target_sha for b in repo.branches if b.target_sha}
        except Exception:
            return False
        if head_target in branch_tips and not branch.get("is_head", False):
            # The branch itself shares a tip with some other ref —
            # fine, that's what we want to surface as a candidate.
            pass
        # Walk HEAD back through first parents.
        try:
            head_oid = str(repo.repo.head.target)
        except Exception:
            return False
        seen: set[str] = set()
        cur_oid: str | None = head_oid
        hops = 0
        max_hops = 256
        while cur_oid and cur_oid not in seen and hops < max_hops:
            seen.add(cur_oid)
            # If HEAD's tip itself is the same as another branch
            # in the graph, that branch is the *source* by
            # definition (everything came from HEAD).
            if cur_oid in branch_tips and cur_oid != head_target:
                # The branch we are scoring points at ``head_target``;
                # if any branch reachable from HEAD points at
                # ``head_target`` too, that's a candidate — but we
                # cheat: report ``True`` whenever *any* branch tip
                # is encountered along the walk-back, because the
                # common case is "two branches at the same commit,
                # one of which used to be HEAD".
                return True
            try:
                commit = repo.repo.revparse_single(cur_oid)
            except Exception:
                return False
            parents = commit.parents
            cur_oid = str(parents[0].id) if parents else None
            hops += 1
        return False

    def _show_branch_popup(self, row_sha: str, anchor: QRect) -> None:
        """Open the hover-popup listing all branches at *row_sha*.

        The popup shows even the primary chip — the user can pick
        *any* of the listed branches. Single-clicking an item
        (which Qt's list-widget triggers on activation) emits
        :attr:`checkout_branch_requested` and closes the popup.
        """
        if self._branch_popup is not None:
            self._hide_branch_popup()
        # Apply the same same-name / ``*/HEAD`` suppression the
        # chip column uses — without it the popup would reveal
        # ``origin/main`` (duplicate of the local main) and
        # ``origin/HEAD`` (synthetic fetch marker) right next to
        # the local main row, exactly the "main, HEAD, main"
        # symptom the user reported.
        branches = self._branches_at_row_visible(row_sha)
        if not branches:
            return
        # A popup with a single entry is redundant — the chip
        # itself already shows that one branch. Skipping the
        # popup here also avoids a flash of an empty-feeling
        # dropdown when the filter collapses
        # ``[main, origin/main]`` down to ``[main]``.
        if len(branches) < 2:
            return
        popup = BranchStackPopup(
            parent=self,
            branches=branches,
            anchor_rect=anchor,
            global_pos=self.mapToGlobal(anchor.bottomLeft()),
        )
        popup.branch_selected.connect(self._on_branch_popup_select)
        popup.show()
        self._branch_popup = popup
        self._branch_popup_row_sha = row_sha
        self._branch_popup_anchor = QRect(anchor)
        # Install a global mouse-move filter as a second line of
        # defence. ``leaveEvent`` on the popup is the primary
        # trigger, but it can fail to fire when the cursor jumps
        # (drag-to-another-screen, focus restore from another
        # app, …) — the ``QEvent.MouseMove`` filter below notices
        # the cursor outside both the popup and the source chip
        # and tears the popup down anyway.
        if QApplication.instance() is not None:
            QApplication.instance().installEventFilter(popup)

    def _hide_branch_popup(self) -> None:
        """Tear down the hover-popup if one is currently visible."""
        popup = self._branch_popup
        if popup is None:
            return
        try:
            popup.close()
        except Exception:
            pass
        # ``BranchStackPopup`` schedules itself for deletion in
        # its own close path; clearing the reference here just
        # releases our handle.
        self._branch_popup = None
        self._branch_popup_row_sha = None
        self._branch_popup_anchor = None

    def _on_branch_popup_select(self, full_name: str) -> None:
        """Handle a branch picked from the hover-popup."""
        self._hide_branch_popup()
        if full_name:
            self.checkout_branch_requested.emit(full_name)

    def _on_hover_popup_timer(self) -> None:
        """Slot invoked when the hover-popup debounce elapses.

        Re-validates the cached chip/row before opening the popup
        — the user may have moved the cursor away in the meantime,
        or the graph may have been rebuilt between the timer
        scheduling and the timer firing.  Both produce a quiet
        no-op so a race never opens a stale popup.
        """
        chip = self._popup_hover_chip
        row_sha = self._popup_hover_row_sha
        if chip is None or not row_sha:
            return
        # The chip's row might have been replaced by a new graph
        # update — confirm the row still exists before opening.
        if self._branch_group_size(row_sha) < 2:
            return
        # No hidden siblings *after* the same-name / ``*/HEAD``
        # filter — the popup would just list the single visible
        # branch the chip already shows. ``hidden_count`` is
        # written by :meth:`_draw_branch_chips` against the
        # filtered list, so it is the right value to consult.
        if chip.get("hidden_count", 0) <= 0:
            return
        # Already showing a popup for the same row — nothing to do.
        if self._branch_popup_row_sha == row_sha:
            return
        anchor = QRect(chip.get("rect") or QRect(0, 0, 0, 0))
        if anchor.isNull():
            return
        self._show_branch_popup(row_sha, anchor)

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        # H14: invalidate the chip-rect cache on every repaint so a
        # click after a scroll / resize / row-count change is
        # always hit-tested against the rects that are currently
        # visible. Without this, the cache can hold rects computed
        # for a different ``_scroll_offset`` and the popup fires
        # for the wrong chip.
        self._branch_chip_rects.clear()
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
                x_start + 6,
                0,
                avail,
                hh,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                label,
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
                dx - rect_w // 2,
                0,
                rect_w,
                self.height(),
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

        # Cells that DO NOT contribute a downward vertical segment. They
        # must be excluded from ``prev_occupied`` (which feeds the next
        # iteration's pipe-drawing check) — otherwise a sibling in the
        # next row at the same lane ends up with a stray pipe dangling
        # into the empty area below.
        #
        # * ``MERGE_RIGHT`` / ``MERGE_LEFT`` / ``TEE_UP`` are drawn with
        #   a vertical segment that goes UP from the commit centre
        #   only. They terminate a connector that arrived from the row
        #   above and do not start a continuation into the row below.
        # * ``HORIZONTAL`` has no vertical at all — it is a pure
        #   horizontal line that crosses a connector mid-flight.
        _downward_strip = (
            _T_MERGE_RIGHT,
            _T_MERGE_LEFT,
            _T_TEE_UP,
            _T_HORIZONTAL,
        )

        prev_occupied: set[int] = set()

        for row_idx, row_data in enumerate(self._rows):
            y = self._row_y(row_idx)
            y_center = y + dh / 2
            if y + dh < header_h or y > self.height():
                # Edge row at the top/bottom of the viewport — the
                # bridge pipe above us (if any) still needs drawing.
                # We *don't* skip ``prev_occupied`` bookkeeping for
                # these rows, but we do compute the bookkeeping from
                # the actual previous row (not from a stale empty
                # set) when this is the first visible row, otherwise
                # the bridge pipe between row_idx-1 and row_idx is
                # silently lost. The arithmetic update at the bottom
                # of the loop covers both visible and culled rows
                # uniformly.
                if row_idx > 0:
                    prev = self._rows[row_idx - 1]
                    prev_cells = prev.get("cells", [])
                    prev_occupied = set()
                    for ci, pc in enumerate(prev_cells):
                        t = pc.get("t", _T_EMPTY)
                        if t == _T_EMPTY or t in _downward_strip:
                            continue
                        prev_occupied.add(ci // 2)
                    if (
                        prev.get("commit") is not None
                        or prev.get("is_uncommitted")
                    ):
                        prev_occupied.add(prev.get("lane", 0))
                else:
                    prev_occupied = set()
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
                # The bridge pipe inherits its colour from the lane
                # *above* — the cell in the previous row at the same
                # lane — so a WIP/stash colour that the lane tracks
                # near HEAD flows down toward the root rather than
                # being overwritten by the root's fork-connector
                # colour.  Falls back to the current row's cell
                # colour when the previous row has no cell at this
                # lane (should not happen in practice, but defensive).
                prev_cells = self._rows[row_idx - 1].get("cells", [])
                for li in common:
                    x = self._lane_x(li, lane_w)
                    clr_idx = 0
                    for ci, pc in enumerate(prev_cells):
                        if ci // 2 == li and pc.get("t", _T_EMPTY) != _T_EMPTY:
                            pt = pc.get("t", _T_EMPTY)
                            if pt in (_T_HORIZONTAL_PIPE, _T_TEE_RIGHT, _T_TEE_LEFT, _T_TEE_UP):
                                # ``"p"`` is now always written by ``CellInfo.to_dict``;
                                # if it's missing the cell is malformed and we fall back
                                # to the horizontal colour (``c``) for robustness.
                                clr_idx = pc.get("p")
                                if clr_idx is None:
                                    clr_idx = pc.get("c", 0)
                            else:
                                clr_idx = pc.get("c", 0)
                            break
                    if clr_idx == 0:
                        for ci, cell in enumerate(cells):
                            if ci // 2 == li and cell.get("t", _T_EMPTY) != _T_EMPTY:
                                t = cell.get("t", _T_EMPTY)
                                if t in (_T_HORIZONTAL_PIPE, _T_TEE_RIGHT, _T_TEE_LEFT, _T_TEE_UP):
                                    clr_idx = cell.get("p")
                                    if clr_idx is None:
                                        clr_idx = cell.get("c", 0)
                                else:
                                    clr_idx = cell.get("c", 0)
                                break
                    if clr_idx == 0 and li == lane:
                        clr_idx = row_data.get("color_index", 0)
                    clr = _cell_color(clr_idx)
                    pen = QPen(clr, ew, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
                    painter.setPen(pen)
                    painter.drawLine(
                        int(x),
                        int(prev_y_center + nr),
                        int(x),
                        int(y_center - nr),
                    )

            # ``prev_occupied`` feeds the NEXT iteration's pipe check.
            # Strip cells that do not produce a downward vertical so a
            # sibling in the next row at the same lane does not get a
            # stray pipe.
            next_prev_occupied: set[int] = set()
            for ci, cell in enumerate(cells):
                t = cell.get("t", _T_EMPTY)
                if t == _T_EMPTY or t in _downward_strip:
                    continue
                next_prev_occupied.add(ci // 2)
            if row_data.get("commit") is not None or row_data.get("is_uncommitted"):
                next_prev_occupied.add(lane)
            prev_occupied = next_prev_occupied

            # Bottommost row has no row below; cap the downward
            # extent of every cell so fork connectors (``TEE_RIGHT``,
            # ``TEE_LEFT``, ``HORIZONTAL_PIPE``) and branch starts
            # (``BRANCH_RIGHT``, ``BRANCH_LEFT``) do not draw a stub
            # dangling into the empty space below the root commit.
            bot_half_h = nr if row_idx == len(self._rows) - 1 else None
            _draw_cell_row(
                painter,
                cells,
                col_left,
                lane_w,
                y_center,
                dh,
                ew,
                nr,
                bottom_half_h=bot_half_h,
            )

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
                bg_color = self._cfg.selected_bg_color if is_selected else self._cfg.hover_bg_color
                y_center = y + dh / 2
                painter.fillRect(
                    self._dividers[0],
                    int(y_center - self._cfg.node_radius),
                    self.width() - self._dividers[0],
                    self._cfg.node_radius * 2,
                    QColor(bg_color),
                )

    def _draw_branch_column(
        self,
        painter: QPainter,
        header_h: int,
        left: int,
        right: int,
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
        self,
        painter: QPainter,
        header_h: int,
        left: int,
        right: int,
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
        self,
        painter: QPainter,
        header_h: int,
        left: int,
        right: int,
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
        self,
        painter: QPainter,
        row_data: dict,
        col_range: tuple[int, int],
        y_center: float,
        fm,
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

        # When a local branch and a remote-tracking branch share the
        # same display name (e.g. ``main`` and ``origin/main`` both
        # pointing at HEAD), the remote chip is suppressed entirely
        # — the local one already conveys the "this branch is here"
        # information and the monitor icon on it makes the "local"
        # side obvious. Drawing both would be redundant *and* it
        # would break hit-testing: the chip-rect cache is keyed by
        # display name, so the second chip would overwrite the
        # first, making the menu work on only one of the two
        # visual chips. Compute the set of local display names up
        # front so the per-branch loop can skip the duplicates in
        # O(1).
        local_display_names: set[str] = {
            _branch_display_name(b) for b in branch_refs if not b.get("is_remote")
        }
        visible_branches = _suppress_dup_remotes(branch_refs, local_display_names)

        if not visible_branches:
            return

        if not visible_branches:
            return

        # Collapse policy: every multi-branch row collapses to a
        # single priority chip with a ``▼`` indicator. The other
        # branches are revealed on hover via the branch-stack
        # popup. This matches the original requirement ("default
        # shows the active branch; hover reveals the rest") and
        # the user's clarified preference: even when there are
        # HEAD + a local + a remote at the same commit, the graph
        # should show **one** chip — typically the local one (the
        # priority logic below keeps HEAD > local > remote).
        #
        # 1 branch  → render 1 chip, no collapse, no popup.
        # 2+ branches → render 1 priority chip + ``▼``; the rest
        #   are revealed on hover via the branch-stack popup.
        sorted_branches = sorted(
            visible_branches,
            key=self._branch_priority_key,
        )
        # Cache every chip rect (even for hidden siblings) so
        # hit-tests and external callers looking up the cache by
        # ``(sha, display)`` get the position a chip *would*
        # occupy if the row were expanded. The drawing code below
        # only paints the primary chip; the others are cached but
        # not drawn (the popup exposes them on hover).
        branches_to_render = sorted_branches
        hidden_count = max(0, len(sorted_branches) - 1)

        for idx, branch in enumerate(branches_to_render):
            is_head = branch.get("is_head")
            is_remote = branch.get("is_remote")
            display = _branch_display_name(branch)
            # ``is_remote_only`` distinguishes "remote ref with no
            # same-name local counterpart" from the suppressed-remote
            # case (which is treated as a local for rendering). Only
            # true remote-only chips use the outlined style below.
            is_remote_only = bool(is_remote) and display not in local_display_names

            text_w = fm.horizontalAdvance(display)
            text_h = fm.height()

            content_w = pad
            if is_head:
                content_w += icon_size + gap
            content_w += text_w
            if not is_remote:
                content_w += gap + icon_size
            content_w += gap + avatar_size + pad
            # The collapse indicator slot is reserved on the
            # *primary* chip only — sibling chips never carry the
            # ``▼`` because the user accesses them via the hover
            # popup, not by clicking the row's collapsed indicator.
            is_primary = idx == 0
            indicator_extra = 0
            if is_primary and hidden_count > 0:
                indicator_extra = 18 + (8 if hidden_count > 1 else 0)
                content_w += indicator_extra

            chip_h = self._cfg.node_radius * 2
            chip_top = y_center - chip_h / 2

            # Only paint the chip when this branch is the priority
            # one. The hidden siblings still get cache entries so
            # :meth:`_branch_chip_at` and test helpers can resolve
            # them; their rect describes where they *would* render
            # in the expanded layout. The chip *body* itself is
            # drawn in two styles:
            #
            # - **Filled** (local branches, including remote refs
            #   suppressed by a same-name local): the rect is filled
            #   with the commit colour, text + icons render in
            #   white.
            # - **Outlined** (remote-only refs with no local
            #   counterpart): the rect is rendered as a border in
            #   the commit colour with no fill, so the chip stays
            #   readable against the dark background while clearly
            #   signalling "remote". Text + icons switch to the
            #   commit colour too — that keeps the wire-frame look
            #   consistent and avoids a black-on-transparent chip.
            chip_path = QPainterPath()
            chip_path.addRoundedRect(cursor_x, chip_top, content_w, chip_h, 4, 4)
            if is_primary:
                if is_remote_only:
                    pen = QPen(commit_color, 1.5)
                    painter.setPen(pen)
                    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                    painter.drawPath(chip_path)
                else:
                    painter.fillPath(chip_path, QBrush(commit_color))
            else:
                painter.setPen(Qt.PenStyle.NoPen)  # cache only

            # Record the chip geometry for hit-testing.  The x coordinate
            # is in content (post-translation) space because the painter
            # is translated by ``-_h_scrolls[0]`` before this draw runs;
            # :meth:`_branch_chip_at` undoes the translation by adding
            # the current scroll value back to the click x.
            #
            # The cache is keyed by ``(row_sha, display)`` rather than
            # ``display`` alone: two different rows can have a branch
            # with the same display name (e.g. ``main`` at HEAD and
            # ``main`` at an older commit that a feature branch was
            # forked from), and each row's chip needs its own entry.
            # Within a single row the suppression above guarantees at
            # most one entry per display name.
            row_sha = _row_sha(row_data)
            self._branch_chip_rects[(row_sha, display)] = {
                "rect": QRect(
                    int(cursor_x),
                    int(chip_top),
                    int(content_w),
                    int(chip_h),
                ),
                "is_remote": bool(is_remote),
                "is_remote_only": is_remote_only,
                "is_head": bool(is_head),
                "full_name": branch["name"],
                "display": display,
                "row_sha": row_sha,
                # Bookkeeping for the hover-popup: how many siblings
                # the chip is currently hiding. ``None`` means "no
                # collapse" (single branch) and ``1+`` means the row
                # was rendered collapsed and the popup should kick in
                # when the user parks the cursor on the chip.
                "hidden_count": hidden_count if is_primary else 0,
            }

            if is_primary:
                inner_x = cursor_x + pad
                inner_cy = y_center
                # Outlined (remote-only) chips share their content
                # colour with the border so the icon + label render
                # as a single-coloured wireframe against the dark
                # background. Picked up here once for the whole
                # ``if is_primary`` block.
                content_color = commit_color if is_remote_only else chip_text_color

                if is_head:
                    ck = QPainterPath()
                    ck.moveTo(inner_x, inner_cy - icon_size * 0.15)
                    ck.lineTo(inner_x + icon_size * 0.35, inner_cy + icon_size * 0.25)
                    ck.lineTo(inner_x + icon_size, inner_cy - icon_size * 0.45)
                    painter.setPen(_icon_pen(content_color, 1.6))
                    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                    painter.drawPath(ck)
                    inner_x += icon_size + gap

                painter.setPen(QPen(content_color))
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
                    painter.setPen(_icon_pen(content_color, 1.2))
                    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                    painter.drawPath(mn)
                    inner_x += gap + icon_size

                avatar = self._avatar_for(
                    _row_author(row_data),
                    avatar_size,
                )
                painter.drawPixmap(int(inner_x + gap), int(inner_cy - avatar_size / 2), avatar)
                inner_x += gap + avatar_size

                if hidden_count > 0:
                    self._draw_collapse_indicator(
                        painter,
                        chip_right_x=int(cursor_x + content_w - indicator_extra),
                        chip_cy=int(inner_cy),
                        chip_h=chip_h,
                        chip_text_color=content_color,
                        hidden_count=hidden_count,
                    )

            cursor_x += content_w + gap

    def _draw_collapse_indicator(
        self,
        painter: QPainter,
        chip_right_x: int,
        chip_cy: int,
        chip_h: float,
        chip_text_color: QColor,
        hidden_count: int,
    ) -> None:
        """Draw the small ``▼`` + ``+N`` badge on a collapsed chip.

        The indicator lives in the trailing padding the chip layout
        reserved (:data:`indicator_extra` in
        :meth:`_draw_branch_chips`); ``chip_right_x`` is the
        left edge of that reserved area. ``hidden_count`` tells us
        how many siblings are hidden behind the popup (1+ when the
        collapsed branch mode kicks in).
        """
        size = max(6, min(chip_h - 4, 12))
        cy = chip_cy
        cx = chip_right_x + 3
        path = QPainterPath()
        path.moveTo(cx, cy + size * 0.3)
        path.lineTo(cx + size * 0.5, cy - size * 0.3)
        path.lineTo(cx + size, cy + size * 0.3)
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(chip_text_color))
        painter.drawPath(path)
        if hidden_count > 1:
            painter.setPen(QPen(chip_text_color))
            painter.setFont(self.font())
            fm = painter.fontMetrics()
            label = f"+{hidden_count}"
            painter.drawText(
                int(cx + size + 4),
                int(cy + fm.ascent() / 2),
                label,
            )

    # ------------------------------------------------------------------
    # graph node rendering
    # ------------------------------------------------------------------

    def _draw_graph_node(
        self,
        painter: QPainter,
        row_data: dict,
        col_cx: float,
        lane_w: float,
        y_center: float,
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
            wip_c = color if color.isValid() else QColor(self._cfg.wip_color)
            if is_selected:
                wip_c = _lighten_color(wip_c, 0.4)
            painter.setPen(QPen(wip_c, 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(self._cfg.background_color))
            painter.drawEllipse(
                int(cx - radius),
                int(y_center - radius),
                int(radius * 2),
                int(radius * 2),
            )
        elif is_stash:
            radius = self._cfg.wip_node_radius
            stash_c = color if color.isValid() else QColor(self._cfg.stash_color)
            if is_selected:
                stash_c = _lighten_color(stash_c, 0.4)
            painter.setPen(QPen(stash_c, 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(self._cfg.background_color))
            painter.drawEllipse(
                int(cx - radius),
                int(y_center - radius),
                int(radius * 2),
                int(radius * 2),
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
            painter.setBrush(_lighten_color(color, 0.4))
            painter.setPen(QPen(color, 0))
            painter.drawEllipse(
                int(cx - radius),
                int(y_center - radius),
                int(radius * 2),
                int(radius * 2),
            )
        else:
            radius = self._cfg.node_radius
            painter.setBrush(color)
            painter.setPen(QPen(color, 0))
            painter.drawEllipse(
                int(cx - radius),
                int(y_center - radius),
                int(radius * 2),
                int(radius * 2),
            )

        painter.restore()

        if not is_uncommitted and not is_stash:
            av_size = max(6, radius * 2 - 3)
            av_pix = self._avatar_for(
                _row_author(row_data),
                av_size,
                shape="circle",
            )
            painter.drawPixmap(
                QPointF(cx - av_size / 2.0, y_center - av_size / 2.0),
                av_pix,
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
        self,
        painter: QPainter,
        label: str,
        x: float,
        y: float,
        color: QColor,
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
        self,
        painter: QPainter,
        row_data: dict,
        col_range: tuple[int, int],
        y_center: float,
        fm,
    ) -> None:
        col_left, col_right = col_range
        if col_right - col_left < 20:
            return

        subject = _row_subject(row_data)
        if not subject:
            return

        kind = _row_kind(row_data)
        text_color = (
            QColor(self._cfg.dim_text_color)
            if kind in ("wip", "stash")
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
        self,
        seed: str,
        size: int = 14,
        *,
        shape: str = "square",
    ) -> QPixmap:
        seed = seed or "?"
        cache_key = f"{seed}_{size}_{shape}"
        if cache_key not in self._avatar_cache:
            self._avatar_cache[cache_key] = make_avatar_pixmap(seed, size, shape=shape)
        return self._avatar_cache[cache_key]

    # ------------------------------------------------------------------
    # input
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        # A left-click anywhere closes the open hover-popup; the
        # chip hit-test below will still emit its signal for chips,
        # so the click is not "swallowed" by the closing gesture.
        if self._branch_popup is not None:
            self._hide_branch_popup()
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

        # A click on a branch chip must not be interpreted as a commit
        # selection — the branch and commit gestures live in different
        # widgets conceptually, even though they share a row.  Stash
        # the press state so :meth:`mouseMoveEvent` can promote a
        # longer move into a branch-chip drag.
        chip = self._branch_chip_at(x, y) if y >= hh else None
        if chip is not None:
            self._drag_press_chip = chip
            self._drag_press_pos = event.pos()
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

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """Double-click on a branch chip → checkout the branch.

        Mirrors the left panel's behaviour: a quick double-click on a
        branch label is the conventional way to switch to it.  A
        double-click anywhere else falls through to the default
        press/release pair (commit selection).
        """
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        x, y = event.pos().x(), event.pos().y()
        if y < self._cfg.header_height:
            super().mouseDoubleClickEvent(event)
            return
        chip = self._branch_chip_at(x, y)
        if chip is None:
            super().mouseDoubleClickEvent(event)
            return
        self.checkout_branch_requested.emit(chip["full_name"])
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging_divider >= 0:
            dx = event.pos().x() - self._drag_start_x
            new_pos = self._drag_start_div + dx
            self._move_divider(self._dragging_divider, new_pos)
            self.update()
            event.accept()
            return

        # Promote a chip press into a drag once the cursor has moved
        # far enough.  Doing the threshold check here (rather than
        # relying on ``QDrag.start()`` alone) keeps short clicks
        # cheap and ensures the user gets a clear "this is now a
        # drag" affordance before :class:`QDrag` locks the mouse.
        if (
            self._drag_press_chip is not None
            and self._drag_press_pos is not None
            and self._drag_active_chip is None
        ):
            moved = (
                event.pos() - self._drag_press_pos
            ).manhattanLength() >= _DRAG_START_THRESHOLD_PX
            if moved:
                self._begin_chip_drag(self._drag_press_chip, self._drag_press_pos)
                # ``QDrag.exec`` blocks until the drag finishes; once
                # it returns, fall through so the rest of the move
                # handler can finish its bookkeeping.  We don't
                # ``return`` early because the ``exec`` already
                # handled the drag cursor / hotspot.

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

        # Hover auto-expand: when the cursor parks over a chip that
        # represents a commit with multiple branches (the collapsed
        # chip with a ``▼`` indicator), schedule a popup that lists
        # every branch at that commit.  The timer-based debounce
        # avoids flickering when the cursor whips across chips.
        self._schedule_hover_popup(x, y)

        super().mouseMoveEvent(event)

    def _schedule_hover_popup(self, x: int, y: int) -> None:
        """Open the multi-branch popup after a brief hover debounce.

        Only schedules the popup when the cursor is on a chip whose
        row has more than one branch — single-branch chips keep the
        historic "double-click to switch" UX.  A pending timer is
        cancelled the moment the cursor leaves the chip, so the
        popup only opens once the user has actually paused on the
        chip.
        """
        chip = self._branch_chip_at(x, y)
        if chip is None:
            self._popup_show_timer.stop()
            return
        row_sha = chip.get("row_sha", "")
        if self._branch_group_size(row_sha) < 2:
            self._popup_show_timer.stop()
            return
        self._popup_hover_chip = chip
        self._popup_hover_row_sha = row_sha
        if not self._popup_show_timer.isActive():
            self._popup_show_timer.start()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging_divider = -1
        # A press that did not turn into a drag (i.e. a short click
        # on a branch chip) leaves the press state in place; clear it
        # here so the next press starts fresh.
        self._drag_press_chip = None
        self._drag_press_pos = None
        self._drag_active_chip = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # branch chip drag-and-drop
    # ------------------------------------------------------------------

    def _begin_chip_drag(self, chip: dict, press_pos: QPoint) -> None:
        """Start a :class:`QDrag` carrying the chip's ref name.

        The drag payload is a :class:`QMimeData` with both a custom
        ``application/x-git-py-branch-chip`` type and the plain-text
        branch name; the custom type lets the drop handler tell a
        chip drag from any other drop the widget might one day
        accept, while the plain text keeps the drag useful when
        dropped onto a different widget that only knows text.

        ``exec`` is called synchronously (the call blocks until the
        drag finishes) so the cursor stays grabbed and the user's
        next move is delivered to the drop target. The post-drag
        cleanup resets the press state so the next press starts
        fresh.
        """
        mime = QMimeData()
        mime.setData(_CHIP_MIME, chip["display"].encode("utf-8"))
        mime.setText(chip["display"])
        drag = QDrag(self)
        drag.setMimeData(mime)
        # The pixmap is what the user sees dragged under the cursor.
        # ``render`` paints the chip's repainted region into a small
        # pixmap; a transparent ``QPixmap`` would also work but the
        # drag looks weird without feedback.
        pix = self.grab(
            QRect(
                chip["rect"].x() - self._h_scrolls[0],
                chip["rect"].y(),
                chip["rect"].width(),
                chip["rect"].height(),
            ),
        )
        if not pix.isNull():
            drag.setPixmap(pix)
            drag.setHotSpot(QPoint(pix.width() // 2, pix.height() // 2))

        self._drag_active_chip = chip
        # ``Qt.DropAction.CopyAction`` is the standard action for
        # payload-only drags where the source is not consumed by
        # the drop.  The user can still move the mouse freely and
        # cancel the drag with Esc.
        drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)
        # After the drag returns the user has released the mouse —
        # treat the press as fully consumed.  ``mouseReleaseEvent``
        # will fire too but the state is already cleared, so the
        # double-clear is a no-op.
        self._drag_press_chip = None
        self._drag_press_pos = None
        self._drag_active_chip = None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        """Accept the drag if it carries a branch-chip payload."""
        if event.mimeData().hasFormat(_CHIP_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802
        """Accept the move so Qt shows the drop indicator."""
        if event.mimeData().hasFormat(_CHIP_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        """Resolve the drop target and either act on it or ignore.

        Only drops that land on **another** branch chip produce a
        menu. Drops on the commit graph, the commit message or empty
        space are accepted (so Qt stops showing the "no drop" cursor)
        but produce no action — the user has to drop on a chip to
        indicate a target branch. The signal ``branch_dropped_on_branch``
        carries ``(source, target)`` so the ``MainWindow`` can wire
        it to the merge / rebase verbs without the widget having to
        know the VM's API.
        """
        mime = event.mimeData()
        if not mime.hasFormat(_CHIP_MIME):
            super().dropEvent(event)
            return
        source = mime.text()
        target_chip = self._branch_chip_at(event.pos().x(), event.pos().y())
        if target_chip is None or not source:
            event.acceptProposedAction()
            return
        if source == target_chip["display"]:
            # Dropping a chip on itself is a no-op — merging a
            # branch into itself never makes sense and showing a
            # menu would just confuse the user.
            event.acceptProposedAction()
            return
        self.branch_dropped_on_branch.emit(source, target_chip["display"])
        event.acceptProposedAction()

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
        # R3.1 (P2): the truncation indicator is anchored to the
        # rightmost column; re-position it whenever the user
        # resizes the window or drags a column divider (the
        # divider drag invokes ``_update_scrollbar`` which in turn
        # calls back into ``_layout_truncation_label`` via the
        # ``update()`` chain — but resizing is the only path that
        # does NOT, so we do it here explicitly).
        if self._truncation_label.isVisible():
            self._layout_truncation_label()
        self._update_scrollbar()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        """Filter keyboard events on the inline ``QLineEdit``.

        QLineEdit has no built-in way to react to Escape; we capture
        the key press here and close the editor silently (no
        ``create_branch_here_requested`` emission). Return ``False``
        on every other event so QLineEdit's own handling — including
        ``returnPressed`` — continues to fire.
        """
        if (
            self._inline_editor is not None
            and watched is self._inline_editor
            and event.type() == QEvent.Type.KeyPress
        ):
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.key() == Qt.Key.Key_Escape:
                self._close_inline_editor()
                return True
        return super().eventFilter(watched, event)

    def _move_divider(self, index: int, new_x: int) -> None:
        if index == 0:
            self._dividers[0] = max(80, min(new_x, self._dividers[1] - 60))
        else:
            self._dividers[1] = max(self._dividers[0] + 60, new_x)
        self._update_scrollbar()

    def _hit_test_commit(self, x: int, y: int) -> str | None:
        """Return the SHA at widget coordinates ``(x, y)`` or ``None``.

        Connector-only rows (``sha == ""``) — the vertical lines that
        join two non-adjacent graph cells — are deliberately skipped:
        clicking on a pipe should not produce a SHA, otherwise the
        user can accidentally "select" a non-existent commit when
        they aim at the connector that crosses the row's vertical
        centre.

        The hit-test is arithmetic whenever the row height divides
        ``scroll_y`` cleanly: this is the common case during a
        scroll and avoids walking the row list on every click. The
        fallback loop handles the row-height mismatch (the panel can
        resize the row height on the fly).
        """
        hh = self._cfg.header_height
        dh = self._cfg.row_height
        scroll_y = y - hh + self._scroll_offset
        if scroll_y >= 0 and dh > 0:
            row_idx = scroll_y // dh
            if row_idx < len(self._rows):
                row_data = self._rows[row_idx]
                # Arithmetically confirm the click is inside the
                # row (not in the gap below the last row). Using a
                # strict `<` keeps the bottom-edge case consistent
                # with the loop fallback below.
                if scroll_y < dh * (row_idx + 1):
                    sha = _row_sha(row_data)
                    if sha == "":
                        # Connector-only row — no commit here.
                        return None
                    return sha
        for row_idx, row_data in enumerate(self._rows):
            row_top = dh * row_idx
            if row_top <= scroll_y < row_top + dh:
                sha = _row_sha(row_data)
                if sha == "":
                    return None
                return sha
        return None

    def _branch_chip_at(self, x: int, y: int) -> dict | None:
        """Return the branch chip under widget coordinates ``(x, y)``.

        Returns ``None`` when the click is outside column 0 (the
        branch column) or did not land on a chip's rounded rect.  The
        chip dict has the keys ``rect`` (content-space :class:`QRect`),
        ``is_remote`` (bool), ``is_head`` (bool), ``full_name`` (the
        original ref name — e.g. ``"origin/main"`` for a remote ref),
        ``display`` (the chip's visible label — e.g. ``"main"``) and
        ``row_sha`` (the commit the chip is attached to).

        The hit-test iterates the cache values rather than looking up
        a key, because the cache is keyed by ``(row_sha, display)``
        but a click position is a point in widget coordinates — the
        only field that matters for "is the point in this chip's
        rect?" is the rect itself, which is identical regardless of
        the key.

        The hit-test converts the click x from widget coordinates to
        content coordinates by adding the current horizontal scroll
        of column 0; the rects stored by :meth:`_draw_branch_chips`
        are in content space (the painter is translated by
        ``-self._h_scrolls[0]`` before the chip is drawn).
        """
        col_left, col_right = self._col_ranges()[0]
        if x < col_left or x >= col_right:
            return None
        content_x = x + self._h_scrolls[0]
        for chip_info in self._branch_chip_rects.values():
            if chip_info["rect"].contains(content_x, y):
                return chip_info
        return None

    def _current_branch_name(self) -> str:
        """Return the current HEAD's branch shorthand, or ``""`` if unborn.

        Used by the branch-chip context menu to fill in the "into
        current…" target.  Goes through the bound
        :class:`GraphViewModel`'s repository so it picks up the same
        view the rest of the UI sees.
        """
        repo = self._view_model.repository()
        if repo is None or not repo.is_open or repo.repo.head_is_unborn:
            return ""
        return repo.repo.head.shorthand

    def _row_by_sha(self, sha: str) -> dict | None:
        for row_data in self._rows:
            if _row_sha(row_data) == sha:
                return row_data
        return None

    def _dump_graph(self) -> None:
        """Save a diagnostic JSON dump of the current graph layout."""
        rows_data: list[dict] = []
        for idx, row_data in enumerate(self._rows):
            commit = row_data.get("commit")
            cells_out: list[dict] = []
            for ci, cell in enumerate(row_data.get("cells", [])):
                t = cell.get("t", 0)
                cells_out.append(
                    {
                        "idx": ci,
                        "lane": ci // 2,
                        "type": t,
                        "color": cell.get("c", 0),
                        "pipe_color": cell.get("p"),
                    }
                )
            rows_data.append(
                {
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
                }
            )

        palette_map = {i: c for i, c in enumerate(BRANCH_PALETTE)}
        palette_map[UNCOMMITTED_COLOR_INDEX] = self._cfg.wip_color

        dump = {
            "timestamp": datetime.now().isoformat(),
            "row_count": len(self._rows),
            "max_lane": max(
                (row_data.get("lane", 0) for row_data in self._rows),
                default=0,
            ),
            "palette": palette_map,
            "wip_color_index": UNCOMMITTED_COLOR_INDEX,
            "stash_color": self._cfg.stash_color,
            "rows": rows_data,
        }

        default_name = f"graph_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Graph Dump",
            default_name,
            "JSON (*.json)",
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
    *,
    bottom_half_h: float | None = None,
) -> None:
    """Draw one row of graph cells at *y_center*.

    Vertical lines (PIPE, COMMIT) span *node_radius* above and below
    the centre; the inter-row gap is bridged by ``_draw_cells``.
    """
    half_h = row_height / 2.0
    bot_half_h = bottom_half_h if bottom_half_h is not None else half_h

    for idx, cell in enumerate(cells):
        t = cell.get("t", 0)
        c = cell.get("c", 0)
        # ``p`` may be missing entirely (fall back to ``color_index``)
        # OR be a legitimate palette index — including 0 (GREEN), which
        # used to be conflated with "missing" because the wire format
        # dropped ``"p"`` whenever the value was falsy.  See
        # ``CellInfo.to_dict`` in ``src/core/graph_v2.py``.
        p = cell.get("p")
        has_pipe_color = p is not None

        lane = idx // 2
        is_even = idx % 2 == 0

        if is_even:
            x = col_left + lane * lane_w
        else:
            x = col_left + lane * lane_w + lane_w / 2

        color = _cell_color(c)
        p_color = _cell_color(p) if has_pipe_color else color

        if t == _T_EMPTY:
            continue
        elif t == _T_PIPE:
            _draw_vert_line(painter, x, y_center, node_radius, edge_width, color)
        elif t == _T_COMMIT:
            _draw_vert_line(painter, x, y_center, node_radius, edge_width, color)
        elif t == _T_BRANCH_RIGHT:
            _draw_branch_right(painter, x, y_center, bot_half_h, edge_width, color, lane_w)
        elif t == _T_BRANCH_LEFT:
            _draw_branch_left(painter, x, y_center, bot_half_h, edge_width, color, lane_w)
        elif t == _T_MERGE_RIGHT:
            _draw_merge_right(painter, x, y_center, half_h, edge_width, color, lane_w)
        elif t == _T_MERGE_LEFT:
            _draw_merge_left(painter, x, y_center, half_h, edge_width, color, lane_w)
        elif t == _T_HORIZONTAL:
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
        elif t == _T_HORIZONTAL_PIPE:
            _draw_vert_line(
                painter,
                x,
                y_center,
                half_h,
                edge_width,
                p_color,
                top_half_h=node_radius,
                bottom_half_h=bot_half_h,
            )
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
        elif t == _T_TEE_RIGHT:
            vert_color = p_color if has_pipe_color else color
            _draw_vert_line(
                painter,
                x,
                y_center,
                half_h,
                edge_width,
                vert_color,
                top_half_h=node_radius,
                bottom_half_h=bot_half_h,
            )
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
        elif t == _T_TEE_LEFT:
            vert_color = p_color if has_pipe_color else color
            _draw_vert_line(
                painter,
                x,
                y_center,
                half_h,
                edge_width,
                vert_color,
                top_half_h=node_radius,
                bottom_half_h=bot_half_h,
            )
            _draw_horiz_line(painter, x, y_center, -lane_w, edge_width, color)
        elif t == _T_TEE_UP:
            vert_color = p_color if has_pipe_color else color
            _draw_horiz_line(painter, x, y_center, lane_w, edge_width, color)
            _draw_vert_line(
                painter,
                x,
                y_center,
                half_h,
                edge_width,
                vert_color,
                upward_only=True,
                top_half_h=node_radius,
            )
        elif t == _T_CROSS:
            # Cross-junction: the merge commit at its own lane
            # has already drawn a TEE_RIGHT / TEE_LEFT carrying the
            # horizontal connector up to this cell. The CROSS itself
            # therefore draws the vertical pipes - one UP to the
            # child above (in the child's colour) and one DOWN to the
            # second parent below (in the second parent's colour) -
            # and, when ``direction`` is set, an additional
            # horizontal segment one lane wide so the connector
            # reaches the commit-centred vertical pipe without the
            # ``lane_w / 2`` empty gap that the between-lanes
            # horizontal alone would leave.
            vert_down_color = color
            vert_up_color = p_color if has_pipe_color else color
            _draw_vert_line(
                painter,
                x,
                y_center,
                half_h,
                edge_width,
                vert_up_color,
                top_half_h=node_radius,
                bottom_half_h=0,
            )
            _draw_vert_line(
                painter,
                x,
                y_center,
                half_h,
                edge_width,
                vert_down_color,
                top_half_h=0,
                bottom_half_h=bot_half_h,
            )
            direction = cell.get("d", 0)
            if direction:
                _draw_horiz_line(
                    painter,
                    x,
                    y_center,
                    lane_w * direction,
                    edge_width,
                    color,
                )


def _draw_vert_line(
    painter: QPainter,
    x: float,
    y_center: float,
    half_h: float,
    width: float,
    color: QColor,
    *,
    upward_only: bool = False,
    top_half_h: float | None = None,
    bottom_half_h: float | None = None,
) -> None:
    """Draw a vertical line segment centred at *y_center*, spanning *half_h*
    pixels above and below (or only above when *upward_only*).

    *top_half_h* overrides the upward span: pass a smaller value when the
    cell has no row above (or the lane above is empty) so the line stops
    at the commit edge instead of leaving a stub that dangles into the
    empty space above the topmost commit.

    *bottom_half_h* overrides the downward span: pass a smaller value
    when the cell has no row below (or the lane below is empty) so the
    line stops at the commit edge instead of leaving a stub dangling
    into the empty space below the bottommost commit.
    """
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    if top_half_h is None:
        top_half_h = half_h
    if bottom_half_h is None:
        bottom_half_h = half_h
    top = y_center - top_half_h
    bot = y_center + bottom_half_h
    if upward_only:
        bot = y_center
    painter.drawLine(int(x), int(top), int(x), int(bot))


def _draw_horiz_line(
    painter: QPainter,
    x: float,
    y_center: float,
    lane_w: float,
    width: float,
    color: QColor,
) -> None:
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    sign = 1 if lane_w > 0 else -1
    abs_w = abs(lane_w)
    painter.drawLine(int(x), int(y_center), int(x + sign * abs_w), int(y_center))


def _draw_branch_right(
    painter: QPainter,
    x: float,
    y_center: float,
    radius: float,
    width: float,
    color: QColor,
    lane_w: float = 30.0,
) -> None:
    """Branch starting here, going down and right (╭)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center + radius)
    path.lineTo(x, y_center + cr)
    # Extend the curve endpoint to ``x + lane_w / 2`` so the curve
    # meets the next ``HORIZONTAL`` cell (which starts at ``x +
    # lane_w / 2`` from the cell centre). Without this, ``cr = 8``
    # leaves a 7-pixel visible break between the curve endpoint and
    # the horizontal start.
    path.cubicTo(x, y_center, x, y_center, x + lane_w / 2, y_center)
    painter.drawPath(path)


def _draw_branch_left(
    painter: QPainter,
    x: float,
    y_center: float,
    radius: float,
    width: float,
    color: QColor,
    lane_w: float = 30.0,
) -> None:
    """Branch starting here, going down and left (╮)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center + radius)
    path.lineTo(x, y_center + cr)
    # Mirror of ``_draw_branch_right`` — extend the curve endpoint
    # to ``x - lane_w / 2`` so it meets the previous ``HORIZONTAL_PIPE``
    # cell (which extends to ``x`` from its centre).
    path.cubicTo(x, y_center, x, y_center, x - lane_w / 2, y_center)
    painter.drawPath(path)


def _draw_merge_right(
    painter: QPainter,
    x: float,
    y_center: float,
    radius: float,
    width: float,
    color: QColor,
    lane_w: float = 30.0,
) -> None:
    """Merge from below, going up and right (╰)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center - radius)
    path.lineTo(x, y_center - cr)
    # Same curve-extension fix as ``_draw_branch_right`` — extend
    # the curve endpoint to ``x + lane_w / 2``.
    path.cubicTo(x, y_center, x, y_center, x + lane_w / 2, y_center)
    painter.drawPath(path)


def _draw_merge_left(
    painter: QPainter,
    x: float,
    y_center: float,
    radius: float,
    width: float,
    color: QColor,
    lane_w: float = 30.0,
) -> None:
    """Merge from below, going up and left (╯)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center - radius)
    path.lineTo(x, y_center - cr)
    # Same curve-extension fix as ``_draw_branch_left`` — extend
    # the curve endpoint to ``x - lane_w / 2``.
    path.cubicTo(x, y_center, x, y_center, x - lane_w / 2, y_center)
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


def _branch_display_name(branch: dict) -> str:
    """Return the user-visible chip label for a branch ref dict.

    Local branches keep their bare name (``"main"``); remote-
    tracking refs have the ``origin/``-style prefix stripped so
    the chip matches what the user sees in the left panel. The
    display name is the same key the chip-rect cache uses, so any
    caller that needs to look up a chip rect should go through
    this helper rather than re-implementing the prefix-stripping
    logic.
    """
    name = branch.get("name", "")
    if branch.get("is_remote") and "/" in name:
        return name.split("/", 1)[1]
    return name


def _suppress_dup_remotes(
    branch_refs: list[dict],
    local_display_names: set[str] | None = None,
) -> list[dict]:
    """Drop same-name remote refs and the synthetic ``*/HEAD`` ref.

    Used by both :meth:`GraphTableWidget._draw_branch_chips` (for
    the visible chip column) and :meth:`GraphTableWidget._branches_at_row`
    / the popup builder (for the hover-popup that reveals the
    hidden siblings). Without going through the same helper here
    the popup showed ``main``, ``origin/main``, ``origin/HEAD``
    next to the local main chip — exactly the
    "main, HEAD, main" the user reported.

    Pass *local_display_names* when you already have the set
    computed (the chip-draw path does); otherwise the helper
    derives it from *branch_refs* itself (used by the popup path,
    which has only the row-local data).
    """
    if local_display_names is None:
        local_display_names = {
            _branch_display_name(b) for b in branch_refs if not b.get("is_remote")
        }
    kept: list[dict] = []
    for branch in branch_refs:
        is_remote = branch.get("is_remote")
        if not is_remote:
            kept.append(branch)
            continue
        full_name = branch.get("name", "")
        # Drop the synthetic ``refs/remotes/<remote>/HEAD``
        # pseudo-ref created by ``fetch``.
        if full_name.split("/", 1)[-1] == "HEAD":
            continue
        # Drop same-name remotes when a local branch already
        # covers that display name at this commit.
        if _branch_display_name(branch) in local_display_names:
            continue
        kept.append(branch)
    return kept


# --------------------------------------------------------------------------
# BranchStackPopup: branch-list dropdown for the "two branches at one commit" UX
# --------------------------------------------------------------------------


class BranchStackPopup(QFrame):
    """Compact floating list of branches that share a commit.

    The popup auto-opens when the user hovers a multi-branch chip;
    each row is a coloured, clickable chip. Clicking (or
    double-clicking) a row emits :attr:`branch_selected` with the
    branch's *full* ref name and closes itself. The widget is a
    separate toplevel window (using ``Qt.Tool`` + ``FramelessWindowHint``)
    so it can render above the graph view without clipping at the
    column boundary.

    Visual contract:
    - Width matches the source chip (or falls back to ``min_width``).
    - Height grows with the number of branches; rows are 22px tall.
    - Active branch (when ``is_head`` is set on its dict) is drawn
      in the same colour as the chip that produced it; others are
      shown in a dimmer shade for visual hierarchy.
    """

    branch_selected = Signal(str)  # full ref name

    def __init__(
        self,
        parent: QWidget,
        branches: list[dict],
        anchor_rect: QRect,
        global_pos: QPoint,
        min_width: int = 160,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint,
        )
        # H13: Mark for deletion on close so the popup object does not
        # linger after the user dismisses it; the next hover opens a
        # fresh instance instead of colliding with the old one.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("BranchStackPopup")
        # ``Qt.Tool`` pops the window above normal widgets but keeps
        # it out of the task bar (typical behaviour for transient
        # popovers like autocomplete suggestions). ``WA_ShowWithoutActivating``
        # so stealing focus from the graph view does not yank the
        # user's keyboard cursor off whatever they were about to type.
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        # Allow the popup to render on top of the application's main window.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # Outer frame: dark surface that matches the rest of the app
        # so the popup does not look like a foreign tooltip. The
        # individual rows override their own background.
        self.setStyleSheet(
            "#BranchStackPopup { background-color: #2a2a3a; "
            "border: 1px solid #444; border-radius: 6px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        for branch in branches:
            row = BranchStackPopup._Row(self, branch)
            row.clicked.connect(self._on_row_clicked)
            row.double_clicked.connect(self._on_row_double_clicked)
            layout.addWidget(row)

        size_policy = QSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        self.setSizePolicy(size_policy)
        self.adjustSize()

        # Position next to the anchor chip — to the right if there
        # is room, otherwise to the left of the chip. This keeps
        # the popup from spilling off-screen on a narrow viewport.
        width = max(min_width, self.sizeHint().width())
        height = self.sizeHint().height()
        x = global_pos.x()
        y = global_pos.y()
        # Adjust vertical position so the popup aligns near the
        # bottom of the chip (cleaner visual link). ``QScreen``
        # exposes its bounds as a ``QRect`` via ``geometry()`` /
        # ``availableGeometry()``; the legacy ``top/right/bottom/
        # left`` accessors vary by Qt binding, so always go through
        # the rect.
        screen_geom = QApplication.screenAt(global_pos)
        if screen_geom is not None:
            avail = screen_geom.availableGeometry()
            if y + height > avail.bottom():
                y = max(avail.top(), avail.bottom() - height)
            if x + width > avail.right():
                x = max(avail.left(), global_pos.x() - width)
        self.setGeometry(int(x), int(y), int(width), int(height))

        # Auto-close timer. Reset on any mouse-move *over the popup
        # itself*, fired once the cursor has left the popup frame
        # for ``_POPUP_LEAVE_CLOSE_MS``. Combined with ``leaveEvent``
        # this guarantees the popup disappears the moment the user
        # moves the cursor off the chip's branches — even when the
        # mouse lands somewhere unrelated (sidebar, another commit
        # row, the window title bar, etc.).
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.setInterval(160)
        self._leave_timer.timeout.connect(self.close)
        self._source_anchor_rect = QRect(anchor_rect)
        # The chip the popup came from lives in widget-space of
        # *parent*. Track the parent so :meth:`moveEvent` can keep
        # the popup glued to the chip when the application window
        # itself moves (without the auto-close timer firing just
        # because the window was dragged to a different screen).
        self._anchor_widget = parent
        self._anchor_widget_pos_at_show = parent.mapToGlobal(QPoint(0, 0))
        # Watch the parent window for move / activate / focus-out
        # events so the popup can react: dragged windows track the
        # chip, and ``ApplicationActivate`` reset re-runs the
        # timer (the user is unlikely to be hovering the chip
        # immediately after focus comes back from another app).
        parent.installEventFilter(self)
        self._watched_parent = parent

    # ----- internal helpers ----------------------------------------

    def _on_row_clicked(self, full_name: str) -> bool | None:
        """Single-click → switch immediately, then close.

        The user-facing rule is "double-click to switch"; in
        practice users expect a list-popup to activate on the
        first click (autocomplete-style). We honour both: a single
        click is enough to select *and* switch.
        """
        self.branch_selected.emit(full_name)

    def _on_row_double_clicked(self, full_name: str) -> bool | None:
        """Double-click is the documented shortcut — reuses the same path."""
        self.branch_selected.emit(full_name)

    # ----- auto-close ----------------------------------------------------

    def leaveEvent(self, event) -> None:  # noqa: N802
        """Start the close timer when the cursor leaves the popup.

        A short debounce (set up in ``__init__``) lets the user
        cross internal row boundaries without the popup blinking
        closed and back open. ``mouseMoveEvent`` resets the timer,
        so hovering inside the popup keeps it open indefinitely.
        """
        self._leave_timer.start()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """Cancel the close timer while the cursor is inside the popup."""
        self._leave_timer.stop()
        super().mouseMoveEvent(event)

    def enterEvent(self, event) -> None:  # noqa: N802
        """Stop the timer if the cursor re-enters before the debounce fires."""
        self._leave_timer.stop()
        super().enterEvent(event)

    def moveEvent(self, event) -> None:  # noqa: N802
        """Keep the popup glued to the chip when the parent window moves.

        Qt.Tool pop-ups stay where they were placed unless the
        application repositions them. Recompute the chip's new
        global position each time the parent widget moves
        (window drag, resize, screen change, ..) and shift the
        popup by the same delta so the visual link to the chip
        stays intact.
        """
        super().moveEvent(event)
        widget = self._anchor_widget
        if widget is None:
            return
        new_origin = widget.mapToGlobal(QPoint(0, 0))
        delta = new_origin - self._anchor_widget_pos_at_show
        if delta.isNull():
            return
        self._anchor_widget_pos_at_show = new_origin
        geo = self.geometry()
        self.move(geo.topLeft() + delta)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        """React to the parent window's moves / focus changes
        *and* to global mouse moves the application receives.

        Two responsibilities:

        - Parent's ``QEvent.Move`` translates the popup by the
          same delta so the visual link to the chip stays intact
          when the user drags the application window to another
          monitor.
        - Application-level ``MouseMove`` watches the cursor: if
          it is outside both the popup frame and the source chip,
          the popup is dismissed. ``leaveEvent`` alone is not
          enough — the popup is a separate ``Qt.Tool`` window
          and ``leaveEvent`` does not fire reliably when the
          cursor jumps to another screen, when focus is restored
          from another app, or when the user starts dragging the
          window itself.

        ``QEvent.ApplicationDeactivate`` (alt-tab away) also
        closes the popup immediately — the user has clearly
        shifted focus elsewhere.
        """
        if watched is self._watched_parent:
            if event.type() == QEvent.Type.Move:
                new_origin = self._anchor_widget.mapToGlobal(QPoint(0, 0))
                delta = new_origin - self._anchor_widget_pos_at_show
                if not delta.isNull():
                    self._anchor_widget_pos_at_show = new_origin
                    geo = self.geometry()
                    self.move(geo.topLeft() + delta)
            elif event.type() in (
                QEvent.Type.WindowStateChange,
                QEvent.Type.ApplicationActivate,
            ):
                return False
        else:
            # Application-level filter installed in
            # :meth:`GraphTableWidget._show_branch_popup`.
            if event.type() == QEvent.Type.MouseMove:
                cursor = event.globalPosition().toPoint()
                if not self._cursor_inside_popup_or_chip(cursor):
                    self.close()
                    return False
            elif event.type() == QEvent.Type.ApplicationDeactivate:
                self.close()
                return False
        return super().eventFilter(watched, event)

    def _cursor_inside_popup_or_chip(self, global_pos: QPoint) -> bool:
        """True when *global_pos* sits inside the popup or the chip."""
        if self.geometry().contains(global_pos):
            return True
        widget = self._anchor_widget
        chip = self._source_anchor_rect
        if widget is not None and not chip.isEmpty():
            top_left = widget.mapToGlobal(chip.topLeft())
            bottom_right = widget.mapToGlobal(chip.bottomRight())
            chip_global = QRect(top_left, bottom_right)
            return chip_global.contains(global_pos)
        return False

    def hideEvent(self, event) -> None:  # noqa: N802
        """Clean up state and drop the parent's reference when hidden.

        Two paths land here:

        - User picked a row (``branch_selected`` → parent calls
          :meth:`GraphTableWidget._hide_branch_popup` → ``close()``
          on us).
        - The leave-timer fired (``self.close()`` from the timer slot,
          no parent involvement).

        The parent only clears its ``_branch_popup`` reference from
        the first path; without help, the timer path leaves a stale
        ``BranchStackPopup`` referenced on the widget. Detecting that
        this widget is still the parent's current popup and clearing
        the slot makes the widget re-show a fresh popup on the next
        hover without keeping the previous (closed) instance alive.
        """
        self._leave_timer.stop()
        if self._watched_parent is not None:
            try:
                # Only clear the slot if we're still the active
                # popup — otherwise the user clicked a row, the
                # parent already cleared us via ``_hide_branch_popup``
                # and we don't want to clobber the fresh popup that
                # might have replaced us.
                if getattr(self._watched_parent, "_branch_popup", None) is self:
                    self._watched_parent._branch_popup = None
                    self._watched_parent._branch_popup_row_sha = None
                    self._watched_parent._branch_popup_anchor = None
            except Exception:
                pass
            try:
                self._watched_parent.removeEventFilter(self)
            except Exception:
                pass
            self._watched_parent = None
        # Drop the application-level mouse-move filter too. Qt's
        # ``removeEventFilter`` is a no-op when the filter wasn't
        # installed, so this is safe even when the global filter
        # was never set up.
        app = QApplication.instance()
        if app is not None:
            try:
                app.removeEventFilter(self)
            except Exception:
                pass
        super().hideEvent(event)

    class _Row(QFrame):
        """One clickable row inside :class:`BranchStackPopup`.

        Implemented as a tiny coloured chip (mirroring the
        collapsed-mode chip) so the popup reads as "the same
        branch chips stacked vertically". Single- and double-click
        are both forwarded to the parent popup via signals.
        """

        clicked = Signal(str)
        double_clicked = Signal(str)

        def __init__(self, parent: QWidget, branch: dict) -> None:
            super().__init__(parent)
            self._branch = branch
            self.setObjectName("BranchStackPopup.Row")
            self.setFrameShape(QFrame.Shape.NoFrame)
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setFixedHeight(24)

            hbox = QHBoxLayout(self)
            hbox.setContentsMargins(8, 2, 8, 2)
            hbox.setSpacing(6)

            if branch.get("is_head"):
                indicator = QLabel("\u2713", self)
                indicator.setStyleSheet("color: white; font-weight: bold;")
                indicator.setFixedWidth(12)
                hbox.addWidget(indicator)

            name_label = QLabel(
                _branch_display_name(branch) or branch.get("name", ""),
                self,
            )
            name_label.setStyleSheet("color: white; font-weight: bold;")
            hbox.addWidget(name_label, stretch=1)

            if not branch.get("is_remote"):
                mn = QLabel("\u26c4", self)  # umbrella as a placeholder
                mn.setStyleSheet("color: white; opacity: 0.7;")
                mn.setFixedWidth(12)
                hbox.addWidget(mn)

            color_idx = _pick_branch_color(branch.get("name", ""))
            palette = BRANCH_PALETTE
            if 0 <= color_idx < len(palette):
                bg = palette[color_idx]
            else:
                bg = palette[0]
            self.setStyleSheet(f"background-color: {bg}; border-radius: 4px;")

        def mousePressEvent(self, event) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit(self._branch.get("name", ""))
                event.accept()
                return
            super().mousePressEvent(event)

        def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                self.double_clicked.emit(self._branch.get("name", ""))
                event.accept()
                return
            super().mouseDoubleClickEvent(event)


__all__ = ["GraphTableWidget", "RenderConfig", "BranchStackPopup"]
