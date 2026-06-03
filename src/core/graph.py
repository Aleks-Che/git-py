"""DAG model and lane-based layout for the commit graph.

This module is **pure Core**: it talks to :class:`src.core.models`
dataclasses and nothing else, so it can be unit-tested without a Qt
event loop and reused by any future UI backend.

The public API is:

* :class:`GraphNode` — a single commit enriched with display metadata
  (lane index, color, refs, row).
* :func:`compute_layout` — turn ``get_history()`` + ``branches`` +
  ``tags`` + head info into a list of :class:`GraphNode` in display
  order.
* :func:`nodes_to_rows` — serialise a layout to ``list[dict]`` so it
  can be shipped over a Qt signal without leaking the dataclass type
  to the UI layer.

The lane algorithm is a two-phase walk inspired by GitKraken's
display engine, but kept deliberately simple:

1. **Phase 1 (priority walk).** Order branches by priority — HEAD's
   branch first, then other local branches, then remote-tracking —
   and for each branch, walk from its tip toward the root, claiming
   each commit for that branch's lane. The walk stops when it hits
   a commit already claimed by a higher-priority branch (a shared
   ancestor). The first parent only is followed; the merge's other
   parents are left for whichever branch later claims them.
2. **Phase 2 (orphan walk).** Any commit not reached by a branch
   tip is laid out with a simple time-ordered fallback: each
   commit tries to continue its first parent's lane; if that lane
   is already occupied by a more recent commit, a new lane is
   opened. This handles repositories with no local branches and
   ad-hoc tag-only commits.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.core.models import BranchInfo, CommitInfo, TagInfo

# A 12-colour palette inspired by GitKraken's dark theme. Cycled in
# order when a new branch is encountered. Stage 9 will move this to
# user config; for now it lives here as a constant per the Stage 2 plan.
BRANCH_PALETTE: tuple[str, ...] = (
    "#2D9A3E",  # green
    "#4A7CE6",  # blue
    "#D04A3E",  # red
    "#D99F2E",  # amber
    "#8E5CE3",  # violet
    "#35A8B8",  # teal
    "#D9409A",  # pink
    "#60B48A",  # mint
    "#DC7020",  # orange
    "#7A838C",  # grey
    "#B88BE6",  # lavender
    "#40BC4E",  # lime
)


@dataclass(frozen=True)
class BranchRef:
    """A branch that points at a commit, with display metadata.

    ``is_head`` is ``True`` for the branch ``HEAD`` is currently
    checked out on (exactly one per repository). ``is_remote`` is
    ``True`` for remote-tracking refs (``origin/main`` and friends);
    local branches have it ``False``. The widget uses these flags
    to choose which decoration to draw next to the branch name.
    """

    name: str
    is_head: bool
    is_remote: bool

    def to_dict(self) -> dict:
        """Serialise to a plain dict (safe for Qt signal payload)."""
        return {
            "name": self.name,
            "is_head": self.is_head,
            "is_remote": self.is_remote,
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
    color: str = BRANCH_PALETTE[0]
    row: int = 0

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
            "color": self.color,
            "row": self.row,
        }


@dataclass
class _TipRef:
    """A target SHA the lane walk should claim. Wraps a real branch
    or a synthesised detached-HEAD tip."""

    name: str
    target_sha: str


def compute_layout(
    history: list[CommitInfo],
    branches: list[BranchInfo],
    tags: list[TagInfo],
    head_target_sha: str | None,
    head_shorthand: str | None,
) -> list[GraphNode]:
    """Compute a lane-based layout for ``history``.

    ``head_target_sha`` is the SHA ``HEAD`` resolves to (``None`` if
    unborn). ``head_shorthand`` is the symbolic name ``HEAD`` carries
    — a branch name like ``"main"`` when on a branch, or
    ``"(detached)"`` when HEAD is detached. It's currently unused
    here but kept in the signature for forward compatibility (Stage
    3+ will read it to decide which WIP node to draw).

    The returned list is in the same order as ``history`` (newest
    first). Each :class:`GraphNode` has ``row`` matching its position
    in the list.
    """
    if not history:
        return []

    refs_by_sha = _build_refs_map(branches, tags, head_target_sha, head_shorthand)
    branch_refs_by_sha = _build_branch_refs_map(branches)
    branch_tips = {b.target_sha for b in branches if b.target_sha}
    lanes = _assign_lanes(history, branches, head_target_sha)
    colors = _assign_colors(history, branch_tips)

    nodes: list[GraphNode] = []
    for row, commit in enumerate(history):
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
                lane=lanes[commit.sha],
                color=colors[commit.sha],
                row=row,
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
) -> dict[str, list[BranchRef]]:
    """Map commit SHA -> ordered list of :class:`BranchRef` for the widget.

    The order matches the input list, which (per :class:`RepositoryManager`)
    is local branches first, then remote-tracking refs. ``HEAD``'s branch
    carries ``is_head=True``; remote-tracking refs carry
    ``is_remote=True``. Within each group the original input order
    is preserved so the column renders predictably.
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
) -> dict[str, int]:
    """Return ``sha -> lane`` for every commit in ``history``.

    See the module docstring for the two-phase algorithm. Limitations
    accepted for Stage 2:

    * Only the **first** parent is followed during the priority walk.
      A merge's other parents get their lane from whichever branch
      later claims them (or from the orphan walk).
    * If a branch's tip is reachable only through a second parent
      of a merge (i.e. that branch has never been on lane 0 itself),
      the priority walk will still follow the first parent down and
      the branch gets a higher lane number — which is the right
      visual outcome.
    """
    if not history:
        return {}

    lane_of: dict[str, int] = {}
    commits_by_sha = {c.sha: c for c in history}
    next_lane = 0

    # Phase 1: priority walk, first-parent only.
    for tip in _priority_tips(branches, head_target_sha):
        if tip.target_sha in lane_of:
            continue
        my_lane = next_lane
        next_lane += 1
        stack = [tip.target_sha]
        while stack:
            sha = stack.pop()
            if sha in lane_of:
                continue
            commit = commits_by_sha.get(sha)
            if commit is None:
                continue
            lane_of[sha] = my_lane
            if commit.parents:
                stack.append(commit.parents[0])

    # Phase 2: orphan walk (commits no branch reached). Process
    # oldest-first and use the "first available lane" rule.
    lane_last_commit: list[str | None] = []
    for commit in reversed(history):
        if commit.sha in lane_of:
            lane = lane_of[commit.sha]
            while len(lane_last_commit) <= lane:
                lane_last_commit.append(None)
            lane_last_commit[lane] = commit.sha
            continue
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

    return lane_of


def _first_free_lane(lane_last_commit: list[str | None]) -> int:
    """Return the index of the first ``None`` slot, or extend the list."""
    for i, sha in enumerate(lane_last_commit):
        if sha is None:
            return i
    return len(lane_last_commit)


def _assign_colors(
    history: list[CommitInfo],
    branch_tips: set[str],
) -> dict[str, str]:
    """Return ``sha -> hex color`` for every commit in ``history``.

    Branch tips pick up a fresh palette colour in the order they
    appear in ``history`` (newest first). The colour is then walked
    **down** the first-parent chain from each tip so that every
    ancestor on the same lineage inherits the branch colour. Earlier
    tips (lower palette index) take precedence — once a commit has a
    colour it is never overwritten. CommitWith no branch tip and
    whose first parent is already coloured inherit that colour in a
    final oldest-first pass.
    """
    color_of: dict[str, str] = {}
    commits_by_sha = {c.sha: c for c in history}
    palette_idx = 0

    # Pass 1: assign colours to branch tips.
    tips_in_order: list[str] = []
    for commit in history:
        if commit.sha in branch_tips:
            color_of[commit.sha] = BRANCH_PALETTE[palette_idx % len(BRANCH_PALETTE)]
            tips_in_order.append(commit.sha)
            palette_idx += 1

    # Pass 2: walk down each tip's first-parent chain. Lower-index
    # tips (earlier in the palette) have priority.
    for tip_sha in tips_in_order:
        tip_color = color_of[tip_sha]
        sha: str | None = tip_sha
        while sha is not None:
            commit = commits_by_sha.get(sha)
            if commit is None or not commit.parents:
                break
            parent_sha = commit.parents[0]
            if parent_sha not in color_of:
                color_of[parent_sha] = tip_color
            sha = parent_sha

    # Pass 3: back-fill any remaining uncoloured commits by
    # inheriting from their first parent (oldest-first so the parent
    # is already resolved). Root orphans get ``BRANCH_PALETTE[0]``.
    for commit in reversed(history):
        if commit.sha in color_of:
            continue
        if commit.parents and commit.parents[0] in color_of:
            color_of[commit.sha] = color_of[commit.parents[0]]
        else:
            color_of[commit.sha] = BRANCH_PALETTE[0]

    return color_of


__all__ = [
    "BRANCH_PALETTE",
    "BranchRef",
    "GraphNode",
    "compute_layout",
    "nodes_to_rows",
]
