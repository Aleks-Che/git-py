"""Dump cell rows around given commits of a real repository.

Replicates GraphViewModel._compute_graph (history + stash insertion)
and prints each row's cells around the target SHAs so colour/lane
bugs can be inspected without the GUI.

Usage:  python tools/dump_graph_cells.py <repo_path> <sha_prefix> [sha_prefix...]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from src.core.graph_v2 import BRANCH_PALETTE, CellType, build_graph  # noqa: E402
from src.core.models import CommitInfo  # noqa: E402
from src.core.repository import RepositoryManager  # noqa: E402


def main() -> None:
    repo_path, targets = sys.argv[1], sys.argv[2:]
    repo = RepositoryManager(repo_path)
    history = repo.get_all_history(max_count=500)
    branches = repo.branches
    head_target = str(repo.repo.head.target) if not repo.repo.head_is_detached else None

    head_idx = 0
    if head_target is not None:
        for i, c in enumerate(history):
            if c.sha == head_target:
                head_idx = i
                break
    for entry in repo.stash_list:
        parent = entry.parent_sha or head_target
        ci = CommitInfo(
            sha=entry.sha,
            short_sha=entry.sha[:7],
            message=f"Stash @{{{entry.index}}}",
            author_name="",
            author_email="",
            author_time=entry.author_time or int(time.time()),
            committer_name="",
            committer_email="",
            committer_time=entry.author_time or int(time.time()),
            parents=[parent] if parent else [],
            kind="stash",
        )
        t = ci.author_time
        idx = 0
        while idx < len(history) and history[idx].author_time > t:
            idx += 1
        while idx < len(history) and history[idx].author_time == t:
            idx += 1
        if ci.parents and ci.parents[0] == head_target:
            idx = min(idx, head_idx)
        history.insert(idx, ci)
        if idx <= head_idx:
            head_idx += 1

    layout = build_graph(history, branches, uncommitted_count=None,
                         head_commit_sha=head_target)
    rows = layout.nodes

    def colour(idx: int) -> str:
        if idx >= len(BRANCH_PALETTE):
            return f"#{idx}"
        return BRANCH_PALETTE[idx]

    hit_rows: set[int] = set()
    for i, n in enumerate(rows):
        if n.commit and any(n.commit.sha.startswith(t) for t in targets):
            hit_rows.update(range(max(0, i - 6), min(len(rows), i + 4)))

    prev = -2
    for i in sorted(hit_rows):
        if i != prev + 1:
            print("    ...")
        prev = i
        n = rows[i]
        if n.commit is None:
            continue
        mark = ">>" if any(n.commit.sha.startswith(t) for t in targets) else "  "
        cells_desc = []
        for col, cell in enumerate(n.cells):
            if cell.cell_type == CellType.EMPTY:
                continue
            desc = f"{col}:{cell.cell_type.name}(c={cell.color_index}"
            if cell.cell_type in (
                CellType.HORIZONTAL_PIPE, CellType.TEE_RIGHT, CellType.TEE_LEFT,
                CellType.TEE_UP, CellType.CROSS,
            ):
                desc += f",p={cell.pipe_color_index}"
            if cell.cell_type == CellType.CROSS:
                desc += f",d={cell.direction}"
            desc += f" {colour(cell.color_index)}"
            if cell.cell_type in (
                CellType.HORIZONTAL_PIPE, CellType.TEE_RIGHT, CellType.TEE_LEFT,
                CellType.TEE_UP, CellType.CROSS,
            ):
                desc += f"/{colour(cell.pipe_color_index)}"
            desc += ")"
            cells_desc.append(desc)
        kind = n.commit.kind
        print(
            f"{mark} row {i:3d} lane={n.lane} ci={n.color_index}({colour(n.color_index)}) "
            f"{n.commit.short_sha} [{kind}] {n.commit.message[:40]!r}"
        )
        print(f"      {' '.join(cells_desc)}")


if __name__ == "__main__":
    main()
