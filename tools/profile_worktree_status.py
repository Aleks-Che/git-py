"""Read-only status/GIL benchmark: python -m tools.profile_worktree_status PATH ..."""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from unittest.mock import patch

from src.core.repository import RepositoryManager
from src.core.worktree_status import read_other_worktree_changes, read_worktree_status
from src.viewmodels.graph_viewmodel import GraphViewModel


def measure(read):
    gaps = []
    stop = threading.Event()
    ready = threading.Event()

    def heartbeat():
        previous = time.perf_counter()
        ready.set()
        while not stop.wait(0.01):
            now = time.perf_counter()
            gaps.append(now - previous)
            previous = now

    thread = threading.Thread(target=heartbeat)
    thread.start()
    ready.wait()
    try:
        start = time.perf_counter()
        result = read()
        elapsed = time.perf_counter() - start
        # Include the final slow native call in the heartbeat sample.
        time.sleep(0.02)
    finally:
        stop.set()
        thread.join()
    return result, elapsed, max(gaps, default=0)


def profile(path, repeat):
    def snapshot():
        manager = RepositoryManager(path)
        try:
            return read_worktree_status(manager, None, False), read_other_worktree_changes(manager)
        finally:
            manager.close()

    readers = {
        "pygit2": lambda manager: dict(manager.repo.status()),
        "git_process": RepositoryManager.get_raw_status,
    }
    results = {"path": path}
    snapshots = {}
    for name, reader in readers.items():
        samples = []
        with patch.object(RepositoryManager, "get_raw_status", reader):
            for _ in range(repeat):
                value, elapsed, gap = measure(snapshot)
                snapshots[name] = value
                samples.append((elapsed, gap))
        results[name] = {
            "median_scan_ms": round(statistics.median(s[0] for s in samples) * 1000, 1),
            "max_python_pause_ms": round(max(s[1] for s in samples) * 1000, 1),
        }
    current, siblings = snapshots["git_process"]
    results["same_status"] = snapshots["pygit2"] == snapshots["git_process"]
    results["changed_paths"] = len(current.raw_status)
    results["dirty_siblings"] = len(siblings)
    manager = RepositoryManager(path)
    try:
        start = time.perf_counter()
        rows, error = GraphViewModel._compute_graph(
            manager, other_worktrees=siblings, raw_status=current.raw_status,
        )
        results["graph_from_snapshot_ms"] = round((time.perf_counter() - start) * 1000, 1)
        results["graph_rows"] = len(rows)
        results["graph_error"] = error
    finally:
        manager.close()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    for path in args.paths:
        print(json.dumps(profile(path, args.repeat), ensure_ascii=True, indent=2), flush=True)
