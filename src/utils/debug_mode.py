"""Debug-mode helpers for graph layout inspection.

This module gates optional diagnostic output for the commit graph
renderer. It is intentionally a no-op by default — the flag flips to
``True`` when the user (or a test) sets the ``GIT_PY_GRAPH_DEBUG``
environment variable, after which :func:`dump_graph` will write a
human-readable description of the current layout to ``stderr``.

The module exists to keep :mod:`src.viewmodels.graph_viewmodel` free
of conditional imports — the graph ViewModel pulls
:func:`is_debug_mode` and :func:`dump_graph` in unconditionally, and
``git_viewmodel`` itself stays a thin shim that does not know whether
debug output is enabled.  When the env var is not set, both calls are
no-ops and the production overhead is a single function-call per
``graph_updated`` emission.
"""
from __future__ import annotations

import os
from typing import Any

_DEBUG_ENV = "GIT_PY_GRAPH_DEBUG"
_DEBUG = bool(int(os.environ.get("GIT_PY_DEBUG", "0")))


def is_debug() -> bool:
    """Return whether general diagnostic output is enabled."""
    return _DEBUG


def debug_print(*args, **kwargs) -> None:
    """Print diagnostic output only when ``GIT_PY_DEBUG=1``."""
    if _DEBUG:
        print(*args, **kwargs)


def is_debug_mode() -> bool:
    """Return ``True`` if the user has opted into graph debug dumps.

    Reads the env var at call time so tests can flip the flag without
    having to reload the module. The check is intentionally cheap —
    a single :func:`os.getenv` — so production code pays no real
    cost when the flag is off.
    """
    return os.environ.get(_DEBUG_ENV) == "1"


def dump_graph(layout: Any, stash_sha_set: set[str] | None = None) -> None:
    """Print a short, human-readable summary of the graph layout to stderr.

    ``layout`` is the list of row dicts produced by
    :func:`src.core.graph_v2.graph_to_dicts`. ``stash_sha_set`` is an
    optional set of stash SHAs used to label stash nodes distinctly in
    the dump.

    The function is a no-op unless :func:`is_debug_mode` returns
    ``True`` — the production code path never sees the body of this
    function execute. The output is intentionally compact (one line
    per row) so the dump does not flood the terminal on large
    repositories.
    """
    if not is_debug_mode():
        return
    import sys

    stash_sha_set = stash_sha_set or set()
    for row in layout:
        commit = row.get("commit") or {}
        sha = commit.get("sha", "")[:7]
        kind = "WIP" if row.get("is_uncommitted") else (
            "stash" if sha in stash_sha_set else "commit"
        )
        lane = row.get("lane", 0)
        cells = row.get("cells", [])
        print(
            f"[graph-debug] row={row.get('row', '?'):>3} "
            f"lane={lane:>2} {kind:>6} sha={sha:<7} cells={len(cells)}",
            file=sys.stderr,
        )
