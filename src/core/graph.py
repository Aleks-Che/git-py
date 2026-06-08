"""DAG model and swimlane-based layout for the commit graph.

This module is **pure Core**: it talks to :class:`src.core.models`
dataclasses and nothing else, so it can be unit-tested without a Qt
event loop and reused by any future UI backend.

The public API is:

* :class:`GraphNode` — a single commit enriched with display metadata
  (lane index, color, refs, row, input_lanes, output_lanes).
* :func:`compute_layout` — turn ``get_history()`` + ``branches`` +
  ``tags`` + head info into a list of :class:`GraphNode` in display
  order.
* :func:`nodes_to_rows` — serialise a layout to ``list[dict]`` so it
  can be shipped over a Qt signal without leaking the dataclass type
  to the UI layer.

The layout algorithm is a single-phase top-down swimlane model
inspired by VS Code's scmHistory.ts:

1. **Seed.** Output lanes are seeded from branch-tips in priority
   order (HEAD branch → other locals → remote), giving each branch
   its own lane with its own colour from the start.
2. **Top-down walk.** For each commit (newest first), input lanes
   are a copy of the previous row's output lanes. The commit's
   position in input lanes is found; its first-parent inherits its
   column; additional parents (merges) create new lanes on the
   right. Each lane retains its branch colour throughout.
3. **Deduplication.** The same SHA cannot appear in multiple
   output lanes — the first occurrence wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.core.models import BranchInfo, CommitInfo, TagInfo

# A 12-colour palette inspired by GitKraken's dark theme. Cycled in
# order when a new branch is encountered. Stage 9 will move this to
# user config; for now it lives here as a constant per the Stage 2 plan.
BRANCH_PALETTE: tuple[str, ...] = (
    "#1A5924",  # green
    "#2B4786",  # blue
    "#782B24",  # red
    "#7D5C1A",  # amber
    "#523583",  # violet
    "#1E626A",  # teal
    "#7D2559",  # pink
    "#38684F",  # mint
    "#7F4112",  # orange
    "#464B51",  # grey
    "#6A5086",  # lavender
    "#256D2D",  # lime
)


@dataclass(frozen=True)
class _SwimlaneEntry:
    """One swimlane column: SHA of the commit + colour of the lane."""

    sha: str
    color: str


@dataclass(frozen=True)
class BranchRef:
    """A branch that points at a commit, with display metadata.

    ``is_head`` is ``True`` for the branch ``HEAD`` is currently
    checked out on (exactly one per repository). ``is_remote`` is
    ``True`` for remote-tracking refs (``origin/main`` and friends);
    local branches have it ``False``. The widget uses these flags
    to choose which decoration to draw next to the branch name.

    ``lane`` and ``color`` carry branch-level display properties so
    that remote-tracking refs can be rendered with their own lane
    line and colour independently of the commit they point at.
    """

    name: str
    is_head: bool
    is_remote: bool
    lane: int = 0
    color: str = ""

    def to_dict(self) -> dict:
        """Serialise to a plain dict (safe for Qt signal payload)."""
        return {
            "name": self.name,
            "is_head": self.is_head,
            "is_remote": self.is_remote,
            "lane": self.lane,
            "color": self.color,
        }


@dataclass
class GraphNode:
    """A commit enriched with display metadata for the graph view."""

    sha: str
    short_sha: str
    subject: str
    author_name: str
    author_email: str
    author_time: int
    parents: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    branch_refs: list[BranchRef] = field(default_factory=list)
    lane: int = 0
    display_column: int = 0
    color: str = BRANCH_PALETTE[0]
    row: int = 0
    kind: str = "commit"  # "commit" | "wip" | "stash"
    input_lanes: list[dict] = field(default_factory=list)
    output_lanes: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialise to a plain dict (safe for Qt signal payload)."""
        return {
            "sha": self.sha,
            "short_sha": self.short_sha,
            "subject": self.subject,
            "author_name": self.author_name,
            "author_email": self.author_email,
            "author_time": self.author_time,
            "parents": list(self.parents),
            "refs": list(self.refs),
            "branch_refs": [b.to_dict() for b in self.branch_refs],
            "lane": self.lane,
            "display_column": self.display_column,
            "color": self.color,
            "row": self.row,
            "kind": self.kind,
            "input_lanes": list(self.input_lanes),
            "output_lanes": list(self.output_lanes),
        }


@dataclass
class _TipRef:
    """A target SHA the lane walk should claim. Wraps a real branch
    or a synthesised detached-HEAD tip."""

    name: str
    target_sha: str


def _build_swimlanes(
    history: list[CommitInfo],
    branches: list[BranchInfo],
    head_target_sha: str | None,
    head_shorthand: str | None,
    branch_colors: dict[str, str],
) -> list[dict]:
    """Build swimlane layout rows for each commit in ``history`` (newest first).

    Returns a list of dicts, one per row, with keys:
        sha, lane, color, input_lanes, output_lanes
    """
    if not history:
        return []

    shas_in_history: set[str] = {c.sha for c in history}

    # Build sha → branch colour lookup (for branch-tips actually in history).
    sha_to_branch_color: dict[str, str] = {}
    for b in branches:
        if b.target_sha and b.target_sha in shas_in_history:
            if b.target_sha not in sha_to_branch_color:
                sha_to_branch_color[b.target_sha] = branch_colors.get(
                    b.name, BRANCH_PALETTE[0],
                )

    # Minimal seed: only HEAD target SHA to guarantee lane 0 for HEAD.
    output_lanes: list[_SwimlaneEntry] = []
    if head_target_sha and head_target_sha in shas_in_history:
        color = sha_to_branch_color.get(head_target_sha, BRANCH_PALETTE[0])
        output_lanes.append(_SwimlaneEntry(sha=head_target_sha, color=color))
    rows: list[dict] = []

    for commit in history:
        sha = commit.sha

        # ---- input lanes: copy of previous row's output ----
        input_lanes = list(output_lanes)

        # ---- find commit position in input lanes ----
        idx = None
        for i, entry in enumerate(input_lanes):
            if entry.sha == sha:
                idx = i
                break

        if idx is None:
            color = sha_to_branch_color.get(sha, BRANCH_PALETTE[0])
            idx = len(input_lanes)
            input_lanes.append(_SwimlaneEntry(sha=sha, color=color))

        commit_color = sha_to_branch_color.get(sha, input_lanes[idx].color)

        # ---- collect all positions of this commit ----
        commit_positions: set[int] = {
            i for i, entry in enumerate(input_lanes) if entry.sha == sha
        }

        # ---- build output lanes ----
        output_lanes = []
        first_parent_placed = False
        primary_idx = min(commit_positions)

        for i, entry in enumerate(input_lanes):
            if i in commit_positions:
                if i == primary_idx and commit.parents and not first_parent_placed:
                    parent_color = sha_to_branch_color.get(
                        commit.parents[0], entry.color,
                    )
                    output_lanes.append(
                        _SwimlaneEntry(sha=commit.parents[0], color=parent_color),
                    )
                    first_parent_placed = True
            else:
                output_lanes.append(entry)

        # ---- additional parents (merge) create new lanes on the right ----
        start = 1 if first_parent_placed else 0
        for p in commit.parents[start:]:
            p_color = sha_to_branch_color.get(
                p, entry.color if entry else BRANCH_PALETTE[0],
            )
            output_lanes.append(_SwimlaneEntry(sha=p, color=p_color))

        # ---- deduplicate SHA in output lanes ----
        seen: set[str] = set()
        deduped: list[_SwimlaneEntry] = []
        for entry in output_lanes:
            if entry.sha not in seen:
                seen.add(entry.sha)
                deduped.append(entry)
        output_lanes = deduped

        rows.append({
            "sha": sha,
            "lane": idx,
            "color": commit_color,
            "input_lanes": [
                {"sha": e.sha, "color": e.color} for e in input_lanes
            ],
            "output_lanes": [
                {"sha": e.sha, "color": e.color} for e in output_lanes
            ],
        })

    return rows


def _compact_swimlane_columns(
    rows: list[dict],
    max_columns: int,
) -> dict[int, int]:
    """Map position indices in input_lanes to compact display columns.

    Two positions whose row ranges overlap need different columns.
    Non-overlapping positions may share a column. Uses greedy
    interval coloring.
    """
    if not rows:
        return {}

    # Build position -> (min_row, max_row).
    pos_rows: dict[int, tuple[int, int]] = {}
    for i, row in enumerate(rows):
        input_entries = row.get("input_lanes", [])
        for pos in range(len(input_entries)):
            if pos not in pos_rows:
                pos_rows[pos] = (i, i)
            else:
                cur_min, cur_max = pos_rows[pos]
                pos_rows[pos] = (min(cur_min, i), max(cur_max, i))

    if not pos_rows:
        return {}

    # Sort by first appearance.
    sorted_pos = sorted(pos_rows.items(), key=lambda x: x[1][0])

    column_of_pos: dict[int, int] = {}
    for pos, (pos_min, pos_max) in sorted_pos:
        used_cols: set[int] = set()
        for other_pos, other_col in column_of_pos.items():
            other_min, other_max = pos_rows[other_pos]
            if not (pos_max < other_min or pos_min > other_max):
                used_cols.add(other_col)
        col = 0
        while col in used_cols:
            col += 1
        column_of_pos[pos] = col % max_columns if max_columns > 0 else col

    return column_of_pos


def _compute_branch_lanes(
    branches: list[BranchInfo],
    swimlane_rows: list[dict],
) -> dict[str, int]:
    """Map branch_name → lane index from the swimlane layout.

    Uses the first row where the branch's target SHA appears to
    determine which lane the branch occupies.
    """
    result: dict[str, int] = {}
    if not swimlane_rows:
        return result
    for branch in branches:
        if not branch.target_sha:
            continue
        for row in swimlane_rows:
            if row["sha"] == branch.target_sha:
                result[branch.name] = row["lane"]
                break
        else:
            result[branch.name] = 0
    return result


def compute_layout(
    history: list[CommitInfo],
    branches: list[BranchInfo],
    tags: list[TagInfo],
    head_target_sha: str | None,
    head_shorthand: str | None,
    *,
    max_columns: int = 12,
) -> list[GraphNode]:
    """Compute a swimlane-based layout for ``history``.

    ``head_target_sha`` is the SHA ``HEAD`` resolves to (``None`` if
    unborn). ``head_shorthand`` is the symbolic name ``HEAD`` carries
    — a branch name like ``"main"`` when on a branch, or
    ``"(detached)"`` when HEAD is detached.

    The returned list is in the same order as ``history`` (newest
    first). Each :class:`GraphNode` has ``row`` matching its position
    in the list.
    """
    if not history:
        return []

    refs_by_sha = _build_refs_map(branches, tags, head_target_sha, head_shorthand)
    branch_colors = _assign_branch_colors(branches, head_target_sha)

    swimlane_rows = _build_swimlanes(
        history, branches, head_target_sha, head_shorthand, branch_colors,
    )

    branch_lanes_by_name = _compute_branch_lanes(branches, swimlane_rows)
    branch_refs_by_sha = _build_branch_refs_map(
        branches, branch_lanes_by_name, branch_colors,
    )

    column_of_pos = _compact_swimlane_columns(swimlane_rows, max_columns)

    nodes: list[GraphNode] = []
    for row, commit in enumerate(history):
        sw = swimlane_rows[row]
        logical_lane = sw["lane"]

        # Add display column to each input/output lane entry.
        in_lanes = [
            {"sha": e["sha"], "color": e["color"], "column": column_of_pos.get(i, i)}
            for i, e in enumerate(sw["input_lanes"])
        ]
        out_lanes = [
            {"sha": e["sha"], "color": e["color"], "column": column_of_pos.get(i, i)}
            for i, e in enumerate(sw["output_lanes"])
        ]

        nodes.append(
            GraphNode(
                sha=commit.sha,
                short_sha=commit.short_sha,
                subject=_subject(commit.message),
                author_name=commit.author_name,
                author_email=commit.author_email,
                author_time=commit.author_time,
                parents=list(commit.parents),
                refs=refs_by_sha.get(commit.sha, []),
                branch_refs=branch_refs_by_sha.get(commit.sha, []),
                lane=logical_lane,
                display_column=column_of_pos.get(logical_lane, logical_lane),
                color=sw["color"],
                row=row,
                kind=getattr(commit, "kind", "commit"),
                input_lanes=in_lanes,
                output_lanes=out_lanes,
            ),
        )

    return nodes


def nodes_to_rows(nodes: list[GraphNode]) -> list[dict]:
    """Serialise a layout to plain dicts for cross-thread Qt signals."""
    return [node.to_dict() for node in nodes]


# ----- internals ---------------------------------------------------------


def _subject(message: str) -> str:
    """Return the first non-empty line of ``message``, stripped.

    ``pygit2`` includes both subject and body in ``commit.message``;
    the graph label only needs the subject.
    """
    for line in message.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _build_refs_map(
    branches: list[BranchInfo],
    tags: list[TagInfo],
    head_target_sha: str | None,
    head_shorthand: str | None,
) -> dict[str, list[str]]:
    """Map commit SHA -> ordered list of ref chip labels.

    Branches are intentionally **not** included here — they live in
    :func:`_build_branch_refs_map` and are rendered in a separate
    left-hand column by the widget. This map only carries labels
    that have no extra metadata worth displaying: ``HEAD`` (when
    present) and tag names, in that order.
    """
    result: dict[str, list[str]] = {}
    if head_target_sha:
        result.setdefault(head_target_sha, []).append("HEAD")
    for tag in tags:
        if tag.target_sha:
            result.setdefault(tag.target_sha, []).append(tag.name)
    return result


def _build_branch_refs_map(
    branches: list[BranchInfo],
    branch_lanes: dict[str, int],
    branch_colors: dict[str, str],
) -> dict[str, list[BranchRef]]:
    """Map commit SHA -> ordered list of :class:`BranchRef` for the widget.

    The order matches the input list, which (per :class:`RepositoryManager`)
    is local branches first, then remote-tracking refs. ``HEAD``'s branch
    carries ``is_head=True``; remote-tracking refs carry
    ``is_remote=True``. Within each group the original input order
    is preserved so the column renders predictably.

    Each :class:`BranchRef` receives its own ``lane`` and ``color``
    from *branch_lanes* and *branch_colors* so the widget can render
    branch-specific lane lines and chip colours independently of the
    commit they point at.
    """
    result: dict[str, list[BranchRef]] = {}
    for branch in branches:
        if not branch.target_sha:
            continue
        result.setdefault(branch.target_sha, []).append(
            BranchRef(
                name=branch.name,
                is_head=branch.is_head,
                is_remote=branch.is_remote,
                lane=branch_lanes.get(branch.name, 0),
                color=branch_colors.get(branch.name, BRANCH_PALETTE[0]),
            ),
        )
    return result


def _priority_tips(
    branches: list[BranchInfo],
    head_target_sha: str | None,
) -> list[_TipRef]:
    """Return the tip references in priority order for lane assignment.

    Order: HEAD's branch, then other local branches, then
    remote-tracking. A detached HEAD (no branch points at it) is
    synthesised as a fake "HEAD" tip and put at the very top.
    """
    tips: list[_TipRef] = []
    branch_target_shas = {b.target_sha for b in branches if b.target_sha}
    head_branch: BranchInfo | None = None
    other_local: list[BranchInfo] = []
    remote: list[BranchInfo] = []
    for branch in branches:
        if not branch.target_sha:
            continue
        if branch.is_head:
            head_branch = branch
        elif branch.is_remote:
            remote.append(branch)
        else:
            other_local.append(branch)
    if head_branch is not None:
        tips.append(_TipRef(name=head_branch.name, target_sha=head_branch.target_sha))
    elif head_target_sha and head_target_sha not in branch_target_shas:
        tips.append(_TipRef(name="HEAD", target_sha=head_target_sha))
    for branch in other_local:
        tips.append(_TipRef(name=branch.name, target_sha=branch.target_sha))
    for branch in remote:
        tips.append(_TipRef(name=branch.name, target_sha=branch.target_sha))
    return tips


def _assign_lanes(
    history: list[CommitInfo],
    branches: list[BranchInfo],
    head_target_sha: str | None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return ``(sha -> primary_lane, branch_name -> branch_lane)``.

    Each branch tip is assigned a lane. When a branch's tip SHA is
    already claimed by a higher-priority branch, it shares that same
    lane rather than opening a new one. The branch still appears in
    ``branch_lane`` so the widget can find its lane position.
    """
    if not history:
        return {}, {}

    lane_of: dict[str, int] = {}
    branch_lane: dict[str, int] = {}
    commits_by_sha = {c.sha: c for c in history}
    next_lane = 0

    tips = _priority_tips(branches, head_target_sha)

    for tip in tips:
        sha = tip.target_sha

        if sha in lane_of:
            branch_lane[tip.name] = lane_of[sha]
            continue

        my_lane = next_lane
        next_lane += 1
        branch_lane[tip.name] = my_lane
        lane_of[sha] = my_lane
        stack = [sha]
        while stack:
            cur = stack.pop()
            if cur in lane_of:
                continue
            commit = commits_by_sha.get(cur)
            if commit is None:
                continue
            lane_of[cur] = my_lane
            if commit.parents:
                stack.append(commit.parents[0])

    lane_last_commit: list[str | None] = []
    for commit in reversed(history):
        if commit.sha in lane_of:
            lane = lane_of[commit.sha]
            while len(lane_last_commit) <= lane:
                lane_last_commit.append(None)
            lane_last_commit[lane] = commit.sha
            continue
        kind = getattr(commit, "kind", "commit")
        if kind == "stash":
            my_lane = _first_free_lane(lane_last_commit)
        else:
            parent = commit.parents[0] if commit.parents else None
            if parent is not None and parent in lane_of:
                parent_lane = lane_of[parent]
                if (
                    parent_lane < len(lane_last_commit)
                    and lane_last_commit[parent_lane] == parent
                ):
                    my_lane = parent_lane
                else:
                    my_lane = _first_free_lane(lane_last_commit)
            else:
                my_lane = _first_free_lane(lane_last_commit)
        while len(lane_last_commit) <= my_lane:
            lane_last_commit.append(None)
        lane_last_commit[my_lane] = commit.sha
        lane_of[commit.sha] = my_lane

    return lane_of, branch_lane


def _first_free_lane(lane_last_commit: list[str | None]) -> int:
    """Return the index of the first ``None`` slot, or extend the list."""
    for i, sha in enumerate(lane_last_commit):
        if sha is None:
            return i
    return len(lane_last_commit)


def _assign_branch_colors(
    branches: list[BranchInfo],
    head_target_sha: str | None,
) -> dict[str, str]:
    """Return ``branch_name -> hex color`` so each branch has a unique colour.

    Branches are ordered by priority (HEAD, locals, remotes — via
    :func:`_priority_tips`) and each is assigned the next palette
    colour. Every branch tip gets its own colour, even when two
    branches point to the same commit SHA.
    """
    tips = _priority_tips(branches, head_target_sha)
    result: dict[str, str] = {}
    for i, tip in enumerate(tips):
        result[tip.name] = BRANCH_PALETTE[i % len(BRANCH_PALETTE)]
    return result


__all__ = [
    "BRANCH_PALETTE",
    "BranchRef",
    "GraphNode",
    "compute_layout",
    "nodes_to_rows",
    "_build_swimlanes",
]
