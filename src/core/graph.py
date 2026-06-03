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
    "#3FB950",  # green
    "#5B8FF9",  # blue
    "#E8685A",  # red
    "#F5B947",  # amber
    "#A371F7",  # violet
    "#43BCCD",  # teal
    "#F25CB0",  # pink
    "#7BC8A4",  # mint
    "#F0883E",  # orange
    "#8B949E",  # grey
    "#D2A8FF",  # lavender
    "#56D364",  # lime
)


@dataclass
class GraphNode:
    """A commit enriched with display metadata for the graph view."""

    sha: str
    short_sha: str
    subject: str
    author_name: str
    author_time: int
    parents: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
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
            "author_time": self.author_time,
            "parents": list(self.parents),
            "refs": list(self.refs),
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
                author_time=commit.author_time,
                parents=list(commit.parents),
                refs=refs_by_sha.get(commit.sha, []),
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
    """Map commit SHA -> ordered list of ref labels.

    HEAD is added first (so it always renders at the top of the label
    stack). Branches follow in input order (typically local first,
    then remote-tracking). Tags come last. Within each group the
    original input order is preserved.
    """
    result: dict[str, list[str]] = {}
    if head_target_sha:
        result.setdefault(head_target_sha, []).append("HEAD")
    for branch in branches:
        if branch.target_sha:
            result.setdefault(branch.target_sha, []).append(branch.name)
    for tag in tags:
        if tag.target_sha:
            result.setdefault(tag.target_sha, []).append(tag.name)
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

    A commit that is a branch tip gets the next palette colour
    (cycled). Otherwise it inherits the colour of its first parent.
    A root commit with no parents uses ``BRANCH_PALETTE[0]``. Tags
    don't change the colour — they sit on whatever colour the
    underlying branch has.
    """
    color_of: dict[str, str] = {}
    palette_idx = 0
    for commit in history:
        if commit.sha in branch_tips:
            color_of[commit.sha] = BRANCH_PALETTE[palette_idx % len(BRANCH_PALETTE)]
            palette_idx += 1
        elif commit.parents:
            color_of[commit.sha] = color_of.get(
                commit.parents[0], BRANCH_PALETTE[0],
            )
        else:
            color_of[commit.sha] = BRANCH_PALETTE[0]
    return color_of


__all__ = [
    "BRANCH_PALETTE",
    "GraphNode",
    "compute_layout",
    "nodes_to_rows",
]
