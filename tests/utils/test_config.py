"""Tests for :mod:`src.utils.config`.

Focus on the small robustness fixes in stage R2.5:

* **M18** — :func:`load_config` must return defaults when the JSON
  payload is not a mapping (lists, numbers, ``null``).
* **M22** — :func:`save_config` must use a temp-file + atomic rename
  pattern so a crash mid-write cannot leave a half-written config
  behind.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.utils.config import (
    _DEFAULT_CONFIG,
    load_config,
    save_config,
)


def _default_keys() -> set[str]:
    return set(_DEFAULT_CONFIG.keys())


def test_load_config_with_non_dict_json_returns_defaults(tmp_path: Path) -> None:
    """M18 — a file whose top-level value is a JSON list must not crash.

    Before the fix :func:`load_config` did
    ``{**_DEFAULT_CONFIG, **json.load(f)}``, which raises
    ``TypeError`` when the file contains ``[1, 2, 3]``. After the fix
    any non-mapping is treated as "no config" and the defaults are
    returned.
    """
    cfg = tmp_path / "config.json"
    cfg.write_text("[1, 2, 3]", encoding="utf-8")
    result = load_config(cfg)
    assert isinstance(result, dict)
    assert set(result.keys()) == _default_keys()
    assert result["theme"] == _DEFAULT_CONFIG["theme"]


@pytest.mark.parametrize(
    "payload, label",
    [
        ("[1, 2, 3]", "list"),
        ("42", "integer"),
        ("3.14", "float"),
        ('"hello"', "string"),
        ("true", "boolean"),
        ("null", "null"),
    ],
)
def test_load_config_rejects_every_non_mapping_json(
    tmp_path: Path, payload: str, label: str,
) -> None:
    """M18 — every non-object top-level JSON value must fall back to defaults.

    Parametrised so all six flavours of "not a dict" are pinned in one
    place; a regression on any single one will surface here.
    """
    cfg = tmp_path / "config.json"
    cfg.write_text(payload, encoding="utf-8")
    result = load_config(cfg)
    assert isinstance(result, dict), f"failed for {label}"
    assert set(result.keys()) == _default_keys()
    # The fallback must be a *copy* — mutating the returned dict must not
    # affect subsequent calls (otherwise later users see stale patches).
    result["theme"] = "mutated"
    again = load_config(cfg)
    assert again["theme"] == _DEFAULT_CONFIG["theme"]


def test_load_config_returns_independent_dict_each_call(tmp_path: Path) -> None:
    """M18 — the returned defaults dict must not be a shared alias.

    ``_DEFAULT_CONFIG`` is a module-level mutable; returning it directly
    would let callers corrupt the defaults for the whole process.
    """
    cfg = tmp_path / "missing.json"  # does not exist
    a = load_config(cfg)
    b = load_config(cfg)
    assert a is not b
    a["theme"] = "mutated"
    assert b["theme"] == _DEFAULT_CONFIG["theme"]


def test_save_config_atomic_via_tmp(tmp_path: Path) -> None:
    """M22 — successful save must leave no ``.tmp`` sibling behind.

    The atomic save pattern is: ``write to <path>.tmp`` then
    ``os.replace`` over ``<path>``. If rename succeeded the temp file
    must be gone; if it leaked, the test catches it.
    """
    target = tmp_path / "config.json"
    tmp = target.with_suffix(target.suffix + ".tmp")
    data = {"theme": "light", "panel_layout": {}, "recent_repos": []}
    save_config(target, data)
    assert target.exists()
    assert not tmp.exists(), "tmp file leaked — atomic rename did not happen"


def test_save_config_atomic_creates_parent_dirs(tmp_path: Path) -> None:
    """M22 — a deeply nested config path must still be created on demand."""
    target = tmp_path / "nested" / "deeper" / "config.json"
    save_config(target, {"theme": "dark"})
    assert target.is_file()
    assert json.loads(target.read_text(encoding="utf-8")) == {"theme": "dark"}


def test_save_config_round_trip_with_dict_payload(tmp_path: Path) -> None:
    """M22 — what we write must be what we read back (``load_config`` plumbed in)."""
    target = tmp_path / "config.json"
    payload = {
        "theme": "light",
        "panel_layout": {"left": [10, 20]},
        "recent_repos": ["/some/repo"],
        "active_repo": "/some/repo",
    }
    save_config(target, payload)
    loaded = load_config(target)
    assert loaded["theme"] == "light"
    assert loaded["panel_layout"] == {"left": [10, 20]}
    assert loaded["recent_repos"] == ["/some/repo"]
    assert loaded["active_repo"] == "/some/repo"
    # Defaults must still be present for keys the payload did not set.
    assert "hotkeys" in loaded


def test_save_config_overwrites_existing_file_atomically(tmp_path: Path) -> None:
    """M22 — writing twice in a row keeps the second value and no temp files."""
    target = tmp_path / "config.json"
    save_config(target, {"theme": "first"})
    save_config(target, {"theme": "second"})
    assert json.loads(target.read_text(encoding="utf-8"))["theme"] == "second"
    # No siblings left behind.
    siblings = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert siblings == []


def test_load_config_with_invalid_json_returns_defaults(tmp_path: Path) -> None:
    """M18 — pre-existing behaviour: bad JSON still falls back to defaults."""
    cfg = tmp_path / "config.json"
    cfg.write_text("{ this is : not json }", encoding="utf-8")
    result = load_config(cfg)
    assert isinstance(result, dict)
    assert set(result.keys()) == _default_keys()
