"""Diagnostic dump helpers for the swimlane graph model.

Call :func:`dump_graph_rows` with the output of ``nodes_to_rows(nodes)``
(or any ``list[dict]`` carrying ``lane``/``display_column``/``input_lanes``/
``output_lanes``) and it prints an ASCII table showing how lanes flow
between rows.

Useful for diagnosing layout problems — quickly reveals mismatches
between node positions and edge lane indices.
"""

from __future__ import annotations

import logging
import sys

_log = logging.getLogger(__name__)


def dump_graph_rows(rows: list[dict], *, file=None) -> None:
    """Print a swimlane-ascii table for every row in the graph.

    Each row shows::

        Row 0  a1b2c3d4  l=0 dc=0  [*main*]  →  [d5e6f7, b8c9d0]
                │                  │               │
    """
    if file is None:
        file = sys.stderr

    if not rows:
        print("(empty graph)", file=file)
        return

    lane_w = 9  # columns per lane in the ascii dump

    for i, row in enumerate(rows):
        sha = row["sha"][:8]
        lane = row.get("lane", -1)
        dc = row.get("display_column", -1)
        color = row.get("color", "?")

        # Count lanes
        input_lanes: list[dict] = row.get("input_lanes", [])
        output_lanes: list[dict] = row.get("output_lanes", [])
        max_lane = max(len(input_lanes), len(output_lanes), lane + 1)

        # Header line
        refs = _format_refs(row)
        line = (
            f"Row {row.get('row', '?'):>3}  "
            f"{sha:>10}  "
            f"l={lane:>2} dc={dc:>2}  "
            f"{color:>9}"
        )
        if refs:
            line += f"  {refs}"
        print(line, file=file)

        # Input lanes visual
        _print_lane_row(file, "IN ", input_lanes, lane, max_lane, lane_w)

        # Output lanes visual (no highlighting — it's a different set from input)
        _print_lane_row(file, "OUT", output_lanes, -1, max_lane, lane_w)

        # Edge ASCII between this row and next (if output→next-input mapping)
        if i + 1 < len(rows):
            next_input: list[dict] = rows[i + 1].get("input_lanes", [])
            _print_edge_row(file, output_lanes, next_input, max_lane, lane_w)

        print(file=file)


def dump_graph_edges(rows: list[dict], *, file=None) -> None:
    """Print a compact edge map: output_lane → next_input_lane for each row pair."""
    if file is None:
        file = sys.stderr
    for i, row in enumerate(rows):
        if i + 1 >= len(rows):
            break
        next_row = rows[i + 1]
        out = row.get("output_lanes", [])
        inp = next_row.get("input_lanes", [])
        inp_map: dict[str, int] = {}
        for j, e in enumerate(inp):
            inp_map[e["sha"]] = j

        print(
            f"Row {row.get('row', i)} → Row {next_row.get('row', i + 1)}:",
            file=file,
        )
        for lane_i, e in enumerate(out):
            next_lane = inp_map.get(e["sha"])
            if next_lane is None:
                print(
                    f"  lane {lane_i} (sha={e['sha'][:8]}) -> TERMINATES",
                    file=file,
                )
            elif next_lane == lane_i:
                print(
                    f"  lane {lane_i} (sha={e['sha'][:8]}) -> lane {next_lane} (straight)",
                    file=file,
                )
            else:
                print(
                    f"  lane {lane_i} (sha={e['sha'][:8]}) -> lane {next_lane} (SHIFT)",
                    file=file,
                )


def check_graph_integrity(rows: list[dict]) -> list[str]:
    """Validate swimlane consistency; returns list of problems (empty = ok)."""
    problems: list[str] = []
    for i, row in enumerate(rows):
        lane = row.get("lane", -1)
        inp = row.get("input_lanes", [])

        if lane > len(inp):
            problems.append(
                f"Row {i}: lane={lane} but input_lanes has only {len(inp)} entries"
            )

        if i + 1 >= len(rows):
            continue
        next_row = rows[i + 1]
        out = row.get("output_lanes", [])
        next_inp = next_row.get("input_lanes", [])

        next_inp_shas = {e["sha"] for e in next_inp}
        out_shas = {e["sha"] for e in out}

        missing = out_shas - next_inp_shas
        extra = next_inp_shas - out_shas

        if missing:
            problems.append(
                f"Row {i}→{i+1}: {len(missing)} SHA(s) in output_lanes not in next input_lanes: "
                + ", ".join(s[:8] for s in sorted(missing)[:5]),
            )
        if extra:
            problems.append(
                f"Row {i}→{i+1}: {len(extra)} SHA(s) in next input_lanes "
                f"not in current output_lanes: "
                + ", ".join(s[:8] for s in sorted(extra)[:5]),
            )

        # Check SHA dedup in output_lanes
        seen: set[str] = set()
        for e in out:
            if e["sha"] in seen:
                problems.append(
                    f"Row {i}: duplicate SHA {e['sha'][:8]} in output_lanes"
                )
            seen.add(e["sha"])

    # Check that every displayed node has its SHA in input_lanes
    for i, row in enumerate(rows):
        sha = row["sha"]
        inp = row.get("input_lanes", [])
        found = any(e["sha"] == sha for e in inp)
        if not found:
            problems.append(
                f"Row {i}: commit {sha[:8]} not found in its own input_lanes"
            )

    return problems


# ── internal helpers ──────────────────────────────────────────────────


def _format_refs(row: dict) -> str:
    parts: list[str] = []
    for r in row.get("refs", []):
        parts.append(r)
    for b in row.get("branch_refs", []):
        name = b.get("name", "?")
        if b.get("is_head"):
            name = f"*{name}*"
        parts.append(name)
    return " ".join(parts) if parts else ""


def _short_sha(sha: str) -> str:
    return sha[:8] if len(sha) >= 8 else sha.ljust(8)


def _print_lane_row(
    file,
    label: str,
    lanes: list[dict],
    highlight_idx: int,
    total: int,
    lane_w: int,
    *,
    highlight_lane: int = -1,
) -> None:
    line = f"  {label}  "
    for li in range(max(total, 1)):
        if li < len(lanes):
            text = _short_sha(lanes[li]["sha"])
        else:
            text = "." * 8
        if li == highlight_idx or li == highlight_lane:
            text = f"[{text[:6]}]"
        else:
            text = f" {text[:6]} "
        line += text.ljust(lane_w)
    print(line, file=file)


def _print_edge_row(
    file,
    out: list[dict],
    inp: list[dict],
    total: int,
    lane_w: int,
) -> None:
    inp_map: dict[str, int] = {}
    for j, e in enumerate(inp):
        inp_map[e["sha"]] = j

    line = "  EDGE "
    for li in range(max(total, 1)):
        if li < len(out):
            entry = out[li]
            next_li = inp_map.get(entry["sha"])
            if next_li is None:
                char = "x"
            elif next_li == li:
                char = "|"
            else:
                char = "\\" if next_li > li else "/"
        else:
            char = " "
        line += (" " * (lane_w // 2) + char).ljust(lane_w)
    print(line, file=file)
