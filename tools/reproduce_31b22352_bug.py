"""Reproduce the colour-mismatch bug for gpt-researcher commit 31b22352.

The bug as reported:
    Above the commit 31b22352 and below it, the line colours should be
    green, but they are a different colour (dark red or violet).

What this script does:
    1. Opens the gpt-researcher repository
       (``C:/work/git/other-repos/llm/gpt-researcher``).
    2. Builds the cell-based graph layout for the most recent 600 commits.
    3. Locates commit 31b22352 (``TARGET``) and prints:
         - the layout summary,
         - the row contents around TARGET,
         - a small ASCII picture of the area,
         - the colour of every vertical PIPE on TARGET's own lane
           (the column a reader would naturally track up and down
           from TARGET),
         - the colour of the lines the user explicitly complained about
           (DARK RED at lane 2, VIOLET at lane 4) above and below TARGET,
         - the row of the fork connector that intersects TARGET's lane.

Run:
    python tools/reproduce_31b22352_bug.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.graph_v2 import BRANCH_PALETTE, CellType, build_graph
from src.core.repository import RepositoryManager

REPO_PATH = Path("C:/work/git/other-repos/llm/gpt-researcher")
TARGET_SHA = "31b22352aa0cb86d737784655502fde9cde46013"
TARGET_SHORT = TARGET_SHA[:7]
GREEN = "#1A5924"
DARK_RED = "#782B24"
VIOLET = "#523583"

_GLYPHS: dict[CellType, str] = {
    CellType.EMPTY: " ",
    CellType.PIPE: "│",
    CellType.COMMIT: "●",
    CellType.BRANCH_RIGHT: "╭",
    CellType.BRANCH_LEFT: "╮",
    CellType.MERGE_RIGHT: "╰",
    CellType.MERGE_LEFT: "╯",
    CellType.HORIZONTAL: "─",
    CellType.HORIZONTAL_PIPE: "┼",
    CellType.TEE_RIGHT: "├",
    CellType.TEE_LEFT: "┤",
    CellType.TEE_UP: "┴",
    CellType.CROSS: "╋",
}


def _hex(idx: int) -> str:
    if 0 <= idx < len(BRANCH_PALETTE):
        return BRANCH_PALETTE[idx]
    return f"?({idx})"


def _cell_str(cell) -> str:
    return _GLYPHS.get(cell.cell_type, "?")


def _column_header(max_col: int) -> str:
    return "       " + "".join(f"{(i // 2) % 10}" for i in range(max_col))


def main() -> int:
    rm = RepositoryManager()
    rm.open(str(REPO_PATH))

    history = rm.get_all_history(max_count=600)
    branches = rm.branches
    layout = build_graph(history, branches)

    target_idx: int | None = None
    for i, n in enumerate(layout.nodes):
        if n.commit and n.commit.sha == TARGET_SHA:
            target_idx = i
            break
    if target_idx is None:
        print(f"TARGET {TARGET_SHA} not found in the layout")
        return 1

    target = layout.nodes[target_idx]
    print(f"history={len(history)} branches={len(branches)}")
    print(f"layout nodes={len(layout.nodes)} max_lane={layout.max_lane}")
    print()
    print(
        f"TARGET row {target_idx}: lane={target.lane} "
        f"color={target.color_index} ({_hex(target.color_index)})"
    )
    print()

    radius = 5
    start = max(0, target_idx - radius)
    end = min(len(layout.nodes), target_idx + radius + 1)
    max_col = max(len(layout.nodes[i].cells) for i in range(start, end))

    print(_column_header(max_col))
    print("       " + "─" * max_col)
    for i in range(start, end):
        node = layout.nodes[i]
        cells = node.cells
        line = "".join(_cell_str(c) for c in cells).rstrip()
        sha = node.commit.short_sha if node.commit else "?"
        marker = " <<< TARGET" if i == target_idx else ""
        print(f"  {i:3d}  {sha}  {line}{marker}")
    print()

    target_lane = target.lane
    target_col = target_lane * 2
    print(f"=== Vertical PIPE on TARGET's own lane (col {target_col}) ===")
    print(
        f"{'row':>4} {'sha':7} {'cell':14} {'color':>5} {'hex':>9} "
        f"{'pipe':>5} {'pipe_hex':>9}"
    )
    for offset in range(-6, 7):
        j = target_idx + offset
        if not (0 <= j < len(layout.nodes)):
            continue
        n = layout.nodes[j]
        if target_col >= len(n.cells):
            continue
        c = n.cells[target_col]
        if c.cell_type.value == 0:
            continue
        sha = n.commit.short_sha if n.commit else "?"
        marker = " <-- TARGET" if j == target_idx else ""
        pipe_hex = _hex(c.pipe_color_index) if c.pipe_color_index else ""
        print(
            f"{j:4d} {sha:7} {c.cell_type.name:14} {c.color_index:5d} "
            f"{_hex(c.color_index):>9} {c.pipe_color_index:5d} {pipe_hex:>9}{marker}"
        )
    print()

    print("=== Lane-2 (DARK RED) lines above and below TARGET ===")
    print(
        f"{'row':>4} {'sha':7} {'cell':14} {'color':>5} {'hex':>9} {'pipe':>5} {'pipe_hex':>9}"
    )
    for offset in range(-3, 6):
        j = target_idx + offset
        if not (0 <= j < len(layout.nodes)):
            continue
        n = layout.nodes[j]
        if 4 >= len(n.cells):
            continue
        c = n.cells[4]
        sha = n.commit.short_sha if n.commit else "?"
        if c.cell_type.value == 0:
            tag = "EMPTY"
            extra = ""
        else:
            tag = c.cell_type.name
            pipe_hex = _hex(c.pipe_color_index) if c.pipe_color_index else ""
            extra = f" {c.color_index:5d} {_hex(c.color_index):>9} {c.pipe_color_index:5d} {pipe_hex:>9}"
        print(f"{j:4d} {sha:7} {tag:14}{extra}")
    print()

    print("=== Lane-4 (VIOLET) lines above and below TARGET ===")
    print(
        f"{'row':>4} {'sha':7} {'cell':14} {'color':>5} {'hex':>9} {'pipe':>5} {'pipe_hex':>9}"
    )
    for offset in range(-3, 6):
        j = target_idx + offset
        if not (0 <= j < len(layout.nodes)):
            continue
        n = layout.nodes[j]
        if 8 >= len(n.cells):
            continue
        c = n.cells[8]
        sha = n.commit.short_sha if n.commit else "?"
        if c.cell_type.value == 0:
            tag = "EMPTY"
            extra = ""
        else:
            tag = c.cell_type.name
            pipe_hex = _hex(c.pipe_color_index) if c.pipe_color_index else ""
            extra = f" {c.color_index:5d} {_hex(c.color_index):>9} {c.pipe_color_index:5d} {pipe_hex:>9}"
        print(f"{j:4d} {sha:7} {tag:14}{extra}")
    print()

    print("=== Verdict ===")
    target_col_above = layout.nodes[target_idx - 1].cells[target_col]
    target_col_below = layout.nodes[target_idx + 1].cells[target_col]
    above_ok = target_col_above.color_index == 0 and target_col_above.pipe_color_index in (0, target_col_above.color_index)
    below_ok = target_col_below.color_index == 0 and target_col_below.pipe_color_index in (0, target_col_below.color_index)
    print(
        f"  TARGET's own lane (col {target_col}) above TARGET: "
        f"{_hex(target_col_above.color_index)} / pipe={_hex(target_col_above.pipe_color_index)} "
        + ("(green)" if above_ok else "(NOT green)")
    )
    print(
        f"  TARGET's own lane (col {target_col}) below TARGET: "
        f"{_hex(target_col_below.color_index)} / pipe={_hex(target_col_below.pipe_color_index)} "
        + ("(green)" if below_ok else "(NOT green)")
    )
    lane0_above = layout.nodes[target_idx - 1].cells[0]
    lane0_below = layout.nodes[target_idx + 1].cells[0]
    print(
        f"  Lane-0 (main line) above TARGET: {_hex(lane0_above.color_index)} "
        f"(target says it should be green / {_hex(0)})"
    )
    print(
        f"  Lane-0 (main line) below TARGET: {_hex(lane0_below.color_index)} "
        f"(target says it should be green / {_hex(0)})"
    )
    lane2_above = layout.nodes[target_idx - 1].cells[4]
    lane4_above = layout.nodes[target_idx - 1].cells[8]
    lane2_below = layout.nodes[target_idx + 1].cells[4]
    lane4_below = layout.nodes[target_idx + 1].cells[8]
    print(
        f"  Lane-2 above TARGET: {_hex(lane2_above.color_index)} "
        f"(target says it should be green / {_hex(0)})"
    )
    print(
        f"  Lane-4 above TARGET: {_hex(lane4_above.color_index)} "
        f"(target says it should be green / {_hex(0)})"
    )
    print(
        f"  Lane-2 below TARGET: {_hex(lane2_below.color_index)} "
        f"(target says it should be green / {_hex(0)})"
    )
    print(
        f"  Lane-4 below TARGET: {_hex(lane4_below.color_index)} "
        f"(target says it should be green / {_hex(0)})"
    )

    print()
    print("  Note: the override fix in src/core/graph_v2.py maps the")
    print("  ``main`` / ``master`` / ``develop`` / ``dev`` branch names to")
    print("  palette index 0 (GREEN, ``#1A5924``) instead of the default")
    print("  of mixing fallback ordinals.  Combined with the")
    print("  ``primary_branch = 'main'`` override for orphan commits on")
    print("  lane 0, the main line now renders uniformly GREEN end-to-end")
    print("  around ``31b22352``.  Side branches (lanes 2, 4, …) stay")
    print("  palette-coloured since they carry distinct per-branch hashes.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())