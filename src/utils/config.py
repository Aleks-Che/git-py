"""Configuration loader/saver (JSON) and settings helpers.

Per ``docs/DEVELOPMENT_RULES.md`` (section 7), paths, hotkeys, theme
parameters, and panel layout live in JSON/YAML configs and are
persisted on exit / restored on launch. The helpers here intentionally
tolerate missing or malformed files: a fresh install should never fail
because the config is absent.

Stage 9 extensions
------------------
Window geometry and splitter sizes are persisted to the same JSON
file (under the ``window_size`` and ``splitter_sizes`` keys). Two
specialised helpers (:func:`load_window_size`,
:func:`load_splitter_sizes`) do the coercion; :func:`default_config_path`
returns the per-user path Qt recommends for app config.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pygit2

# Default window size used when no config exists yet or when the
# saved value is unusable. Matches the historical hard-coded value
# in :class:`src.ui.main_window.MainWindow.__init__` so users who
# upgrade keep the same starting geometry.
DEFAULT_WINDOW_WIDTH = 1280
DEFAULT_WINDOW_HEIGHT = 800

# ``splitter_sizes`` is a ``{name: [int, ...]}`` mapping; this constant
# lists the splitter names :class:`MainWindow` writes. The two
# splitters the user explicitly asked to persist are
# ``"horizontal"`` (left panel | graph | right panel) and
# ``"right_vertical"`` (commit panel | commit detail).
SPLITTER_KEY_HORIZONTAL = "horizontal"
SPLITTER_KEY_RIGHT_VERTICAL = "right_vertical"
SPLITTER_KEY_GRAPH = "graph"

# Key in the top-level config dict holding per-repo graph column widths.
# Value is ``{repo_path: [branch_lbl_w, graph_w, commit_msg_w]}``.
GRAPH_CONFIGS_KEY = "graph_configs"

_DEFAULT_CONFIG: dict[str, Any] = {
    "theme": "dark",
    "panel_layout": {},
    "hotkeys": {
        "undo": "Ctrl+Z",
        "redo": "Ctrl+Y",
        "fetch": "Ctrl+Shift+F",
        "pull": "Ctrl+Shift+P",
        "push": "Ctrl+Shift+U",
        "stash_push": "Ctrl+Shift+S",
        "stash_pop": "Ctrl+Shift+O",
    },
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
    # Persisted window size. Filled in by :class:`MainWindow` on
    # close; restored on next launch. Missing / invalid → use
    # :data:`DEFAULT_WINDOW_WIDTH` / :data:`DEFAULT_WINDOW_HEIGHT`.
    "window_size": [DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT],
    # Persisted ``QSplitter`` sizes. Keys are
    # :data:`SPLITTER_KEY_HORIZONTAL` and
    # :data:`SPLITTER_KEY_RIGHT_VERTICAL`; values are ``[int, ...]``.
    "splitter_sizes": {},
    # Author identity for commits (overridden by Git config when
    # ``use_default_git_credentials`` is ``True``).
    "author_name": "",
    "author_email": "",
    # SSH key paths for remotes that use SSH transport.
    "ssh_private_key": "",
    "ssh_public_key": "",
    # When ``True``, read author name/email from ``git config`` instead of
    # the ``author_name`` / ``author_email`` keys above.
    "use_default_git_credentials": True,
    # Recent repositories shown in the tab bar (list of absolute paths).
    "recent_repos": [],
    # The active repository path (str or null).
    "active_repo": None,
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


def default_config_path() -> Path:
    """Return the per-user config path the app uses in production.

    Uses Qt's :class:`QStandardPaths.AppConfigLocation`, which on
    common platforms resolves to:

    * **Windows:** ``%APPDATA%\\git-py\\config.json``
    * **macOS:**   ``~/Library/Preferences/git-py/config.json``
    * **Linux:**   ``~/.config/git-py/config.json``

    The directory is created lazily by :func:`save_config`; this
    function only computes the path. Tests should pass a ``tmp_path``
    explicitly to :class:`MainWindow` to avoid touching the real
    user config.
    """
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation,
    )
    return Path(base) / "git-py" / "config.json"


def _coerce_window_size(value: Any) -> tuple[int, int] | None:
    """Coerce ``value`` to ``(width, height)`` or ``None`` on any failure.

    Both components must be positive integers. A boolean is rejected
    explicitly (``bool`` is a subclass of ``int`` in Python and would
    otherwise slip through the ``int`` check).
    """
    if not isinstance(value, list | tuple) or len(value) != 2:
        return None
    raw_w, raw_h = value
    if isinstance(raw_w, bool) or isinstance(raw_h, bool):
        return None
    if not isinstance(raw_w, int) or not isinstance(raw_h, int):
        return None
    if raw_w <= 0 or raw_h <= 0:
        return None
    return raw_w, raw_h


def load_window_size(config: dict[str, Any]) -> tuple[int, int]:
    """Return ``(width, height)`` from ``config``; defaults if missing / invalid."""
    size = _coerce_window_size(config.get("window_size"))
    if size is None:
        return (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
    return size


def _coerce_splitter_sizes(value: Any) -> dict[str, list[int]]:
    """Coerce ``value`` to a ``{name: [int, ...]}`` mapping; ``{}`` on failure.

    Each entry's values must be non-negative integers. A boolean in
    the list is rejected (same rationale as :func:`_coerce_window_size`).
    """
    if not isinstance(value, dict):
        return {}
    result: dict[str, list[int]] = {}
    for key, sizes in value.items():
        if not isinstance(key, str) or not isinstance(sizes, list | tuple):
            continue
        coerced: list[int] = []
        for s in sizes:
            if isinstance(s, bool) or not isinstance(s, int):
                coerced = []
                break
            if s < 0:
                coerced = []
                break
            coerced.append(s)
        if not coerced:
            continue
        result[key] = coerced
    return result


def load_splitter_sizes(config: dict[str, Any]) -> dict[str, list[int]]:
    """Return the persisted splitter sizes from ``config``; ``{}`` on failure."""
    return _coerce_splitter_sizes(config.get("splitter_sizes"))


def load_hotkey(
    config: dict[str, Any], action_key: str, default: str,
) -> str:
    """Return the hotkey for *action_key* from *config*; fall back to *default*.

    Reads from the ``"hotkeys"`` sub-dict.  Returns *default* when the
    key is missing, not a string, or the config has no ``"hotkeys"``
    mapping at all.
    """
    hotkeys = config.get("hotkeys")
    if not isinstance(hotkeys, dict):
        return default
    value = hotkeys.get(action_key)
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip()


def load_graph_column_widths(
    config: dict[str, Any], repo_path: str | None,
) -> list[int] | None:
    """Return ``[branch_lbl_w, graph_w, commit_msg_w]`` for *repo_path*.

    Returns ``None`` when no per-repo entry exists — callers should
    fall back to the graph panel's built-in defaults.
    """
    if not repo_path:
        return None
    graph_configs = config.get(GRAPH_CONFIGS_KEY)
    if not isinstance(graph_configs, dict):
        return None
    widths = graph_configs.get(repo_path)
    if not isinstance(widths, list) or len(widths) != 3:
        return None
    result: list[int] = []
    for w in widths:
        if isinstance(w, bool) or not isinstance(w, int) or w <= 0:
            return None
        result.append(w)
    return result


def save_graph_column_widths(
    config: dict[str, Any], repo_path: str, widths: list[int],
) -> None:
    """Write per-repo graph column widths into *config* (mutates in-place)."""
    graph_configs: dict[str, Any] = config.setdefault(GRAPH_CONFIGS_KEY, {})
    if not isinstance(graph_configs, dict):
        graph_configs = {}
        config[GRAPH_CONFIGS_KEY] = graph_configs
    graph_configs[repo_path] = list(widths)


def load_author_signature(
    config: dict[str, Any] | None = None,
) -> pygit2.Signature:
    """Return a :class:`pygit2.Signature` from app config.

    When ``config`` is ``None`` or the ``use_default_git_credentials``
    flag is ``True``, the signature is read from ``git config``
    (``user.name`` / ``user.email``).  Otherwise the ``author_name``
    and ``author_email`` keys are used.  Falls back to ``("git-py",
    "git-py@localhost")`` when neither source provides a value.
    """
    import time

    import pygit2

    if config is None:
        config = {}

    use_default = config.get("use_default_git_credentials", True)
    if use_default:
        try:
            name = _git_config_get("user.name")
            email = _git_config_get("user.email")
            if name and email:
                return pygit2.Signature(name, email, int(time.time()), 0)
        except Exception:
            pass
        return pygit2.Signature("git-py", "git-py@localhost", int(time.time()), 0)

    name = (config.get("author_name") or "").strip()
    email = (config.get("author_email") or "").strip()
    if not name or not email:
        return pygit2.Signature("git-py", "git-py@localhost", int(time.time()), 0)
    return pygit2.Signature(name, email, int(time.time()), 0)


def _git_config_get(key: str) -> str:
    """Read a single ``git config --global`` key, returning ``""`` on failure."""
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "config", "--global", key],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


__all__ = [
    "DEFAULT_WINDOW_HEIGHT",
    "DEFAULT_WINDOW_WIDTH",
    "GRAPH_CONFIGS_KEY",
    "SPLITTER_KEY_GRAPH",
    "SPLITTER_KEY_HORIZONTAL",
    "SPLITTER_KEY_RIGHT_VERTICAL",
    "default_config_path",
    "get_int",
    "load_author_signature",
    "load_config",
    "load_graph_column_widths",
    "load_hotkey",
    "load_splitter_sizes",
    "save_config",
    "save_graph_column_widths",
]
