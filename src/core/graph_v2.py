"""Commit graph construction using cell-based lane tracking.

Ported from the keifu project's ``graph.rs`` (Rust + git2-rs).
The algorithm makes a single pass over the commit list (newest first),
assigning lanes, detecting fork points, building connector rows,
and producing per-row cell vectors that describe the exact geometry
for every row of the graph.

Key differences from the old ``graph.py``:

* Cell-based rendering — each row carries a ``list[CellType]`` that
  tells the widget exactly what to draw. No geometry computation in
  the widget layer.
* Fork point detection — commits with 2+ children get their merge
  cells merged into the fork point commit's own row.
* Fork siblings — the first parent of a merge commit that sits on a
  fork point is treated specially so its colour propagates correctly.
* Explicit lane merging — when a branch lane ends and its parent is
  already tracked on a different lane, the ending lane is released.
* Uncommitted changes are handled in core — inserted at position 0
  with a special colour index.
* Deterministic branch colours — ``branch_name_to_color()`` maps
  branch names to palette indices via hashing + hardcoded overrides.

This module is pure Core — no PySide6 imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from zlib import crc32

from src.core.models import BranchInfo, CommitInfo

UNCOMMITTED_COLOR_INDEX: int = 40
"""Special colour index reserved for the uncommitted-changes node.

One past the end of :data:`BRANCH_PALETTE` so that a regular branch
hash can never collide with the WIP marker.
"""

BRANCH_PALETTE: tuple[str, ...] = (
    "#1A5924",  # 0   green
    "#2B4786",  # 1   blue
    "#782B24",  # 2   red
    "#7D5C1A",  # 3   amber
    "#523583",  # 4   violet
    "#1E626A",  # 5   teal
    "#7D2559",  # 6   pink
    "#38684F",  # 7   mint
    "#7F4112",  # 8   orange  (HEAD special)
    "#464B51",  # 9   grey
    "#6A5086",  # 10  lavender
    "#256D2D",  # 11  lime
    "#5A8A3C",  # 12  olive
    "#3B6FB0",  # 13  steel
    "#B5453C",  # 14  rust
    "#C4912E",  # 15  gold
    "#704A9E",  # 16  plum
    "#2D7F8C",  # 17  cyan
    "#C4426E",  # 18  rose
    "#4D7844",  # 19  sage
    "#AD5A28",  # 20  copper
    "#595F6B",  # 21  slate
    "#7C5E9E",  # 22  lilac
    "#41804A",  # 23  pine
    # --- extended palette (24..39): added in 2026-07 to reduce
    # crc32 % 24 collisions in repositories with 60+ branches.
    "#0F4D5C",  # 24  sea
    "#D05B3F",  # 25  coral
    "#9A7B3A",  # 26  bronze
    "#3A4F8C",  # 27  indigo
    "#5BA8C9",  # 28  sky
    "#B8945C",  # 29  sand
    "#7A2E3F",  # 30  burgundy
    "#E8956C",  # 31  peach
    "#A89968",  # 32  khaki
    "#3F8C73",  # 33  jade
    "#A8478B",  # 34  fuchsia
    "#8B5A3C",  # 35  chestnut
    "#2680B0",  # 36  cerulean
    "#9A7CB0",  # 37  wisteria
    "#C4845A",  # 38  sandalwood
    "#5C8C42",  # 39  moss
)
"""40-colour palette used by :class:`ColorAssigner` and the widget layer."""

# Hardcoded overrides ensure that important branches always get the same
# well-known colours, regardless of which repository is open.
_BRANCH_COLOR_OVERRIDES: dict[str, int] = {
    "main": 1,
    "master": 1,
    "develop": 0,
    "dev": 0,
}
"""Case-insensitive mapping from branch name # colour palette index."""

# DEPRECATED: retained for compatibility; HEAD highlighting is now handled
# by the graph layout's regular color assignment.
HEAD_SPECIAL_COLOR_INDEX: int = 8

def _pick_branch_color(name: str) -> int:
    """Return a deterministic palette index for *name*.

    Hardcoded overrides are checked first (case-insensitive); the
    remainder are hashed modulo the palette size. ``zlib.crc32`` is
    used instead of the built-in :func:`hash` because the latter is
    randomised at interpreter startup via ``PYTHONHASHSEED`` and
    would give a different colour for the same branch on every run.
    """
    lower = name.lower()
    override = _BRANCH_COLOR_OVERRIDES.get(lower)
    if override is not None:
        return override
    return crc32(lower.encode("utf-8")) % len(BRANCH_PALETTE)


# Alias kept for documentation cross-reference.
MAIN_COLOR_INDEX: int = 1  # "main"/"master" → blue via overrides


class CellType(IntEnum):
    """Atomic rendering element for one cell of a graph row.

    Integer values allow cheap serialisation (``cell.value``) for Qt signals.
    """

    EMPTY = 0
    PIPE = 1  # │ vertical line (active lane)
    COMMIT = 2  # ● commit node
    BRANCH_RIGHT = 3  # ╭ branch starts, goes right + down
    BRANCH_LEFT = 4  # ╮ branch starts, goes left + down
    MERGE_RIGHT = 5  # ╰ merge from right, goes up
    MERGE_LEFT = 6  # ╯ merge from left, goes up
    HORIZONTAL = 7  # ─ horizontal line
    HORIZONTAL_PIPE = 8  # ─┼─ horizontal crossing a vertical
    TEE_RIGHT = 9  # ├ T-junction right
    TEE_LEFT = 10  # ┤ T-junction left
    TEE_UP = 11  # ┴ T-junction up (fork middle lane)
    CROSS = 12  # ┼ cross: horizontal + vertical up + vertical down
    # Used at a lane where a child sits above AND a
    # second parent sits below (a fork-merge point
    # whose child and second parent share the lane).
    # The horizontal pipe is the merge connector from
    # the merge commit; the vertical pipes pass
    # through the cell in both directions.


@dataclass
class CellInfo:
    """A single cell with its type and colour payload.

    For most cell types the payload is a single ``color_index``.
    ``HORIZONTAL_PIPE``, ``TEE_RIGHT``, ``TEE_LEFT``, and ``TEE_UP`` carry
    *two* indices: ``(horizontal_color, pipe_color)`` --- when
    ``pipe_color_index`` is non-zero it overrides the vertical-line colour.
    """

    cell_type: CellType
    color_index: int = 0
    pipe_color_index: int = 0
    direction: int = 0
    """For ``CROSS`` cells: ``1`` = horizontal extends to the RIGHT
    (toward the next lane), ``-1`` = to the LEFT (toward the previous
    lane), ``0`` = no horizontal segment.  Bridges the gap between the
    commit-centred vertical pipe and the between-lanes horizontal
    connector — see ``graph_panel._draw_cell_row`` for the renderer."""

    def to_dict(self) -> dict:
        d: dict = {"t": int(self.cell_type)}
        if self.cell_type == CellType.EMPTY:
            return d
        if self.cell_type in (
            CellType.HORIZONTAL_PIPE,
            CellType.TEE_RIGHT,
            CellType.TEE_LEFT,
            CellType.TEE_UP,
            CellType.CROSS,
        ):
            d["c"] = self.color_index
            # ``pipe_color_index == 0`` is a legitimate palette index
            # (GREEN ``#1A5924``) and must survive the round-trip.  The
            # renderer distinguishes "use the pipe colour" from "fall
            # back to ``color_index``" by checking whether the ``p`` key
            # is present at all.
            d["p"] = self.pipe_color_index
        else:
            d["c"] = self.color_index
        if self.cell_type == CellType.CROSS and self.direction:
            d["d"] = self.direction
        if self.cell_type in (
            CellType.HORIZONTAL,
            CellType.HORIZONTAL_PIPE,
        ) and self.direction:
            # Trimmed horizontal: ``-1`` paints only the left half of
            # the cell's span (stops at the next lane centre), ``+1``
            # only the right half.  Used for the incoming cell of an
            # up-bend so the track does not protrude past the bend.
            d["d"] = self.direction
        return d

    @staticmethod
    def empty() -> CellInfo:
        return CellInfo(CellType.EMPTY)

    @staticmethod
    def pipe(color: int) -> CellInfo:
        return CellInfo(CellType.PIPE, color_index=color)

    @staticmethod
    def commit(color: int) -> CellInfo:
        return CellInfo(CellType.COMMIT, color_index=color)

    @staticmethod
    def branch_right(color: int) -> CellInfo:
        return CellInfo(CellType.BRANCH_RIGHT, color_index=color)

    @staticmethod
    def branch_left(color: int) -> CellInfo:
        return CellInfo(CellType.BRANCH_LEFT, color_index=color)

    @staticmethod
    def merge_right(color: int) -> CellInfo:
        return CellInfo(CellType.MERGE_RIGHT, color_index=color)

    @staticmethod
    def merge_left(color: int) -> CellInfo:
        return CellInfo(CellType.MERGE_LEFT, color_index=color)

    @staticmethod
    def horizontal(color: int, direction: int = 0) -> CellInfo:
        return CellInfo(CellType.HORIZONTAL, color_index=color, direction=direction)

    @staticmethod
    def horizontal_pipe(h_color: int, p_color: int, direction: int = 0) -> CellInfo:
        return CellInfo(
            CellType.HORIZONTAL_PIPE,
            color_index=h_color,
            pipe_color_index=p_color,
            direction=direction,
        )

    @staticmethod
    def tee_right(color: int) -> CellInfo:
        return CellInfo(CellType.TEE_RIGHT, color_index=color)

    @staticmethod
    def tee_left(color: int) -> CellInfo:
        return CellInfo(CellType.TEE_LEFT, color_index=color)

    @staticmethod
    def tee_up(color: int) -> CellInfo:
        return CellInfo(CellType.TEE_UP, color_index=color)

    @staticmethod
    def cross(h_color: int, p_color: int, direction: int = 0) -> CellInfo:
        """Cross-junction (┼): horizontal + vertical up + vertical down.

        *h_color* is the horizontal/vertical-down colour (the merge
        connector + the lane continuation down to the second parent).
        *p_color* overrides the vertical-up colour (the pipe to the
        child above).
        *direction* — ``1`` to extend the horizontal RIGHTWARD by one
        lane width, ``-1`` to extend LEFTWARD, ``0`` for no horizontal
        stub.  The renderer in ``graph_panel._draw_cell_row`` uses
        this to bridge the ``lane_w / 2`` gap between the commit-
        centred vertical pipe and the between-lanes horizontal
        connector at the merge commit's row.
        """
        return CellInfo(
            CellType.CROSS,
            color_index=h_color,
            pipe_color_index=p_color,
            direction=direction,
        )


@dataclass
class GraphNode:
    """A row in the commit graph — either a real commit, an uncommitted-changes
    marker, or a fork-connector row (``commit is None``)."""

    commit: CommitInfo | None = None
    lane: int = 0
    color_index: int = 0
    branch_names: list[str] = field(default_factory=list)
    is_head: bool = False
    is_uncommitted: bool = False
    uncommitted_count: int | None = None
    cells: list[CellInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        commit_dict = None
        if self.commit is not None:
            commit_dict = {
                "sha": self.commit.sha,
                "short_sha": self.commit.short_sha,
                "subject": _subject(self.commit.message),
                "author_name": self.commit.author_name,
                "author_email": self.commit.author_email,
                "author_time": self.commit.author_time,
                "parents": list(self.commit.parents),
                "kind": self.commit.kind,
            }
        return {
            "commit": commit_dict,
            "lane": self.lane,
            "color_index": self.color_index,
            "branch_names": list(self.branch_names),
            "is_head": self.is_head,
            "is_uncommitted": self.is_uncommitted,
            "uncommitted_count": self.uncommitted_count,
            "cells": [c.to_dict() for c in self.cells],
        }


@dataclass
class GraphLayout:
    """Complete graph layout."""

    nodes: list[GraphNode]
    max_lane: int


# ---------------------------------------------------------------------------
# ColorAssigner
# ---------------------------------------------------------------------------


class ColorAssigner:
    """Manages allocation of colour indices for graph lanes.

    Colour assignment is **deterministic by branch name** (``_pick_branch_color``)
    so the same branch always gets the same colour across sessions.
    When a commit carries no branch name (mid-history commit) the lane
    simply re-uses the colour that was already assigned to it.
    """

    def __init__(self) -> None:
        self._lane_colors: dict[int, int] = {}
        self._used_colors: set[int] = set()
        self._main_lane: int | None = None
        self._main_color: int = 1  # main/master → blue via overrides
        # Legacy bookkeeping retained for compatibility with the old lane API.
        self._in_fork: bool = False

    # -- public API --------------------------------------------------------

    def advance_row(self) -> None:
        """Called at the start of each commit row."""
        pass

    def is_main_lane(self, lane: int) -> bool:
        return self._main_lane == lane

    def get_main_color(self) -> int:
        return self._main_color

    def continue_lane(self, lane: int) -> int:
        """Return the colour for *lane*, assigning one if necessary."""
        if lane in self._lane_colors:
            return self._lane_colors[lane]
        return self._pick_fallback(lane)

    def assign_main_color(self, lane: int, branch_name: str | None = None) -> int:
        """Reserve the main-lane colour, derived from *branch_name*."""
        self._main_lane = lane
        color = _pick_branch_color(branch_name or "main")
        self._main_color = color
        self._set_lane_color(lane, color)
        return color

    def assign_color(self, lane: int, branch_name: str | None = None) -> int:
        """Allocate a colour for *lane* derived from *branch_name*."""
        if branch_name:
            color = _pick_branch_color(branch_name)
        else:
            color = self._pick_fallback(lane)
        self._set_lane_color(lane, color)
        return color

    def assign_fork_sibling_color(self, lane: int, branch_name: str | None = None) -> int:
        """Allocate a colour for a fork-sibling lane."""
        return self.assign_color(lane, branch_name)

    def begin_fork(self) -> None:
        """Legacy hook retained for compatibility with old callers."""
        self._in_fork = True

    def end_fork(self) -> None:
        """Legacy hook retained for compatibility with old callers."""
        self._in_fork = False

    def release_lane(self, lane: int) -> None:
        """Free *lane*'s colour so it can be reused."""
        if lane in self._lane_colors:
            self._used_colors.discard(self._lane_colors.pop(lane))

    # -- internals ---------------------------------------------------------

    def _pick_fallback(self, lane: int) -> int:
        """Sequential fallback when no branch name is available."""
        for offset in range(len(BRANCH_PALETTE)):
            candidate = (lane + offset) % len(BRANCH_PALETTE)
            if candidate not in self._used_colors:
                self._used_colors.add(candidate)
                return candidate
        return lane % len(BRANCH_PALETTE)

    def _set_lane_color(self, lane: int, color: int) -> None:
        self._lane_colors[lane] = color
        self._used_colors.add(color)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_graph(
    commits: list[CommitInfo],
    branches: list[BranchInfo],
    uncommitted_count: int | None = None,
    head_commit_sha: str | None = None,
) -> GraphLayout:
    """Build a cell-based graph layout from *commits* (newest first).

    Parameters
    ----------
    commits:
        Commit history, newest first.
    branches:
        All known branches (local + remote).
    uncommitted_count:
        Pass a non-negative integer when there are uncommitted changes and
        a synthetic WIP node should appear above HEAD.  ``None`` means
        no uncommitted node.
    head_commit_sha:
        The SHA that HEAD points to.  Used to position the uncommitted
        node correctly.
    """
    if not commits:
        if uncommitted_count is not None:
            return GraphLayout(
                nodes=[
                    GraphNode(
                        commit=None,
                        lane=0,
                        color_index=UNCOMMITTED_COLOR_INDEX,
                        is_uncommitted=True,
                        uncommitted_count=uncommitted_count,
                        cells=[CellInfo.commit(UNCOMMITTED_COLOR_INDEX)],
                    )
                ],
                max_lane=0,
            )
        return GraphLayout(nodes=[], max_lane=0)

    # SHA -> list of branch names
    oid_to_branches: dict[str, list[str]] = {}
    head_oid: str | None = None
    for branch in branches:
        if branch.target_sha:
            oid_to_branches.setdefault(branch.target_sha, []).append(branch.name)
            if branch.is_head:
                head_oid = branch.target_sha

    # Fallback: use the parameter if branches didn't yield a HEAD
    if head_oid is None:
        head_oid = head_commit_sha

    # SHA -> row index
    oid_to_row: dict[str, int] = {c.sha: i for i, c in enumerate(commits)}

    # ``nodes_by_sha`` maps a commit's SHA to its index in the
    # ``nodes`` list built by the main loop below.  R3.1 (P1):
    # replacing the previous ``any(n.commit.sha == ...)`` linear
    # scans over the (already-processed) ``nodes`` list — O(n²) on
    # a 5 000-commit repo.  The dict is updated every time
    # ``nodes.append(...)`` runs so callers always see a current
    # snapshot.
    nodes_by_sha: dict[str, int] = {}

    # Detect fork points (commits with 2+ children in the visible history)
    parent_children: dict[str, list[str]] = {}
    for commit in commits:
        for parent_sha in commit.parents:
            if parent_sha in oid_to_row:
                parent_children.setdefault(parent_sha, []).append(commit.sha)
    fork_points: set[str] = {
        parent for parent, children in parent_children.items() if len(children) >= 2
    }

    # Lane tracking: each lane holds the SHA it is currently tracking (or None)
    lanes: list[str | None] = []
    nodes: list[GraphNode] = []
    max_lane: int = 0

    color_assigner = ColorAssigner()
    oid_color_index: dict[str, int] = {}
    lane_color_index: dict[int, int] = {}

    for commit in commits:
        color_assigner.advance_row()

        # Find the lane tracking this commit OID (if any)
        commit_lane_opt: int | None = None
        for i, lo in enumerate(lanes):
            if lo == commit.sha:
                commit_lane_opt = i
                break

        lane: int
        if commit_lane_opt is not None:
            lane = commit_lane_opt
        else:
            empty = _find_empty_lane(lanes)
            if empty is not None:
                lane = empty
            else:
                lanes.append(None)
                lane = len(lanes) - 1

        # --- fork point handling: multiple lanes track the same commit ---
        fork_lanes: list[int] = [i for i, lo in enumerate(lanes) if lo == commit.sha]
        # Snapshot the colour of every fork lane NOW, before the
        # fork-connector handling releases those lanes. The CROSS
        # cell placed at a fork-merge point needs the lane's
        # original colour (the child's branch colour) for the
        # vertical-up pipe; once ``lane_color_index.pop(ml)`` runs
        # below the colour is gone and the CROSS cell would fall
        # back to the second parent's colour, blending the two
        # connections visually.
        fork_lane_colors: dict[int, int] = {ml: lane_color_index.get(ml, ml) for ml in fork_lanes}
        # Track the fork lanes (children lanes) so that, when this
        # commit is also a merge, a second parent landing on one of
        # these lanes can be drawn with a CROSS cell at the
        # intersection. The set is captured BEFORE the fork connector
        # cells are merged into the commit's row and BEFORE the lanes
        # are released, because we need to know which lanes are about
        # to be freed (those lanes have a child of this commit).
        fork_lane_set: set[int] = set(fork_lanes)

        # ``child_lane_set`` collects the lanes of every child of
        # *this* commit. ``fork_lane_set`` only knows about lanes that
        # are tracking the commit SHA right now; for a fork-merge
        # commit whose children landed on different lanes earlier
        # (e.g. ``gpt-researcher`` ``409b8b60`` — children ``18d4051``
        # on lane 0 and ``3080b0c4`` on lane 1, only the latter left
        # ``lanes[0] == commit.sha`` at processing time), the second
        # parent can collide with a child's lane that ``fork_lane_set``
        # does NOT cover. Building the child-lane set from
        # ``parent_children`` + the already-processed ``nodes`` list
        # is the only way to detect every such case.
        #
        # R3.1 (P1): use ``nodes_by_sha`` (O(1) dict lookup) instead
        # of the previous ``for n in nodes: if n.commit.sha == ...``
        # linear scan that made the per-commit block O(n).
        child_lane_set: set[int] = set()
        for child_sha in parent_children.get(commit.sha, []):
            child_idx = nodes_by_sha.get(child_sha)
            if child_idx is None:
                continue
            child_lane_set.add(nodes[child_idx].lane)
        fork_lane_set |= child_lane_set
        # Also snapshot the colour of any child lane not already in
        # ``fork_lane_colors``. ``oid_color_index`` carries the
        # commit's assigned colour even after the lane was released.
        for cl in child_lane_set:
            if cl not in fork_lane_colors:
                # Find the child SHA again to look up its colour.
                for child_sha in parent_children.get(commit.sha, []):
                    child_idx = nodes_by_sha.get(child_sha)
                    if child_idx is None:
                        continue
                    child_node = nodes[child_idx]
                    if child_node.lane != cl:
                        continue
                    fork_lane_colors[cl] = lane_color_index.get(cl)
                    if fork_lane_colors[cl] is None:
                        fork_lane_colors[cl] = oid_color_index.get(child_sha, cl)
                    break
        fork_merging_cells: list[CellInfo] | None = None
        fork_merging_lanes: list[tuple[int, int]] = []
        if len(fork_lanes) >= 2:
            main_lane = min(fork_lanes)
            merging_lanes: list[tuple[int, int]] = []
            for fl in fork_lanes:
                if fl == main_lane:
                    continue
                color = lane_color_index.get(fl)
                if color is None:
                    color = oid_color_index.get(commit.sha, fl)
                merging_lanes.append((fl, color))

            for ml, _ in merging_lanes:
                if ml > max_lane:
                    max_lane = ml
            if main_lane > max_lane:
                max_lane = main_lane

            main_color = lane_color_index.get(main_lane)
            if main_color is None:
                main_color = oid_color_index.get(commit.sha, main_lane)
            fork_merging_cells = _build_fork_connector_cells(
                main_lane,
                main_color,
                merging_lanes,
                lanes,
                oid_color_index,
                lane_color_index,
                max_lane,
            )
            fork_merging_lanes = list(merging_lanes)

            for ml, _ in merging_lanes:
                if ml < len(lanes):
                    lanes[ml] = None
                    color_assigner.release_lane(ml)
                    lane_color_index.pop(ml, None)

        # --- determine colour index ---
        commit_branch_names = oid_to_branches.get(commit.sha, [])
        primary_branch = commit_branch_names[0] if commit_branch_names else None

        commit_color_index: int
        if commit_lane_opt is not None:
            # When the commit's SHA is already tracking on a lane (set
            # earlier by a merge commit's parent processing), prefer
            # the colour derived from the commit's own branch name
            # over the lane-cache colour the merge pre-assigned.
            # Without this, a side-branch tip that lives below a
            # merge commit gets drawn in the merge's fallback
            # colour instead of its own ``_pick_branch_color``
            # colour (e.g. the ``gpt-researcher``
            # ``3mk4yl/fix-dict-unhashable-bug`` tip rendered in
            # GREEN instead of GOLD).
            if primary_branch is not None:
                commit_color_index = color_assigner.assign_color(
                    lane, primary_branch
                )
            else:
                commit_color_index = color_assigner.continue_lane(lane)
        elif not nodes or all(n.commit is None for n in nodes):
            commit_color_index = color_assigner.assign_main_color(lane, primary_branch)
        else:
            commit_color_index = color_assigner.assign_color(lane, primary_branch)
        oid_color_index[commit.sha] = commit_color_index
        lane_color_index[lane] = commit_color_index

        # Clear this commit's lane
        if lane < len(lanes):
            lanes[lane] = None

        # --- process parents ---
        valid_parents: list[str] = [p for p in commit.parents if p in oid_to_row]

        fork_sibling_color: int | None = None

        if len(valid_parents) >= 2:
            color_assigner.begin_fork()

        parent_lanes: list[tuple[str, int, bool, int, bool]] = []
        # (parent_sha, parent_lane, was_existing, color, already_shown)

        for parent_idx, parent_sha in enumerate(valid_parents):
            existing_parent_lane: int | None = None
            for i, lo in enumerate(lanes):
                if lo == parent_sha:
                    existing_parent_lane = i
                    break

            # R3.1 (P1): the previous implementation did a linear
            # ``any(n.commit.sha == parent_sha for n in nodes)`` which
            # made the whole loop O(n²) on 5 000-commit repos.  The
            # ``nodes_by_sha`` dict lets us resolve any "is this
            # commit already rendered?" question in O(1).
            parent_already_shown = nodes_by_sha.get(parent_sha) is not None

            parent_lane: int
            was_existing: bool
            parent_color: int

            if existing_parent_lane is not None:
                if (
                    parent_idx == 0
                    and parent_sha in fork_points
                    and len(valid_parents) >= 2
                ):
                    # Merge commit whose first parent is a fork point:
                    # keep parent on the same lane as commit and
                    # force ``final_color_index`` to ``main_color`` so
                    # the merge reads as the main line's colour.
                    lanes[lane] = parent_sha
                    main_c = color_assigner.get_main_color()
                    color = main_c if color_assigner.is_main_lane(lane) else commit_color_index
                    fork_sibling_color = color
                    lane_color_index[lane] = color
                    parent_lane = lane
                    was_existing = False
                    parent_color = color
                elif parent_idx == 0 and parent_sha in fork_points:
                    # Single-parent commit whose parent sits on a
                    # fork-point lane (the legacy path).  Keep
                    # parent on the same lane as commit, but DO NOT
                    # overwrite the lane colour with the main line's
                    # colour — that clobbers a side-branch tip's own
                    # ``commit_color_index`` (e.g. the
                    # ``3mk4yl/fix-dict-unhashable-bug`` GOLD tip
                    # would otherwise be repainted as BLUE/master).
                    lanes[lane] = parent_sha
                    parent_lane = lane
                    was_existing = False
                    parent_color = commit_color_index
                else:
                    color = lane_color_index.get(existing_parent_lane)
                    if color is None:
                        color = oid_color_index.get(parent_sha, existing_parent_lane)
                    parent_lane = existing_parent_lane
                    was_existing = True
                    parent_color = color
            elif parent_idx == 0:
                lanes[lane] = parent_sha
                oid_color_index[parent_sha] = commit_color_index
                parent_lane = lane
                was_existing = False
                parent_color = commit_color_index
            else:
                empty = _find_empty_lane(lanes)
                if empty is not None:
                    new_lane = empty
                else:
                    lanes.append(None)
                    new_lane = len(lanes) - 1
                lanes[new_lane] = parent_sha
                parent_branch_names = oid_to_branches.get(parent_sha, [])
                parent_branch = parent_branch_names[0] if parent_branch_names else None
                new_color = color_assigner.assign_fork_sibling_color(new_lane, parent_branch)
                oid_color_index[parent_sha] = new_color
                # Deliberately do NOT overwrite ``lane_color_index[new_lane]``
                # with ``new_color`` when ``new_color`` is a fallback
                # (``primary_branch is None``).  Doing so poisons the
                # lane cache for every later commit that lands on this
                # lane via ``continue_lane()`` and renders the mainline
                # around a merge commit in the fork-sibling fallback
                # colour (e.g. ``gpt-researcher`` mainline around
                # ``31b22352`` drawn in PINK instead of BLUE/master).
                # The branch-name case is harmless: when the parent
                # itself is processed its ``commit_lane_opt`` branch
                # calls ``assign_color(lane, primary_branch)`` and
                # writes the correct lane colour anyway.
                if parent_branch is not None:
                    lane_color_index[new_lane] = new_color
                parent_lane = new_lane
                was_existing = False
                parent_color = new_color

            parent_lanes.append(
                (parent_sha, parent_lane, was_existing, parent_color, parent_already_shown)
            )

        final_color_index = (
            fork_sibling_color if fork_sibling_color is not None else commit_color_index
        )

        # When a commit is BOTH a fork point (has children) AND
        # has a second parent whose lane coincides with a child's
        # lane, GitKraken-style rendering keeps the second
        # parent on the SAME lane as the child and lets a
        # dedicated ``CROSS`` cell at the intersection make
        # both connections (merge from below + branch to above)
        # visually unambiguous. The second parent therefore
        # stays on its natural lane; nothing is reshuffled here.
        # See ``_build_row_cells`` for the ``CROSS`` placement.
        _ = fork_merging_cells  # intentional no-op; see comment above

        if lane > max_lane:
            max_lane = lane
        for _, pl, _, _, _ in parent_lanes:
            if pl > max_lane:
                max_lane = pl

        # Detect lane merge
        lane_merge: tuple[int, int] | None = None
        for _, pl, was_existing, color, _ in parent_lanes:
            if was_existing and pl != lane:
                lane_merge = (pl, color)
                break

        # Build cells
        cells = _build_row_cells(
            lane,
            final_color_index,
            parent_lanes,
            lanes,
            oid_color_index,
            lane_color_index,
            max_lane,
            fork_lane_set=fork_lane_set,
            fork_lane_colors=fork_lane_colors,
        )

        # Merge fork connector cells into the commit's own cells so the
        # branching is rendered directly from the fork point commit node.
        # The fork connector already supplies the correct horizontal and
        # pipe colours, so the cells are used as-is.
        # EXCEPTION: a CROSS cell already placed by ``_build_row_cells``
        # at a fork-merge point (second parent sharing a fork lane)
        # must NOT be overwritten by the fork connector's TEE_UP /
        # MERGE_LEFT — the cross carries the merge-from-below +
        # branch-to-above semantic that the curve alone would lose.
        # The CROSS cell occupies the lane centre (col = lane * 2), so
        # the fork connector's TEE_UP at that col is dropped, but the
        # rest of the connector (horizontal at lane centres 1, 3, 5…,
        # TEE_UP / MERGE_LEFT at other fork lanes, and PIPE at
        # unrelated lanes) is merged in.
        if fork_merging_cells is not None:
            # The fork connector was built BEFORE the commit's own
            # colour was decided: its ``main_color`` came from the
            # lane cache, i.e. the colour of the child that registered
            # the main lane.  The vertical segment under the commit
            # dot must match the pipe that continues to the first
            # parent (``final_color_index``, which ``lane_color_index``
            # carries into the rows below) — otherwise the half-cell
            # under the commit is painted in a child branch's colour
            # (e.g. kilocode ``22149292``).
            main_fc_idx = lane * 2
            if main_fc_idx < len(fork_merging_cells):
                fc_main = fork_merging_cells[main_fc_idx]
                if fc_main.cell_type == CellType.TEE_RIGHT:
                    if fc_main.pipe_color_index != final_color_index:
                        fork_merging_cells[main_fc_idx] = CellInfo(
                            fc_main.cell_type,
                            color_index=fc_main.color_index,
                            pipe_color_index=final_color_index,
                            direction=fc_main.direction,
                        )
                elif (
                    fc_main.cell_type == CellType.PIPE
                    and fc_main.color_index != final_color_index
                ):
                    fork_merging_cells[main_fc_idx] = CellInfo.pipe(final_color_index)
            # Priority rule (update2 B4): when the row carries CROSS
            # cells (a second parent landed on a fork lane) the MERGE
            # connector owns the horizontal track between the commit
            # and each CROSS — the forking branch's colour appears
            # only going up from the CROSS and on fork segments beyond
            # it.  ``_build_row_cells`` already wrote the merge
            # connector in the second parent's colour, so the fork
            # connector must not overwrite those columns.
            merge_own_cols: set[int] = set()
            for col_e, cell_e in enumerate(cells):
                if cell_e.cell_type != CellType.CROSS:
                    continue
                if cell_e.direction == -1:
                    merge_own_cols.update(range(lane * 2, col_e))
                elif cell_e.direction == 1:
                    merge_own_cols.update(range(col_e + 1, lane * 2 + 1))
            while len(cells) < len(fork_merging_cells):
                cells.append(CellInfo.empty())
            for fci, fc in enumerate(fork_merging_cells):
                if fc.cell_type == CellType.EMPTY:
                    continue
                existing = cells[fci]
                if existing.cell_type in (
                    CellType.BRANCH_RIGHT,
                    CellType.BRANCH_LEFT,
                    CellType.MERGE_LEFT,
                    CellType.MERGE_RIGHT,
                    CellType.CROSS,
                ):
                    continue
                if fci in merge_own_cols and existing.cell_type in (
                    CellType.TEE_RIGHT,
                    CellType.TEE_LEFT,
                    CellType.HORIZONTAL,
                    CellType.HORIZONTAL_PIPE,
                ):
                    continue
                cells[fci] = fc

            # --- half-cell cleanup past fork-connector bends ---------
            # Even/odd column geometry makes every horizontal cell
            # paint half a cell into the next column's span.  For most
            # bends the bend cell itself repaints that overshoot
            # (TEE_UP draws its own horizontal).  Two bend kinds do
            # not, leaving a foreign-coloured half-cell past the bend:
            #
            # * ``CROSS`` with ``direction == -1``: the stub covers the
            #   span arriving from the commit in the merge colour, but
            #   the left neighbour cell's right half sticks out past
            #   the bend in the forking branch's colour.  That half
            #   belongs to the NEXT fork segment (e.g. the stash lane)
            #   — recolour it (sql-skill ``8ee78fc``, update2 B1).
            #
            # * ``MERGE_LEFT`` (upward bend, end of the segment): a
            #   foreign horizontal surviving from the parent connector
            #   paints half a cell into the void past the bend —
            #   drop the horizontal part (keep any vertical pipe)
            #   (sql-skill ``460f62c``, update2 B2).
            for mi, (ml, ml_color) in enumerate(fork_merging_lanes):
                bend_idx = ml * 2
                prev_idx = bend_idx - 1
                if bend_idx >= len(cells) or prev_idx < 0:
                    continue
                bend = cells[bend_idx]
                prev = cells[prev_idx]
                if bend.cell_type == CellType.CROSS and bend.direction == -1:
                    next_color = (
                        fork_merging_lanes[mi + 1][1]
                        if mi + 1 < len(fork_merging_lanes)
                        else bend.color_index
                    )
                    if (
                        prev.cell_type == CellType.HORIZONTAL
                        and prev.color_index != next_color
                    ):
                        cells[prev_idx] = CellInfo.horizontal(next_color)
                    elif (
                        prev.cell_type == CellType.HORIZONTAL_PIPE
                        and prev.color_index != next_color
                    ):
                        cells[prev_idx] = CellInfo.horizontal_pipe(
                            next_color, prev.pipe_color_index
                        )
                    # The fork connector stopped one cell early before
                    # its next bend assuming an intermediate pipe/tee
                    # covers the gap; a CROSS bend paints nothing
                    # rightward, so fill the hole up to the next bend.
                    # The last filled cell (immediately left of the
                    # bend) is right-trimmed (``direction=-1``) — a
                    # full-width odd cell would paint a half-cell past
                    # the bend into the void (kilocode ``9c0e4f76``
                    # col 11, ``5c7978c2`` col 11).
                    if mi + 1 < len(fork_merging_lanes):
                        next_bend_idx = fork_merging_lanes[mi + 1][0] * 2
                        for gap_idx in range(bend_idx + 1, next_bend_idx):
                            if gap_idx >= len(cells):
                                break
                            gap_cell = cells[gap_idx]
                            trim = -1 if gap_idx == next_bend_idx - 1 else 0
                            if gap_cell.cell_type == CellType.EMPTY:
                                cells[gap_idx] = CellInfo.horizontal(
                                    next_color, direction=trim
                                )
                            elif gap_cell.cell_type == CellType.PIPE:
                                cells[gap_idx] = CellInfo.horizontal_pipe(
                                    next_color, gap_cell.color_index, direction=trim
                                )
                elif bend.cell_type == CellType.MERGE_LEFT:
                    if (
                        prev.cell_type == CellType.HORIZONTAL
                        and prev.color_index != ml_color
                    ):
                        cells[prev_idx] = CellInfo.empty()
                    elif (
                        prev.cell_type == CellType.HORIZONTAL_PIPE
                        and prev.color_index != ml_color
                    ):
                        cells[prev_idx] = CellInfo.pipe(prev.pipe_color_index)

        branch_names = oid_to_branches.get(commit.sha, [])
        is_head = head_oid is not None and head_oid == commit.sha

        nodes.append(
            GraphNode(
                commit=commit,
                lane=lane,
                color_index=final_color_index,
                branch_names=branch_names,
                is_head=is_head,
                cells=cells,
            )
        )
        # R3.1 (P1): keep the O(1) lookup dict in sync with the
        # ``nodes`` list.  Appended at the same index so a future
        # ``nodes_by_sha[commit.sha]`` returns the position of the
        # node we just inserted.
        nodes_by_sha[commit.sha] = len(nodes) - 1

        # --- handle lane merging ---
        if lane_merge is not None:
            parent_lane_m, _ = lane_merge
            if parent_lane_m < lane:
                main_l = parent_lane_m
                ending_l = lane
            else:
                main_l = lane
                ending_l = parent_lane_m

            ending_oid = lanes[ending_l] if ending_l < len(lanes) else None
            ending_already_shown = True
            if ending_oid is not None:
                # R3.1 (P1): ``nodes_by_sha`` replaces the previous
                # ``any(n.commit.sha == ending_oid for n in nodes)``
                # linear scan.
                ending_already_shown = nodes_by_sha.get(ending_oid) is not None

            continues_down = not ending_already_shown

            if ending_l < len(lanes):
                first_parent_on_ending = False
                if parent_lanes:
                    first_parent_on_ending = parent_lanes[0][1] == ending_l

                if not first_parent_on_ending and not continues_down:
                    if ending_l < len(lanes) and lanes[ending_l] is not None:
                        ending_oid_val = lanes[ending_l]
                        if main_l < len(lanes) and lanes[main_l] is None:
                            lanes[main_l] = ending_oid_val
                    lanes[ending_l] = None
                    color_assigner.release_lane(ending_l)
                    lane_color_index.pop(ending_l, None)

    # --- Rebalance stashes above HEAD onto offset lanes -------------------
    # Without this step, a stash whose first parent is HEAD inherits
    # lane 0 from the main loop (its parent is HEAD which lives on lane
    # 0), and the WIP node below then has to take the next free offset
    # lane — visually putting the WIP marker on a side branch.
    #
    # The rebalance moves every stash whose first parent is HEAD to a
    # fresh offset lane (1, 2, 3, …), updates the stash's own row to
    # draw a TEE_LEFT (or COMMIT + HORIZONTAL) connection back to HEAD,
    # and re-renders HEAD's fork connector so it joins every lane that
    # has a branch into HEAD — including the freshly-shifted stashes
    # and any pre-existing branches the main loop already placed.
    max_lane = _rebalance_stashes_for_wip(nodes, head_oid, max_lane, uncommitted_count)

    # --- Insert uncommitted changes node ---
    if uncommitted_count is not None and uncommitted_count >= 0:
        head_node_idx: int | None = None
        if head_oid is not None:
            for i, n in enumerate(nodes):
                if n.commit is not None and n.commit.sha == head_oid:
                    head_node_idx = i
                    break

        if head_node_idx is not None:
            head_lane = nodes[head_node_idx].lane

            head_lane_available = _is_wip_compatible(nodes, head_node_idx, head_lane)

            uncommitted_lane: int
            if head_lane_available:
                uncommitted_lane = head_lane
            else:
                best_lane = max_lane + 1
                best_distance = 999999
                for candidate in range(max_lane + 2):
                    available = True
                    c_idx = candidate * 2
                    for i in range(head_node_idx):
                        if c_idx < len(nodes[i].cells):
                            if nodes[i].cells[c_idx].cell_type != CellType.EMPTY:
                                available = False
                                break
                        else:
                            break
                    if available:
                        dist = abs(candidate - head_lane)
                        if dist < best_distance:
                            best_distance = dist
                            best_lane = candidate
                uncommitted_lane = best_lane

            if uncommitted_lane > max_lane:
                max_lane = uncommitted_lane

            # Ensure all nodes have enough cells
            required_cells = (max_lane + 1) * 2
            for node in nodes:
                while len(node.cells) < required_cells:
                    node.cells.append(CellInfo.empty())

            # Add Pipe to all nodes before HEAD
            pipe_cell_idx = uncommitted_lane * 2
            for i in range(head_node_idx):
                if nodes[i].cells[pipe_cell_idx].cell_type == CellType.EMPTY:
                    nodes[i].cells[pipe_cell_idx] = CellInfo.pipe(UNCOMMITTED_COLOR_INDEX)

            # Connector from HEAD to uncommitted lane if different
            if uncommitted_lane != head_lane:
                head_cell_idx2 = head_lane * 2
                uncommitted_cell_idx = uncommitted_lane * 2

                if uncommitted_lane > head_lane:
                    for col in range(head_cell_idx2 + 1, uncommitted_cell_idx):
                        if nodes[head_node_idx].cells[col].cell_type == CellType.EMPTY:
                            nodes[head_node_idx].cells[col] = CellInfo.horizontal(
                                UNCOMMITTED_COLOR_INDEX,
                            )
                    nodes[head_node_idx].cells[uncommitted_cell_idx] = CellInfo.merge_left(
                        UNCOMMITTED_COLOR_INDEX,
                    )
                else:
                    for col in range(uncommitted_cell_idx + 1, head_cell_idx2):
                        if nodes[head_node_idx].cells[col].cell_type == CellType.EMPTY:
                            nodes[head_node_idx].cells[col] = CellInfo.horizontal(
                                UNCOMMITTED_COLOR_INDEX,
                            )
                    nodes[head_node_idx].cells[uncommitted_cell_idx] = CellInfo.merge_right(
                        UNCOMMITTED_COLOR_INDEX,
                    )

            uncommitted_cells: list[CellInfo] = [CellInfo.empty() for _ in range(required_cells)]
            uncommitted_cells[uncommitted_lane * 2] = CellInfo.commit(UNCOMMITTED_COLOR_INDEX)

            nodes.insert(
                0,
                GraphNode(
                    commit=None,
                    lane=uncommitted_lane,
                    color_index=UNCOMMITTED_COLOR_INDEX,
                    is_uncommitted=True,
                    uncommitted_count=uncommitted_count,
                    cells=uncommitted_cells,
                ),
            )

    return GraphLayout(nodes=nodes, max_lane=max_lane)


# ---------------------------------------------------------------------------
# Cell building helpers
# ---------------------------------------------------------------------------


def _is_wip_compatible(
    nodes: list[GraphNode],
    head_node_idx: int,
    head_lane: int,
) -> bool:
    """Return True if a WIP node could sit on *head_lane* above HEAD.

    Lane 0 (the main line) is "free" for the WIP when no row above
    HEAD places something at that lane that would interrupt the
    vertical pipe leading from WIP down to HEAD.  Concretely:

    * ``EMPTY`` — trivially fine.
    * ``PIPE`` / ``TEE_*`` / ``MERGE_*`` / ``BRANCH_*`` / ``COMMIT`` —
      these all share a vertical line at the cell centre, so the WIP's
      vertical pipe continues through them without a visual break.
    * ``HORIZONTAL`` / ``HORIZONTAL_PIPE`` — these are *crossings*
      where the WIP's vertical pipe would be cut by a horizontal line
      coming from another lane (e.g. a branch from a sibling feature
      crossing the main line).  Those block the WIP.
    """
    head_cell_idx = head_lane * 2
    blocking = {CellType.HORIZONTAL, CellType.HORIZONTAL_PIPE}
    for i in range(head_node_idx):
        if head_cell_idx >= len(nodes[i].cells):
            continue
        if nodes[i].cells[head_cell_idx].cell_type in blocking:
            return False
    return True


def _rebalance_stashes_for_wip(
    nodes: list[GraphNode],
    head_oid: str | None,
    max_lane: int,
    uncommitted_count: int | None = None,
) -> int:
    """Move stash nodes above HEAD to offset lanes, freeing lane 0 for WIP.

    Stashes above HEAD whose first parent is HEAD are normally assigned
    lane 0 by the main loop (their parent HEAD lives on lane 0, so the
    stash inherits the lane when no other commit claims it first).  The
    WIP insertion step below then has to take the next free offset
    lane, which puts the WIP marker on a side branch.  This rebalance
    shifts every such stash to the next free offset lane (1, 2, 3, …)
    and re-draws the connection in both the stash's row (TEE_LEFT or
    COMMIT + HORIZONTAL) and HEAD's row (a fresh fork connector that
    joins all branches into HEAD, including the shifted stashes and
    any pre-existing branches the main loop already placed).

    The function is a no-op when there are no stashes above HEAD.

    ``uncommitted_count`` — pass the value the caller is about to use
    for WIP insertion so the rebalance knows whether the head-lane
    cell it clears will be refilled by a WIP pipe. Clearing the cell
    when no WIP is coming leaves the lane 0 line with an EMPTY cell
    that breaks its visual continuity above HEAD (see fix note in
    the per-stash loop below).

    Returns the updated ``max_lane``.
    """
    if head_oid is None:
        return max_lane

    head_node_idx: int | None = None
    for i, n in enumerate(nodes):
        if n.commit is not None and n.commit.sha == head_oid:
            head_node_idx = i
            break

    if head_node_idx is None:
        return max_lane

    # Find stash rows above HEAD whose first parent is HEAD — these are
    # the only stashes the rebalance needs to move.
    stash_indices: list[int] = [
        i
        for i in range(head_node_idx)
        if nodes[i].commit is not None
        and nodes[i].commit.kind == "stash"
        and nodes[i].commit.parents
        and nodes[i].commit.parents[0] == head_oid
    ]

    if not stash_indices:
        return max_lane

    stash_indices.sort()  # top to bottom in the rendered output

    head_node = nodes[head_node_idx]
    head_lane = head_node.lane
    head_color = head_node.color_index

    # Lanes already in use by *non-stash* commits above HEAD — the
    # stash gets the first offset lane (1, 2, 3, …) that does not
    # collide with one of these.  Stash lanes are excluded because
    # the rebalance is about to free them.
    used_lanes: set[int] = {
        nodes[i].lane
        for i in range(head_node_idx)
        if nodes[i].commit is None or nodes[i].commit.kind != "stash"
    }

    stash_assignments: list[tuple[int, int]] = []  # (stash_idx, target_lane)
    next_lane = max(1, head_lane + 1)
    for stash_idx in stash_indices:
        while next_lane in used_lanes:
            next_lane += 1
        stash_assignments.append((stash_idx, next_lane))
        used_lanes.add(next_lane)
        next_lane += 1

    new_max_lane = max(max_lane, max((t for _, t in stash_assignments), default=0))
    required_cells = (new_max_lane + 1) * 2
    for node in nodes:
        while len(node.cells) < required_cells:
            node.cells.append(CellInfo.empty())

    # --- move each stash to its new lane --------------------------------
    # Prepare a mapping from lane → owning stash's colour so PIPE cells
    # drawn at each lane use the correct colour (not all head_color).
    stash_assignments.sort(key=lambda x: x[0])  # top to bottom
    single_color_map: dict[int, int] = {}
    for stash_idx, target_lane in stash_assignments:
        single_color_map[target_lane] = nodes[stash_idx].color_index

    # Also seed the map with non-stash lanes above HEAD so intermediate
    # PIPE cells pick up pre-existing branch colours (e.g. a feature
    # branch on lane 1 while a stash ends up on lane 3).
    for i in range(head_node_idx):
        if nodes[i].lane not in single_color_map:
            single_color_map[nodes[i].lane] = nodes[i].color_index

    for stash_idx, target_lane in stash_assignments:
        stash = nodes[stash_idx]
        old_lane = stash.lane

        # Clear the stash's old COMMIT cell so the new position can take over.
        old_cell_idx = old_lane * 2
        if old_cell_idx < len(stash.cells):
            stash.cells[old_cell_idx] = CellInfo.empty()
        # Also handle the head-lane cell — three cases:
        #
        # 1. The stash was already on ``head_lane``: ``old_cell_idx`` IS
        #    ``head_cell_idx`` so the clear above already emptied it.
        # 2. The stash was on an offset lane: the cell at ``head_lane``
        #    is a PIPE drawn by the main loop's active-lane tracking
        #    and belongs to the lane 0 line above HEAD.
        # 3. WIP will be inserted: the WIP refill step only writes a new
        #    PIPE into EMPTY cells, so anything left in the way blocks it.
        #
        # Case (2) + no WIP is the visual bug we are fixing: clearing the
        # PIPE when nothing will refill it leaves an EMPTY cell that
        # severs the lane 0 line above HEAD.  With WIP (case 3) the clear
        # is required to make way for the uniform UNCOMMITTED pipe.
        has_wip = uncommitted_count is not None and uncommitted_count >= 0
        head_cell_idx = head_lane * 2
        if head_cell_idx < len(stash.cells) and head_cell_idx != old_cell_idx:
            if has_wip:
                stash.cells[head_cell_idx] = CellInfo.empty()
            # else: preserve the main-loop PIPE — it is the lane 0 line
            # passing through the stash's row.

        # When the stash moved *away* from ``head_lane``, the cell at
        # ``head_lane`` was just emptied (it was the stash's old COMMIT).
        # With WIP, the WIP insertion would have refilled it; without
        # WIP we restore a PIPE here so the lane 0 line above HEAD stays
        # continuous through every row.
        #
        # Skip the restore at the very top of the graph (``stash_idx ==
        # 0``): there is no row above to bridge to, so the PIPE would
        # be an orphan stub that extends ``node_radius`` pixels up into
        # the empty space above the topmost commit.  An EMPTY cell lets
        # the bridge from the row below terminate at the topmost row's
        # commit edge with no dangling stub.
        if (
            not has_wip
            and stash_idx > 0
            and head_cell_idx < len(stash.cells)
            and stash.cells[head_cell_idx].cell_type == CellType.EMPTY
        ):
            stash.cells[head_cell_idx] = CellInfo.pipe(head_color)

        # Update the stash's lane and draw the commit at its new lane.
        # No vertical PIPE is added above the stash — the commit's own
        # upward nub (node_radius) is sufficient, matching how the main
        # loop renders the topmost commit of a side branch.
        stash.lane = target_lane
        new_cell_idx = target_lane * 2
        stash_color = single_color_map[target_lane]
        stash.cells[new_cell_idx] = CellInfo.commit(stash_color)

        # Plain COMMIT at the stash's lane — no TEE_LEFT, no HORIZONTAL.
        # For non-adjacent lanes, add PIPE at intermediate lanes so the
        # gap bridge maintains vertical continuity through all rows
        # (matching how the main loop renders side branches between
        # regular commits — PIPE at every active lane).
        stash.cells[new_cell_idx] = CellInfo.commit(stash_color)
        for between_lane in range(head_lane + 1, target_lane):
            between_cell_idx = between_lane * 2
            if between_cell_idx >= len(stash.cells):
                continue
            existing = stash.cells[between_cell_idx]
            if existing.cell_type == CellType.EMPTY:
                lane_color = single_color_map.get(between_lane, head_color)
                stash.cells[between_cell_idx] = CellInfo.pipe(lane_color)

    # --- rebuild HEAD's fork connector ----------------------------------
    # Collect every branch above HEAD that shares HEAD as its first
    # parent — both the stashes we just shifted and any pre-existing
    # branches the main loop already placed on offset lanes.
    merging_lanes: list[tuple[int, int]] = []
    for i in range(head_node_idx):
        n = nodes[i]
        if n.commit is not None and n.commit.parents and n.commit.parents[0] == head_oid:
            merging_lanes.append((n.lane, n.color_index))
    merging_lanes.sort()

    active_lanes: list[str | None] = [None] * (new_max_lane + 1)
    fork_cells = _build_fork_connector_cells(
        head_lane,
        head_color,
        merging_lanes,
        active_lanes,
        {},
        {},
        new_max_lane,
    )

    # Overlay the fork connector on HEAD's row.  HEAD's PIPE at the
    # main lane is replaced by TEE_RIGHT, which keeps the vertical line
    # intact and adds the horizontal that starts the connector.
    for fci, fc in enumerate(fork_cells):
        if fc.cell_type != CellType.EMPTY:
            head_node.cells[fci] = fc

    return new_max_lane


def _build_row_cells(
    commit_lane: int,
    commit_color: int,
    parent_lanes: list[tuple[str, int, bool, int, bool]],
    active_lanes: list[str | None],
    oid_color_index: dict[str, int],
    lane_color_index: dict[int, int],
    max_lane: int,
    *,
    fork_lane_set: set[int] | None = None,
    fork_lane_colors: dict[int, int] | None = None,
) -> list[CellInfo]:
    """Build the cell list for one row.

    *fork_lane_set* — when provided, lanes in this set are treated as
    "fork lanes": lanes that host a CHILD of the current commit. When the
    second parent lands on one of those lanes (a fork-merge commit
    whose child and second parent share a lane), the cell at that lane
    is replaced with a :class:`CellType.CROSS` so the merge connector
    (horizontal from the commit) and the vertical pipe passing through
    (child above + second parent below) read as two distinct
    connections instead of one continuous line. GitKraken does the
    same — the second parent stays on the natural lane and a
    cross-junction cell marks the fork-merge point.

    *fork_lane_colors* — optional mapping of fork-lane → colour used
    for the CROSS cell's vertical-up pipe. Captured BEFORE the fork
    handling releases the lanes so the original child's branch
    colour survives. Falls back to ``parent_color`` if missing.
    """
    if fork_lane_set is None:
        fork_lane_set = set()
    if fork_lane_colors is None:
        fork_lane_colors = {}

    cells = [CellInfo.empty() for _ in range((max_lane + 1) * 2)]

    # Vertical lines for active lanes
    for lane_idx, lane_oid in enumerate(active_lanes):
        if lane_oid is not None and lane_idx != commit_lane:
            cell_idx = lane_idx * 2
            if cell_idx < len(cells):
                # ``0`` (GREEN) is a valid palette index, so the lookup
                # must use ``is None`` for the fallback — ``or`` would
                # treat 0 as "missing" and silently fall back to the
                # oid-derived colour (which is the *other* branch's
                # colour for a re-used lane).  See the
                # ``BUG_VISUAL_FEAT_PIPE_COLOR`` regression.
                color = lane_color_index.get(lane_idx)
                if color is None:
                    color = oid_color_index.get(lane_oid, lane_idx)
                cells[cell_idx] = CellInfo.pipe(color)

    # Commit node
    commit_cell_idx = commit_lane * 2
    if commit_cell_idx < len(cells):
        cells[commit_cell_idx] = CellInfo.commit(commit_color)

    # Connections to parents
    for parent_idx, (
        _parent_sha,
        parent_lane,
        was_existing,
        parent_color,
        already_shown,
    ) in enumerate(parent_lanes):
        if parent_lane == commit_lane:
            continue

        # Detect fork-merge point: this parent is the second parent
        # (or any non-first parent) AND lands on a fork lane (a lane
        # hosting one of the current commit's children). In that
        # situation a CROSS cell replaces the curve so the merge
        # connector reads as horizontal from the commit centre, and
        # the vertical pipes (up to child + down to second parent)
        # stay continuous through the cell.
        on_fork_lane = parent_idx >= 1 and parent_lane in fork_lane_set

        if on_fork_lane:
            end_idx = parent_lane * 2
            if end_idx < len(cells):
                # The horizontal is the merge connector (the second
                # parent's colour); the vertical-up is the child's
                # colour (the fork lane above); the vertical-down is
                # the second parent's colour (continuing down to the
                # second parent itself). Use the fork-lane snapshot
                # colour (captured before fork-lane release) for the
                # child, so the two pipe sections stay colour-coded
                # — gold for the branch-up, blue for the merge-down.
                child_color = fork_lane_colors.get(
                    parent_lane,
                    lane_color_index.get(parent_lane, parent_color),
                )
                # Direction of the merge connector at the fork-merge
                # CROSS cell: extend the horizontal toward the merge
                # commit so it bridges the ``lane_w / 2`` gap between
                # the commit-centred vertical pipe and the between-
                # lanes horizontal at col 1 / col 2*parent_lane-1.
                cross_dir = -1 if parent_lane > commit_lane else 1
                cells[end_idx] = CellInfo.cross(
                    parent_color,
                    child_color,
                    direction=cross_dir,
                )
            # The horizontal connector from the commit lane to this
            # lane is handled by the ``TEE_RIGHT``/``TEE_LEFT`` block
            # below. Continue so we still draw the connector.
            # (We intentionally do NOT add a BRANCH_LEFT/MERGE_LEFT
            # curve at this lane — CROSS replaces it.)
            if parent_lane > commit_lane:
                # Connection to the right.
                if commit_cell_idx < len(cells):
                    cells[commit_cell_idx] = CellInfo(
                        CellType.TEE_RIGHT,
                        color_index=parent_color,
                        pipe_color_index=commit_color,
                    )
                if parent_lane > commit_lane + 1:
                    for col in range(commit_lane * 2 + 1, parent_lane * 2 - 1):
                        if col < len(cells):
                            existing = cells[col]
                            if existing.cell_type == CellType.PIPE:
                                cells[col] = CellInfo.horizontal_pipe(
                                    parent_color,
                                    existing.color_index,
                                )
                            elif existing.cell_type == CellType.EMPTY:
                                cells[col] = CellInfo.horizontal(parent_color)
            else:
                # Connection to the left.
                if commit_cell_idx < len(cells):
                    cells[commit_cell_idx] = CellInfo(
                        CellType.TEE_LEFT,
                        color_index=parent_color,
                        pipe_color_index=commit_color,
                    )
                if parent_lane < commit_lane - 1:
                    for col in range(parent_lane * 2 + 1, commit_lane * 2 - 1):
                        if col < len(cells):
                            existing = cells[col]
                            if existing.cell_type == CellType.PIPE:
                                cells[col] = CellInfo.horizontal_pipe(
                                    parent_color,
                                    existing.color_index,
                                )
                            elif existing.cell_type == CellType.EMPTY:
                                cells[col] = CellInfo.horizontal(parent_color)
            continue

        if parent_lane > commit_lane:
            # Connection to the right.
            # Replace the COMMIT cell with TEE_RIGHT so the horizontal
            # HORIZONTAL cell sits at the mid of the commit lane and
            # leaves a visible gap (≈ ``node_radius`` pixels) between
            # the commit ellipse and the connector — a common symptom
            # on PR-merge commits where the second parent lives
            # multiple lanes away from the main line.
            if commit_cell_idx < len(cells):
                cells[commit_cell_idx] = CellInfo(
                    CellType.TEE_RIGHT,
                    color_index=parent_color,
                    pipe_color_index=commit_color,
                )
            if parent_lane > commit_lane + 1:
                for col in range(commit_lane * 2 + 1, parent_lane * 2 - 1):
                    if col < len(cells):
                        existing = cells[col]
                        if existing.cell_type == CellType.PIPE:
                            cells[col] = CellInfo.horizontal_pipe(
                                parent_color,
                                existing.color_index,
                            )
                        elif existing.cell_type == CellType.EMPTY:
                            cells[col] = CellInfo.horizontal(parent_color)

            end_idx = parent_lane * 2
            if end_idx < len(cells):
                if was_existing and already_shown:
                    cells[end_idx] = CellInfo.merge_left(parent_color)
                elif was_existing:
                    cells[end_idx] = CellInfo(
                        CellType.TEE_LEFT,
                        color_index=parent_color,
                        pipe_color_index=parent_color,
                    )
                else:
                    cells[end_idx] = CellInfo.branch_left(parent_color)
        else:
            # Connection to the left.
            # Replace the COMMIT cell with TEE_LEFT so the horizontal
            # line reaches the commit centre. Symmetric to the
            # rightward branch above.
            if commit_cell_idx < len(cells):
                cells[commit_cell_idx] = CellInfo(
                    CellType.TEE_LEFT,
                    color_index=parent_color,
                    pipe_color_index=commit_color,
                )
            if parent_lane < commit_lane - 1:
                for col in range(parent_lane * 2 + 1, commit_lane * 2 - 1):
                    if col < len(cells):
                        existing = cells[col]
                        if existing.cell_type == CellType.PIPE:
                            cells[col] = CellInfo.horizontal_pipe(
                                parent_color,
                                existing.color_index,
                            )
                        elif existing.cell_type == CellType.EMPTY:
                            cells[col] = CellInfo.horizontal(parent_color)

            start_idx = parent_lane * 2
            if start_idx < len(cells):
                if was_existing and already_shown:
                    cells[start_idx] = CellInfo.merge_right(parent_color)
                elif was_existing:
                    cells[start_idx] = CellInfo(
                        CellType.TEE_RIGHT,
                        color_index=parent_color,
                        pipe_color_index=parent_color,
                    )
                else:
                    cells[start_idx] = CellInfo.branch_right(parent_color)

    return cells


def _build_fork_connector_cells(
    main_lane: int,
    main_color: int,
    merging_lanes: list[tuple[int, int]],
    active_lanes: list[str | None],
    oid_color_index: dict[str, int],
    lane_color_index: dict[int, int],
    max_lane: int,
) -> list[CellInfo]:
    cells = [CellInfo.empty() for _ in range((max_lane + 1) * 2)]

    merging_lane_nums = sorted(ml for ml, _ in merging_lanes)

    # Main lane: PIPE (single merge) or TEE_RIGHT with first-merge
    # horizontal colour (multiple merges).
    main_cell_idx = main_lane * 2
    first_merge_color = merging_lanes[0][1] if merging_lanes else main_color
    if main_cell_idx < len(cells):
        if len(merging_lanes) == 1:
            cells[main_cell_idx] = CellInfo(
                CellType.TEE_RIGHT, color_index=first_merge_color, pipe_color_index=main_color
            )
        elif len(merging_lanes) >= 2:
            cells[main_cell_idx] = CellInfo(
                CellType.TEE_RIGHT, color_index=first_merge_color, pipe_color_index=main_color
            )
        else:
            cells[main_cell_idx] = CellInfo.pipe(main_color)

    # Vertical lines for active lanes (except main and merging)
    for lane_idx, lane_oid in enumerate(active_lanes):
        if lane_oid is not None and lane_idx != main_lane and lane_idx not in merging_lane_nums:
            cell_idx = lane_idx * 2
            if cell_idx < len(cells):
                # ``0`` (GREEN) is a valid palette index; use ``is None``
                # for the fallback.  See ``BUG_VISUAL_FEAT_PIPE_COLOR``.
                color = lane_color_index.get(lane_idx)
                if color is None:
                    color = oid_color_index.get(lane_oid, lane_idx)
                cells[cell_idx] = CellInfo.pipe(color)

    prev_lane = main_lane
    for idx, (merge_lane, merge_color) in enumerate(merging_lanes):
        is_rightmost = idx == len(merging_lanes) - 1
        is_adjacent = merge_lane == prev_lane + 1

        # Skip intermediate horizontals for the rightmost merge only
        # when it is adjacent (the previous cell's horizontal already
        # covers the gap).  For non-adjacent rightmost merges stop one
        # cell early --- the HORIZONTAL_PIPE on the intermediate lane
        # already reaches the merge point.
        if not is_rightmost or not is_adjacent:
            end_col = merge_lane * 2
            if is_rightmost and not is_adjacent:
                end_col -= 1
            for col in range(prev_lane * 2 + 1, end_col):
                if col < len(cells):
                    existing = cells[col]
                    if existing.cell_type == CellType.PIPE:
                        cells[col] = CellInfo.horizontal_pipe(merge_color, existing.color_index)
                    elif existing.cell_type in (CellType.EMPTY, CellType.HORIZONTAL):
                        cells[col] = CellInfo.horizontal(merge_color)

        end_idx = merge_lane * 2
        if end_idx < len(cells):
            if not is_rightmost:
                next_merge_color = merging_lanes[idx + 1][1]
                cells[end_idx] = CellInfo(
                    CellType.TEE_UP, color_index=next_merge_color, pipe_color_index=merge_color
                )
            else:
                cells[end_idx] = CellInfo.merge_left(merge_color)

        prev_lane = merge_lane

    return cells


def _find_empty_lane(lanes: list[str | None]) -> int | None:
    """Return the index of the first empty lane, or None."""
    for i, lo in enumerate(lanes):
        if lo is None:
            return i
    return None


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def graph_to_dicts(layout: GraphLayout) -> list[dict]:
    """Serialise a :class:`GraphLayout` to plain dicts for Qt signals."""
    return [n.to_dict() for n in layout.nodes]


# ---------------------------------------------------------------------------
# Old-graph compatibility: BranchRef + refs/branch_refs helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BranchRef:
    """A branch that points at a commit, with display metadata."""

    name: str
    is_head: bool
    is_remote: bool

    def to_dict(self) -> dict:
        return {"name": self.name, "is_head": self.is_head, "is_remote": self.is_remote}


def _build_refs_map(
    tags: list,
    head_target_sha: str | None,
) -> dict[str, list[str]]:
    """Map SHA -> list of ref chip labels (HEAD, tag names)."""
    result: dict[str, list[str]] = {}
    if head_target_sha:
        result.setdefault(head_target_sha, []).append("HEAD")
    for tag in tags:
        if tag.target_sha:
            result.setdefault(tag.target_sha, []).append(tag.name)
    return result


def _build_branch_refs_map(branches: list[BranchInfo]) -> dict[str, list[BranchRef]]:
    """Map SHA -> list of BranchRef for the left-hand branch column."""
    result: dict[str, list[BranchRef]] = {}
    for branch in branches:
        if not branch.target_sha:
            continue
        if not _is_valid_sha(branch.target_sha):
            continue
        result.setdefault(branch.target_sha, []).append(
            BranchRef(name=branch.name, is_head=branch.is_head, is_remote=branch.is_remote),
        )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _subject(message: str) -> str:
    """Return the first non-empty line of *message*, stripped."""
    for line in message.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _is_valid_sha(s: str) -> bool:
    """Return ``True`` for a full SHA-1 or SHA-256 hexadecimal object ID."""
    return len(s) in (40, 64) and all(c in "0123456789abcdefABCDEF" for c in s)


__all__ = [
    "BRANCH_PALETTE",
    "BranchRef",
    "CellInfo",
    "CellType",
    "ColorAssigner",
    "GraphLayout",
    "GraphNode",
    "HEAD_SPECIAL_COLOR_INDEX",
    "MAIN_COLOR_INDEX",
    "UNCOMMITTED_COLOR_INDEX",
    "build_graph",
    "graph_to_dicts",
    "_pick_branch_color",
]
