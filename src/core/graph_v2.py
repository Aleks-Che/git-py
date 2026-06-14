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

from src.core.models import BranchInfo, CommitInfo

UNCOMMITTED_COLOR_INDEX: int = 24
"""Special colour index reserved for the uncommitted-changes node."""

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
)
"""24-colour palette used by :class:`ColorAssigner` and the widget layer."""

# Hardcoded overrides ensure that important branches always get the same
# well-known colours, regardless of which repository is open.
_BRANCH_COLOR_OVERRIDES: dict[str, int] = {
    "main": 1,
    "master": 1,
    "develop": 0,
    "dev": 0,
}
"""Case-insensitive mapping from branch name # colour palette index."""

HEAD_SPECIAL_COLOR_INDEX: int = 8
"""Colour index used for commits that HEAD directly points to (orange)."""


def _pick_branch_color(name: str) -> int:
    """Return a deterministic palette index for *name*.

    Hardcoded overrides are checked first (case-insensitive); the
    remainder are hashed modulo the palette size.
    """
    lower = name.lower()
    override = _BRANCH_COLOR_OVERRIDES.get(lower)
    if override is not None:
        return override
    return abs(hash(lower)) % len(BRANCH_PALETTE)


# Alias kept for documentation cross-reference.
MAIN_COLOR_INDEX: int = 1  # "main"/"master" → blue via overrides


class CellType(IntEnum):
    """Atomic rendering element for one cell of a graph row.

    Integer values allow cheap serialisation (``cell.value``) for Qt signals.
    """

    EMPTY = 0
    PIPE = 1             # │ vertical line (active lane)
    COMMIT = 2           # ● commit node
    BRANCH_RIGHT = 3     # ╭ branch starts, goes right + down
    BRANCH_LEFT = 4      # ╮ branch starts, goes left + down
    MERGE_RIGHT = 5      # ╰ merge from right, goes up
    MERGE_LEFT = 6       # ╯ merge from left, goes up
    HORIZONTAL = 7       # ─ horizontal line
    HORIZONTAL_PIPE = 8  # ─┼─ horizontal crossing a vertical
    TEE_RIGHT = 9        # ├ T-junction right
    TEE_LEFT = 10        # ┤ T-junction left
    TEE_UP = 11          # ┴ T-junction up (fork middle lane)


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

    def to_dict(self) -> dict:
        d: dict = {"t": int(self.cell_type)}
        if self.cell_type == CellType.EMPTY:
            return d
        if self.cell_type in (
            CellType.HORIZONTAL_PIPE,
            CellType.TEE_RIGHT,
            CellType.TEE_LEFT,
            CellType.TEE_UP,
        ):
            d["c"] = self.color_index
            if self.pipe_color_index:
                d["p"] = self.pipe_color_index
        else:
            d["c"] = self.color_index
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
    def horizontal(color: int) -> CellInfo:
        return CellInfo(CellType.HORIZONTAL, color_index=color)

    @staticmethod
    def horizontal_pipe(h_color: int, p_color: int) -> CellInfo:
        return CellInfo(CellType.HORIZONTAL_PIPE, color_index=h_color, pipe_color_index=p_color)

    @staticmethod
    def tee_right(color: int) -> CellInfo:
        return CellInfo(CellType.TEE_RIGHT, color_index=color)

    @staticmethod
    def tee_left(color: int) -> CellInfo:
        return CellInfo(CellType.TEE_LEFT, color_index=color)

    @staticmethod
    def tee_up(color: int) -> CellInfo:
        return CellInfo(CellType.TEE_UP, color_index=color)


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
        self._in_fork = True

    def end_fork(self) -> None:
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
        fork_merging_cells: list[CellInfo] | None = None
        if len(fork_lanes) >= 2:
            main_lane = min(fork_lanes)
            merging_lanes: list[tuple[int, int]] = []
            for fl in fork_lanes:
                if fl == main_lane:
                    continue
                color = lane_color_index.get(fl) or oid_color_index.get(commit.sha, fl)
                merging_lanes.append((fl, color))

            for ml, _ in merging_lanes:
                if ml > max_lane:
                    max_lane = ml
            if main_lane > max_lane:
                max_lane = main_lane

            main_color = (
                lane_color_index.get(main_lane)
                or oid_color_index.get(commit.sha, main_lane)
            )
            fork_merging_cells = _build_fork_connector_cells(
                main_lane, main_color, merging_lanes, lanes,
                oid_color_index, lane_color_index, max_lane,
            )

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

            parent_already_shown = any(
                n.commit is not None and n.commit.sha == parent_sha for n in nodes
            )

            parent_lane: int
            was_existing: bool
            parent_color: int

            if existing_parent_lane is not None:
                if parent_idx == 0 and parent_sha in fork_points:
                    lanes[lane] = parent_sha
                    main_c = color_assigner.get_main_color()
                    color = (
                        main_c if color_assigner.is_main_lane(lane)
                        else commit_color_index
                    )
                    fork_sibling_color = color
                    lane_color_index[lane] = color
                    parent_lane = lane
                    was_existing = False
                    parent_color = color
                else:
                    color = (
                        lane_color_index.get(existing_parent_lane)
                        or oid_color_index.get(parent_sha, existing_parent_lane)
                    )
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
                lane_color_index[new_lane] = new_color
                parent_lane = new_lane
                was_existing = False
                parent_color = new_color

            parent_lanes.append(
                (parent_sha, parent_lane, was_existing, parent_color, parent_already_shown)
            )

        final_color_index = (
            fork_sibling_color if fork_sibling_color is not None
            else commit_color_index
        )

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
            lane, final_color_index, parent_lanes, lanes,
            oid_color_index, lane_color_index, max_lane,
        )

        # Merge fork connector cells into the commit's own cells so the
        # branching is rendered directly from the fork point commit node.
        # The fork connector already supplies the correct horizontal and
        # pipe colours, so the cells are used as-is.
        if fork_merging_cells is not None:
            while len(cells) < len(fork_merging_cells):
                cells.append(CellInfo.empty())
            for fci, fc in enumerate(fork_merging_cells):
                if fc.cell_type == CellType.EMPTY:
                    continue
                cells[fci] = fc

        branch_names = oid_to_branches.get(commit.sha, [])
        is_head = (head_oid is not None and head_oid == commit.sha)

        nodes.append(GraphNode(
            commit=commit,
            lane=lane,
            color_index=final_color_index,
            branch_names=branch_names,
            is_head=is_head,
            cells=cells,
        ))

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
                ending_already_shown = any(
                    n.commit is not None and n.commit.sha == ending_oid for n in nodes
                )

            continues_down = not ending_already_shown

            if ending_l < len(lanes):
                first_parent_on_ending = False
                if parent_lanes:
                    first_parent_on_ending = (parent_lanes[0][1] == ending_l)

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
    max_lane = _rebalance_stashes_for_wip(nodes, head_oid, max_lane)

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

            nodes.insert(0, GraphNode(
                commit=None,
                lane=uncommitted_lane,
                color_index=UNCOMMITTED_COLOR_INDEX,
                is_uncommitted=True,
                uncommitted_count=uncommitted_count,
                cells=uncommitted_cells,
            ))

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
        i for i in range(head_node_idx)
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
        nodes[i].lane for i in range(head_node_idx)
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
    for stash_idx, target_lane in stash_assignments:
        stash = nodes[stash_idx]
        old_lane = stash.lane

        # Clear the stash's old COMMIT cell so lane 0 is free for WIP.
        old_cell_idx = old_lane * 2
        if old_cell_idx < len(stash.cells):
            stash.cells[old_cell_idx] = CellInfo.empty()

        # Update the stash's lane and add a PIPE at the new lane for
        # every row above it (so the stash's vertical line is drawn
        # through all those rows).
        stash.lane = target_lane
        new_cell_idx = target_lane * 2
        for i in range(stash_idx):
            cell = nodes[i].cells[new_cell_idx]
            if cell.cell_type == CellType.EMPTY:
                nodes[i].cells[new_cell_idx] = CellInfo.pipe(head_color)

        # Place a plain COMMIT at the stash's lane.  For non-adjacent
        # lanes, add PIPE cells at every intermediate lane so the gap
        # bridge maintains the vertical line — without adding any
        # horizontals at the stash row.  The fork connector at HEAD
        # below handles the visual join at the parent row.
        stash.cells[new_cell_idx] = CellInfo.commit(head_color)
        for between_lane in range(head_lane + 1, target_lane):
            between_cell_idx = between_lane * 2  # left half of intermediate lane
            if between_cell_idx >= len(stash.cells):
                continue
            existing = stash.cells[between_cell_idx]
            if existing.cell_type == CellType.EMPTY:
                stash.cells[between_cell_idx] = CellInfo.pipe(head_color)

    # --- rebuild HEAD's fork connector ----------------------------------
    # Collect every branch above HEAD that shares HEAD as its first
    # parent — both the stashes we just shifted and any pre-existing
    # branches the main loop already placed on offset lanes.
    merging_lanes: list[tuple[int, int]] = []
    for i in range(head_node_idx):
        n = nodes[i]
        if (n.commit is not None
                and n.commit.parents
                and n.commit.parents[0] == head_oid):
            merging_lanes.append((n.lane, head_color))
    merging_lanes.sort()

    active_lanes: list[str | None] = [None] * (new_max_lane + 1)
    fork_cells = _build_fork_connector_cells(
        head_lane, head_color, merging_lanes, active_lanes,
        {}, {}, new_max_lane,
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
) -> list[CellInfo]:
    cells = [CellInfo.empty() for _ in range((max_lane + 1) * 2)]

    # Vertical lines for active lanes
    for lane_idx, lane_oid in enumerate(active_lanes):
        if lane_oid is not None and lane_idx != commit_lane:
            cell_idx = lane_idx * 2
            if cell_idx < len(cells):
                color = lane_color_index.get(lane_idx) or oid_color_index.get(lane_oid, lane_idx)
                cells[cell_idx] = CellInfo.pipe(color)

    # Commit node
    commit_cell_idx = commit_lane * 2
    if commit_cell_idx < len(cells):
        cells[commit_cell_idx] = CellInfo.commit(commit_color)

    # Connections to parents
    for _parent_sha, parent_lane, was_existing, parent_color, already_shown in parent_lanes:
        if parent_lane == commit_lane:
            continue

        if parent_lane > commit_lane:
            # Connection to the right.
            if parent_lane == commit_lane + 1:
                # Adjacent parent — replace COMMIT with TEE_RIGHT so the
                # horizontal exactly reaches the parent lane centre.
                if commit_cell_idx < len(cells):
                    cells[commit_cell_idx] = CellInfo(
                        CellType.TEE_RIGHT,
                        color_index=parent_color,
                        pipe_color_index=commit_color,
                    )
            else:
                for col in range(commit_lane * 2 + 1, parent_lane * 2 - 1):
                    if col < len(cells):
                        existing = cells[col]
                        if existing.cell_type == CellType.PIPE:
                            cells[col] = CellInfo.horizontal_pipe(parent_color, existing.color_index)
                        elif existing.cell_type == CellType.EMPTY:
                            cells[col] = CellInfo.horizontal(parent_color)

            end_idx = parent_lane * 2
            if end_idx < len(cells):
                if was_existing and already_shown:
                    cells[end_idx] = CellInfo.merge_left(parent_color)
                elif was_existing:
                    cells[end_idx] = CellInfo.tee_left(parent_color)
                else:
                    cells[end_idx] = CellInfo.branch_left(parent_color)
        else:
            # Connection to the left.
            if parent_lane == commit_lane - 1:
                # Adjacent parent — replace COMMIT with TEE_LEFT.
                if commit_cell_idx < len(cells):
                    cells[commit_cell_idx] = CellInfo(
                        CellType.TEE_LEFT,
                        color_index=parent_color,
                        pipe_color_index=commit_color,
                    )
            else:
                for col in range(parent_lane * 2 + 1, commit_lane * 2 - 1):
                    if col < len(cells):
                        existing = cells[col]
                        if existing.cell_type == CellType.PIPE:
                            cells[col] = CellInfo.horizontal_pipe(parent_color, existing.color_index)
                        elif existing.cell_type == CellType.EMPTY:
                            cells[col] = CellInfo.horizontal(parent_color)

            start_idx = parent_lane * 2
            if start_idx < len(cells):
                if was_existing and already_shown:
                    cells[start_idx] = CellInfo.merge_right(parent_color)
                elif was_existing:
                    cells[start_idx] = CellInfo.tee_right(parent_color)
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
            cells[main_cell_idx] = CellInfo(CellType.TEE_RIGHT,
                                            color_index=first_merge_color,
                                            pipe_color_index=main_color)
        elif len(merging_lanes) >= 2:
            cells[main_cell_idx] = CellInfo(CellType.TEE_RIGHT,
                                            color_index=first_merge_color,
                                            pipe_color_index=main_color)
        else:
            cells[main_cell_idx] = CellInfo.pipe(main_color)

    # Vertical lines for active lanes (except main and merging)
    for lane_idx, lane_oid in enumerate(active_lanes):
        if lane_oid is not None and lane_idx != main_lane and lane_idx not in merging_lane_nums:
            cell_idx = lane_idx * 2
            if cell_idx < len(cells):
                color = lane_color_index.get(lane_idx) or oid_color_index.get(lane_oid, lane_idx)
                cells[cell_idx] = CellInfo.pipe(color)

    prev_lane = main_lane
    for idx, (merge_lane, merge_color) in enumerate(merging_lanes):
        is_rightmost = (idx == len(merging_lanes) - 1)
        is_adjacent = (merge_lane == prev_lane + 1)

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
                cells[end_idx] = CellInfo(CellType.TEE_UP,
                                          color_index=next_merge_color,
                                          pipe_color_index=merge_color)
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
    branches: list[BranchInfo],
    tags: list,
    head_target_sha: str | None,
    head_shorthand: str | None,
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
    """Return ``True`` if *s* looks like a full hex SHA-1 (40 hex chars)."""
    return len(s) == 40 and all(c in "0123456789abcdef" for c in s)


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
