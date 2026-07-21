# STATUS — Stage R2.5
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** ff828bd (R2.4)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (H10, M2, M18, M22 — small state bugs)

Sub-agent delivered everything; STATUS file missing (budget cap). Salvaged below.

### Code changes

**`src/viewmodels/repo_tabs_viewmodel.py`** — H10 fix in `remove_tab()`. The previous mask `if self._active_index >= len(self._tabs): self._active_index = len(self._tabs) - 1` accidentally hid the off-by-one for the case where a tab left of the active one is removed (3 tabs `[a, b, c]`, active=`b` idx 1, remove tab 0 → expected active still points at `b`, old code left it at index 1 = `c`). Explicit `elif index < self._active_index: self._active_index -= 1` branch added.

**`src/core/repository.py`** — M2 fix applied uniformly to `open()`, `init()`, `clone()`: build `pygit2.Repository` (or `init_repository`) into a local first, then assign `self._repo = local_repo` only after full success. Avoids leaving `_repo` half-built when pygit2 raises mid-construction.

**`src/utils/config.py`** — M18: `isinstance(data, dict)` guard in `load_config()` before merging into defaults. Previously `{**_DEFAULT_CONFIG, **json.load(f)}` would raise `TypeError` on list/scalar/None JSON. M22: atomic `save_config` via `tmp + os.replace` plus `flush()` and optional `fsync()`. Cleanup `tmp.unlink(missing_ok=True)` on failure. New `import os` at top.

**`src/ui/main_window.py`** — wired `right_panel._commit_detail.error_occurred` (line 174) to `_on_error` and `_log_widget.append_log` alongside the other `commit_detail` signal connections. The signal already existed and was already emitted at line 710 — only the wiring in `MainWindow` was missing.

### Tests added (+13 in the new tests/utils/test_config.py, +4 across existing files, +2 in test_repo_tabs_viewmodel.py, +2 in test_repository.py)

- `tests/viewmodels/test_repo_tabs_viewmodel.py` — 2 new: original spec case + the actually-broken corner case.
- `tests/core/test_repository.py` — 2 new: invalid path leaves `_repo` untouched, init on bad path same.
- `tests/utils/__init__.py` — new package marker.
- `tests/utils/test_config.py` — 13 tests:
  - 6 parametrised non-dict JSON flavours (`[]`, `42`, `"x"`, `null`, `true`, nested-list-of-scalars) all fall back to defaults.
  - Invalid JSON (`{,`) falls back.
  - Default-copy independence (mutating the result doesn't mutate `_DEFAULT_CONFIG`).
  - M22 atomic save: no `.tmp` left behind on success.
  - `save_config` creates parent dir.
  - Round-trip save→load.
  - Overwrite existing config preserves structure.
  - No sibling `.tmp` files left over from prior crashed saves.

## Tests
- `pytest tests/viewmodels/test_repo_tabs_viewmodel.py tests/core/test_repository.py tests/utils/test_config.py` → **70 passed**
- `ruff check <files>` → **All checks passed**

## Files changed
- M src/viewmodels/repo_tabs_viewmodel.py
- M src/core/repository.py
- M src/utils/config.py
- M src/ui/main_window.py
- M tests/viewmodels/test_repo_tabs_viewmodel.py (+2)
- M tests/core/test_repository.py (+2)
- + tests/utils/__init__.py
- + tests/utils/test_config.py (13 tests)

## Known issues / deferred
- Sub-agent mentioned the "full test sweep through other directories crashes inside a Qt extension (shiboken6) unrelated" — that refers to the **inter-test segfault** workaround we already use (run files individually), not an actual bug introduced here.
- 3 pre-existing failures in `tests/viewmodels/test_main_viewmodel_merge.py` — unchanged (baseline).

## Notes for review
The H10 corner-case pinning is the most important regression test added. The originally-suggested scenario (`active=2`, remove tab 0) accidentally exercised a code path where both old and new code happened to agree. The new `test_remove_tab_active_at_index_1_when_tab_at_0_removed` pins the actual failing case.
