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
    load_push_timeout,
    save_config,
)


def _default_keys() -> set[str]:
    return set(_DEFAULT_CONFIG.keys())


@pytest.mark.parametrize("value", [0, -1, 86401, True, False, "3600", 1.5, None, [], {}])
def test_invalid_push_timeout_falls_back_to_thirty_minutes(tmp_path, value):
    config_path = tmp_path / "config.json"
    save_config(config_path, {"push_timeout_seconds": value})
    assert load_push_timeout(load_config(config_path)) == 1800


@pytest.mark.parametrize("timeout", [1, 3600, 86400])
def test_push_timeout_roundtrip(tmp_path, timeout):
    config_path = tmp_path / "config.json"
    save_config(config_path, {"push_timeout_seconds": timeout})
    assert load_push_timeout(load_config(config_path)) == timeout


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


@pytest.mark.parametrize("key,value", [
    ("author_name", 123),
    ("author_email", []),
    ("ssh_private_key", {}),
    ("ssh_public_key", "bad\0path"),
    ("use_default_git_credentials", "false"),
    ("auto_fetch_enabled", 1),
    ("auto_fetch_interval_ms", 2**40),
    ("graph_history_limit", 0),
    ("graph_history_limit", -1),
    ("graph_history_limit", True),
    ("command_processor_history_size", 0),
    ("discard_file_max_backup_bytes", -1),
    ("merge_async_threshold", -1),
    ("window_size", [2**40, 800]),
    ("diff_view_mode", []),
    ("diff_view_mode", {}),
    ("recent_repos", "repo"),
    ("active_repo", []),
    ("ai", []),
    ("hotkeys", []),
])
def test_known_config_fields_reject_bad_types_and_ranges(tmp_path, key, value):
    target = tmp_path / "config.json"
    save_config(target, {key: value})
    defaults = load_config(tmp_path / "missing.json")
    assert load_config(target)[key] == defaults[key]


def test_config_keeps_valid_paths_and_nested_extension_keys(tmp_path):
    target = tmp_path / "config.json"
    save_config(target, {
        "recent_repos": [123, "repo", "repo", None, "", "bad\0path", "other"],
        "active_repo": 123,
        "ai": {"model": "custom-model", "timeout_seconds": -1, "extension": {"x": 1}},
        "hotkeys": {"undo": "Alt+Z", "redo": [], "custom": "Alt+X"},
        "future": {"enabled": True},
    })
    config = load_config(target)
    assert config["recent_repos"] == ["repo", "other"]
    assert config["active_repo"] is None
    assert config["ai"]["model"] == "custom-model"
    assert config["ai"]["timeout_seconds"] == 60
    assert config["ai"]["extension"] == {"x": 1}
    assert config["hotkeys"]["undo"] == "Alt+Z"
    assert config["hotkeys"]["redo"] == "Ctrl+Y"
    assert config["hotkeys"]["custom"] == "Alt+X"
    save_config(target, config)
    assert load_config(target)["future"] == {"enabled": True}


@pytest.mark.parametrize("interval", [0, -10])
def test_nonpositive_fetch_interval_disables_auto_fetch(tmp_path, interval):
    target = tmp_path / "config.json"
    save_config(target, {"auto_fetch_enabled": True, "auto_fetch_interval_ms": interval})
    assert load_config(target)["auto_fetch_enabled"] is False


def test_invalid_utf8_config_falls_back(tmp_path):
    target = tmp_path / "config.json"
    target.write_bytes(b"\xff")
    assert load_config(target) == load_config(tmp_path / "missing.json")


def test_overlapping_saves_use_independent_temporary_files(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    target = tmp_path / "config.json"
    barrier = Barrier(2)
    original_dump = json.dump

    def overlapping_dump(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_dump(*args, **kwargs)

    monkeypatch.setattr("src.utils.config.json.dump", overlapping_dump)
    payloads = [{"writer": "first"}, {"writer": "second", "text": "x" * 100}]
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(save_config, target, payload) for payload in payloads]
        for future in futures:
            future.result(timeout=5)
    assert json.loads(target.read_text(encoding="utf-8")) in payloads
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_replace_keeps_previous_config_and_removes_temp(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    save_config(target, {"author_name": "Original"})

    def deny_replace(*_args):
        raise PermissionError("test: read-only destination")

    monkeypatch.setattr("src.utils.config.os.replace", deny_replace)
    with pytest.raises(PermissionError):
        save_config(target, {"author_name": "Changed"})
    assert load_config(target)["author_name"] == "Original"
    assert list(tmp_path.glob("*.tmp")) == []


def test_graph_preferences_accept_old_path_aliases_and_skip_invalid_keys(tmp_path):
    from src.utils.config import load_graph_column_widths, save_graph_column_widths

    repo = tmp_path / "Repo"
    repo.mkdir()
    old_path = str(repo / ".." / "Repo")
    config = {"graph_configs": {"bad\0path": [1, 2, 3], old_path: [260, 140, 100]}}
    assert load_graph_column_widths(config, repo.as_posix()) == [260, 140, 100]
    save_graph_column_widths(config, repo.as_posix(), [280, 160, 100])
    assert old_path not in config["graph_configs"]
    assert load_graph_column_widths(config, old_path) == [280, 160, 100]
