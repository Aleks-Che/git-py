"""Configuration loader/saver (JSON).

Per ``docs/DEVELOPMENT_RULES.md`` (section 7), paths, hotkeys, theme
parameters, and panel layout live in JSON/YAML configs and are
persisted on exit / restored on launch. The helpers here intentionally
tolerate missing or malformed files: a fresh install should never fail
because the config is absent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG: dict[str, Any] = {
    "theme": "dark",
    "panel_layout": {},
    "hotkeys": {},
    # Merge operations touching more than this many files are routed
    # through :class:`AsyncWorker` so the UI stays responsive.
    # Rebase is always async regardless of size.
    "merge_async_threshold": 50,
    # Auto-fetch every N milliseconds when a repository is open. Set
    # to 0 (or any non-positive value) to disable. Default is 60 s.
    "auto_fetch_interval_ms": 60_000,
    # Whether the auto-fetch timer is enabled. Default off; the UI
    # toggle (Stage 9) will flip this on first launch.
    "auto_fetch_enabled": False,
}

# Keys that must be ints (validation on load; bad values fall back).
_INT_KEYS = frozenset({"merge_async_threshold", "auto_fetch_interval_ms"})


def get_int(config: dict[str, Any], key: str, default: int) -> int:
    """Read an int-valued config key, returning ``default`` on bad / missing values."""
    value = config.get(key)
    if isinstance(value, bool):  # bool is a subclass of int, reject explicitly
        return default
    if isinstance(value, int):
        return value
    return default


def load_config(path: Path | str) -> dict[str, Any]:
    """Read the config from ``path``; return defaults on any failure."""
    p = Path(path)
    if not p.is_file():
        return dict(_DEFAULT_CONFIG)
    try:
        with p.open("r", encoding="utf-8") as f:
            return {**_DEFAULT_CONFIG, **json.load(f)}
    except (OSError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)


def save_config(path: Path | str, data: dict[str, Any]) -> None:
    """Write ``data`` as pretty-printed JSON to ``path`` (mkdir parents first)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
