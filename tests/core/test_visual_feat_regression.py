"""Regression tests for ``BUG_VISUAL_FEAT_PIPE_COLOR``.

The local ``git-py`` repository carries a 3-commit side branch
(``visual-feat``) that gets merged back into main and is then crossed by
a horizontal connector from another side branch
(``bugfix-color-branch``).  The original bug had two layers:

* **Wire format (renderer)** — ``CellInfo.to_dict`` dropped the ``"p"``
  key whenever ``pipe_color_index == 0`` (GREEN), and the renderer's
  ``if p: ... else: color`` fallback then re-painted the vertical pipe
  in the crossing branch's colour.
* **Core lookup** — several ``build_graph`` sites used
  ``lane_color_index.get(key) or oid_color_index.get(key, default)``
  which treats ``0`` as falsy.  After the visual-feat chain ended the
  lane was re-used for the mainline, the lookup fell through to
  ``oid_color_index['mainline-commit']`` and the pipe below the visual-
  feat tip was over-painted with the mainline colour.

These tests guard against both regressions.  They live in ``tests/core``
because the symptom is visible in ``build_graph``'s cell layout; the
renderer integration is exercised separately by ``tests/ui``.
"""
from __future__ import annotations

import pytest
from src.core.graph_v2 import (
    BRANCH_PALETTE,
    CellType,
    build_graph,
)
from src.core.repository import RepositoryManager

# Indices / SHAs are stable as long as the local repository is checked
# out on the visual-feat-containing history.  The fixture skips the
# whole module if the test environment does not have that branch, so
# the suite still works in unrelated worktrees.
EXPECTED_VISUAL_FEAT_TIP_SHORT = "95136c3"
EXPECTED_VISUAL_FEAT_COLOR_IDX = 0  # crc32(b"visual-feat") % 40 == 0  →  GREEN
EXPECTED_VISUAL_FEAT_COLOR_HEX = BRANCH_PALETTE[EXPECTED_VISUAL_FEAT_COLOR_IDX]

# The merge commit that brings visual-feat back into main-content.
EXPECTED_MERGE_INTO_MAIN_SHORT = "037d6bf"

# The merge commit that introduces a crossing branch (bugfix-color-branch)
# whose horizontal connector passes *through* the visual-feat lane.
EXPECTED_MERGE_BUGFIX_SHA = "48f34b906731"  # full SHA for stable lookup

# All three visual-feat commits — top to bottom in the chain.
EXPECTED_VISUAL_FEAT_COMMITS = ("95136c3", "1ac38a5", "369fb70")

# Full SHA prefixes for stable lookup (resilient to row re-shuffles).
EXPECTED_VISUAL_FEAT_TIP_SHA_PREFIX = "95136c3e858586ae5817ca78f6d5e639fe6c0b51"
EXPECTED_VISUAL_FEAT_SHA_PREFIXES = {
    "95136c3": "95136c3e858586ae5817ca78f6d5e639fe6c0b51",
    "1ac38a5": "1ac38a588a39f7edee84ee39a7a87e9587e57ac9",
    "369fb70": "369fb70b4ca0a14c25a37a87b65e3f49c9b6ec46",
}


def _real_layout() -> tuple:
    """Return ``(layout, branches, visual_feat_tip_sha, merge_sha)`` or
    skip the calling test when the visual-feat branch is not present
    in the local checkout.
    """
    rm = RepositoryManager()
    rm.open(".")
    history = rm.get_all_history(max_count=10_000)
    branches = rm.branches

    visual_feat_tip_sha: str | None = None
    merge_sha: str | None = None
    for b in branches:
        if b.name == "visual-feat" and b.target_sha:
            visual_feat_tip_sha = b.target_sha
        if b.name == "main-content" and b.target_sha:
            # The tip commit is on main-content; the merge "visual-feat
            # into main-content" is one row above.  We don't need a SHA
            # for the main tip, but we do need the merge SHA below.
            pass

    # The merge commit message contains the literal string "Merge
    # branch 'visual-feat'" — search the most-recent history for it.
    for c in history:
        if "Merge branch 'visual-feat'" in c.message:
            merge_sha = c.sha
            break

    if visual_feat_tip_sha is None or merge_sha is None:
        pytest.skip(
            "local repository does not have a visual-feat branch — "
            "BUG_VISUAL_FEAT_PIPE_COLOR regression suite skipped"
        )

    layout = build_graph(history, branches)
    return layout, branches, visual_feat_tip_sha, merge_sha


def _row_short_sha(row) -> str | None:
    if row.commit is None:
        return None
    return row.commit.short_sha


def _find_visual_feat_span(layout) -> tuple[int, int, int] | None:
    """Return ``(top_row, bottom_row, lane)`` covering the visual-feat chain.

    ``top_row`` is the row of the visual-feat tip commit; ``bottom_row``
    is the last visual-feat ancestor in the chain (the one whose
    parent is on mainline).  ``lane`` is the visual-feat lane index —
    the chain lives on a single lane from tip down to its bottom
    ancestor.

    Uses the full SHA prefix of the visual-feat tip rather than the
    short SHA so the test stays stable as the local history grows.
    """
    tip_idx: int | None = None
    bottom_idx: int | None = None
    lane: int | None = None
    for i, n in enumerate(layout.nodes):
        if n.commit is None:
            continue
        if n.commit.sha.startswith(EXPECTED_VISUAL_FEAT_TIP_SHA_PREFIX):
            tip_idx = i
            lane = n.lane
            bottom_idx = tip_idx
            for j in range(tip_idx, len(layout.nodes) - 1):
                cur = layout.nodes[j]
                nxt = layout.nodes[j + 1]
                if nxt.commit is None or nxt.lane != cur.lane:
                    break
                bottom_idx = j + 1
            break
    if tip_idx is None or bottom_idx is None or lane is None:
        return None
    return tip_idx, bottom_idx, lane


def _find_visual_feat_lane_pipe_after(layout, lane: int, after_row: int) -> int | None:
    """Return the first row index >= ``after_row`` whose cell on ``lane``
    is a PIPE or HORIZONTAL_PIPE.  Used to locate the cell that the
    original bug painted with the wrong colour.
    """
    col = lane * 2
    for i in range(after_row, len(layout.nodes)):
        n = layout.nodes[i]
        if n.commit is None:
            continue
        if len(n.cells) <= col:
            continue
        cell = n.cells[col]
        if cell.cell_type in (CellType.PIPE, CellType.HORIZONTAL_PIPE):
            return i
    return None


# ---------------------------------------------------------------------------
# Per-commit colour invariants
# ---------------------------------------------------------------------------


# Full SHA prefixes for stable lookup (resilient to row re-shuffles).
# Defined here so the module-level constants are visible to the
# parametrize decorator below.
EXPECTED_VISUAL_FEAT_TIP_SHA_PREFIX = "95136c3e858586ae5817ca78f6d5e639fe6c0b51"
EXPECTED_VISUAL_FEAT_SHA_PREFIXES = {
    "95136c3": "95136c3e858586ae5817ca78f6d5e639fe6c0b51",
    "1ac38a5": "1ac38a58c8260e96379c5dcfb53e61e2c84cf727",
    "369fb70": "369fb70b4ca0001d40ec09d6472f4958079a687a",
}


def test_visual_feat_tip_is_green() -> None:
    """The visual-feat tip commit must report ``color_index == 0`` (GREEN)."""
    layout, _branches, tip_sha, _merge = _real_layout()
    for n in layout.nodes:
        if n.commit is not None and n.commit.sha == tip_sha:
            assert n.color_index == EXPECTED_VISUAL_FEAT_COLOR_IDX, (
                f"visual-feat tip should be GREEN (idx 0), got "
                f"{n.color_index} ({BRANCH_PALETTE[n.color_index]})"
            )
            assert (
                BRANCH_PALETTE[n.color_index] == EXPECTED_VISUAL_FEAT_COLOR_HEX
            )
            return
    pytest.fail("visual-feat tip commit not found in layout")


@pytest.mark.parametrize(
    "short_sha,full_sha_prefix",
    list(EXPECTED_VISUAL_FEAT_SHA_PREFIXES.items()),
)
def test_visual_feat_commit_is_green(short_sha: str, full_sha_prefix: str) -> None:
    """Every commit in the visual-feat chain carries the branch's colour."""
    layout, _branches, _tip, _merge = _real_layout()
    for n in layout.nodes:
        if n.commit is not None and n.commit.sha.startswith(full_sha_prefix):
            assert n.color_index == EXPECTED_VISUAL_FEAT_COLOR_IDX, (
                f"visual-feat commit {short_sha} ({full_sha_prefix}) "
                f"expected GREEN (idx 0), got {n.color_index} "
                f"({BRANCH_PALETTE[n.color_index]})"
            )
            return
    pytest.fail(
        f"visual-feat commit {short_sha} ({full_sha_prefix}) not found in layout"
    )


# ---------------------------------------------------------------------------
# Per-cell colour invariants on lane 1 (the visual-feat lane)
# ---------------------------------------------------------------------------


def test_visual_feat_horizontal_pipe_at_intersection_uses_green_pipe() -> None:
    """Row 2 (merge bugfix-color-branch) col 2 = HORIZONTAL_PIPE.

    The horizontal half is GOLD (the crossing branch's colour).  The
    vertical half must be GREEN (the visual-feat lane colour) — never
    GOLD or any other value.  Before the wire-format fix the renderer
    fell back to the horizontal colour.
    """
    layout, _branches, _tip, _merge = _real_layout()
    span = _find_visual_feat_span(layout)
    assert span is not None
    _top_idx, _bottom, lane = span

    target_idx: int | None = None
    for i, n in enumerate(layout.nodes):
        if n.commit is not None and n.commit.sha.startswith(EXPECTED_MERGE_BUGFIX_SHA):
            target_idx = i
            break
    assert target_idx is not None, (
        f"merge bugfix-color-branch commit {EXPECTED_MERGE_BUGFIX_SHA} not "
        "found in the layout — test fixture may be stale"
    )

    n = layout.nodes[target_idx]
    assert n.cells and len(n.cells) > lane * 2, (
        f"merge commit at row {target_idx} has no cell on lane {lane}"
    )
    cell = n.cells[lane * 2]
    assert cell.cell_type == CellType.HORIZONTAL_PIPE, (
        f"merge bugfix commit at row {target_idx} col={lane * 2} "
        f"expected HORIZONTAL_PIPE, got {cell.cell_type.name}"
    )
    d = cell.to_dict()
    assert d["c"] != EXPECTED_VISUAL_FEAT_COLOR_IDX, (
        "horizontal half of HORIZONTAL_PIPE on lane 1 should NOT be "
        "visual-feat colour — it represents the crossing branch"
    )
    assert "p" in d, (
        "HORIZONTAL_PIPE must serialise the pipe colour so the renderer "
        "can distinguish 'pipe=0' from 'pipe missing'"
    )
    assert d["p"] == EXPECTED_VISUAL_FEAT_COLOR_IDX, (
        f"HORIZONTAL_PIPE at sha={n.commit.short_sha} col={lane * 2} "
        f"should have pipe colour idx=0 (GREEN "
        f"{EXPECTED_VISUAL_FEAT_COLOR_HEX}), got idx={d['p']} "
        f"({BRANCH_PALETTE[d['p']]})"
    )


def test_visual_feat_lane_pipe_stays_green_below_chain_end() -> None:
    """After the visual-feat chain ends, lane 1's vertical pipe stays GREEN.

    The first row *after* the bottom of the visual-feat chain sits on a
    different lane (typically a sibling fork), but ``lanes[1]`` still
    tracks the mainline commit because ``fork_point`` handling
    intentionally does NOT overwrite ``lane_color_index[1]`` with the
    mainline colour — that would clobber the side branch tip colour.
    The first PIPE that the algorithm places on lane 1 after the
    visual-feat end must therefore stay GREEN.
    """
    layout, _branches, _tip, _merge = _real_layout()
    span = _find_visual_feat_span(layout)
    assert span is not None
    tip_idx, bottom_idx, lane = span

    # Walk past the bottom of the visual-feat chain looking for a
    # *pipe* cell (not a COMMIT) on the same lane.  Commits that land
    # on lane 1 *after* visual-feat belong to other branches and
    # legitimately have their own colour.
    next_pipe_idx = _find_visual_feat_lane_pipe_after(layout, lane, bottom_idx + 1)
    assert next_pipe_idx is not None, (
        f"no PIPE found on lane {lane} (col {lane * 2}) after the "
        f"visual-feat chain end at row {bottom_idx}"
    )

    cell = layout.nodes[next_pipe_idx].cells[lane * 2]
    assert cell.color_index == EXPECTED_VISUAL_FEAT_COLOR_IDX, (
        f"pipe on lane {lane} col={lane * 2} right after visual-feat was "
        f"over-painted: row {next_pipe_idx} cell={cell.cell_type.name} "
        f"colour idx={cell.color_index} ({BRANCH_PALETTE[cell.color_index]}).  "
        "The build_graph lookup fell through to ``oid_color_index`` for "
        "the re-used lane."
    )


def test_visual_feat_lane_wire_format_throughout_chain() -> None:
    """Walk the visual-feat chain and assert every cell on lane 1 carries GREEN.

    For every row from the tip down through the last visual-feat
    ancestor the cell at ``col = lane * 2`` is either a branch curve /
    commit dot / pipe whose vertical pipe colour must be GREEN.  Any
    other colour means a refactor changed how colour propagates through
    the chain.

    The merge-into-main row itself lives on mainline (lane 0) and draws
    the BRANCH_LEFT curve at col 2 that starts the chain — that cell is
    also GREEN (visual-feat colour) by construction.
    """
    layout, _branches, _tip, _merge = _real_layout()
    span = _find_visual_feat_span(layout)
    assert span is not None
    tip_idx, bottom_idx, lane = span
    col = lane * 2

    problems: list[str] = []
    # Start one row above the tip so we also cover the BRANCH_LEFT
    # curve that the merge-into-main row draws.
    start_idx = max(0, tip_idx - 1)
    for i in range(start_idx, bottom_idx + 1):
        n = layout.nodes[i]
        if n.commit is None:
            continue
        if len(n.cells) <= col:
            problems.append(f"row {i}: no cell at col={col}")
            continue
        cell = n.cells[col]
        if cell.cell_type == CellType.EMPTY:
            problems.append(f"row {i} {n.commit.short_sha}: EMPTY at col={col}")
            continue
        # The horizontal half of HORIZONTAL_PIPE may legitimately be a
        # different colour; we only care about the vertical pipe half.
        if cell.cell_type == CellType.HORIZONTAL_PIPE:
            d = cell.to_dict()
            if "p" not in d:
                problems.append(
                    f"row {i} {n.commit.short_sha} HORIZONTAL_PIPE missing 'p'"
                )
                continue
            if d["p"] != EXPECTED_VISUAL_FEAT_COLOR_IDX:
                problems.append(
                    f"row {i} {n.commit.short_sha} HORIZONTAL_PIPE pipe colour "
                    f"is {d['p']} ({BRANCH_PALETTE[d['p']]}), expected "
                    f"{EXPECTED_VISUAL_FEAT_COLOR_IDX}"
                )
            continue
        # All other cell types on this lane use ``color_index`` as the
        # vertical pipe colour.
        if cell.color_index != EXPECTED_VISUAL_FEAT_COLOR_IDX:
            problems.append(
                f"row {i} {n.commit.short_sha} {cell.cell_type.name} "
                f"on lane {lane} has colour idx={cell.color_index} "
                f"({BRANCH_PALETTE[cell.color_index]}), expected "
                f"{EXPECTED_VISUAL_FEAT_COLOR_IDX} ({EXPECTED_VISUAL_FEAT_COLOR_HEX})"
            )

    assert not problems, (
        "visual-feat lane cells failed invariants:\n" + "\n".join(problems)
    )


# ---------------------------------------------------------------------------
# Wire-format invariants (independent of layout specifics)
# ---------------------------------------------------------------------------


def test_cellinfo_to_dict_carries_pipe_for_zero_colours() -> None:
    """``CellInfo(..., pipe_color_index=0).to_dict()`` carries ``"p": 0``."""
    from src.core.graph_v2 import CellInfo  # local import keeps top tidy

    for ctype in (
        CellType.HORIZONTAL_PIPE,
        CellType.TEE_RIGHT,
        CellType.TEE_LEFT,
        CellType.TEE_UP,
        CellType.CROSS,
    ):
        d = CellInfo(ctype, color_index=15, pipe_color_index=0).to_dict()
        assert "p" in d, (
            f"{ctype.name} must serialise the pipe colour even when it is 0"
        )
        assert d["p"] == 0


def test_build_graph_never_drops_pipe_color_to_fallback() -> None:
    """Random sampling of ``build_graph`` rows must produce cells whose
    wire-format ``"p"`` equals the in-memory ``pipe_color_index`` for
    pipe-aware cell types.  Guards against any future refactor that
    re-introduces the ``if p: ... else: ...`` pattern.
    """
    layout, _branches, _tip, _merge = _real_layout()

    mismatches: list[str] = []
    pipe_aware = {
        CellType.HORIZONTAL_PIPE,
        CellType.TEE_RIGHT,
        CellType.TEE_LEFT,
        CellType.TEE_UP,
        CellType.CROSS,
    }

    for i, n in enumerate(layout.nodes[:50]):
        if n.commit is None:
            continue
        for col, cell in enumerate(n.cells):
            if cell.cell_type not in pipe_aware:
                continue
            d = cell.to_dict()
            if d.get("p") != cell.pipe_color_index:
                mismatches.append(
                    f"row {i} {n.commit.short_sha} col={col} "
                    f"{cell.cell_type.name} pipe_color_index="
                    f"{cell.pipe_color_index} but wire dict has p={d.get('p')!r}"
                )

    assert not mismatches, "\n".join(mismatches)
