# STATUS — Stage R2.1
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** e921ad4 (R1.7)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (C5, M4 — error handling in hot paths)

Salvaged after sub-agent hit max_iterations (35/35) before writing STATUS. Code + tests verified independently (pytest + ruff below). Sub-agent self-fixed two transient issues mid-stage:
1. Tags test caused `RecursionError` (because `repo.references.delete()` itself uses `lookup_reference` which was monkeypatched). Fixed by using pre-bound `original_lookup(ref_name).delete()`.
2. Stray `patch` left `graph_viewmodel._compute_graph` with a broken docstring (missing `"""` opener). Restored next iteration.

### Code changes

`src/viewmodels/commit_panel_viewmodel.py`
- `refresh_status` (around line 269): try/except `(GitError, pygit2.GitError, OSError)`. Pre-check for missing `.git/index` file (raises `OSError` with a clear message) so the panel surfaces the breakage instead of silently showing an empty status.
- `_compute_staged_files` (around line 493): same wrapper, emits `error_occurred` and returns `set()`.
- `selected_file_supports_line_actions` revparse path: added `(GitError, OSError)` arm emitting `error_occurred` (defensive extra).

`src/viewmodels/graph_viewmodel.py`
- Imported `pygit2` and `collections.abc.Callable`.
- `refresh_graph` now passes `self.error_occurred.emit` to `_compute_graph` via a new `error_callback` parameter.
- `_compute_graph`: `repo.stash_list` (around line 129) wrapped in `try/except (GitError, pygit2.GitError, OSError)`. On failure routes message through callback, substitutes `stash_entries = []` so the rest of the history still renders.

`src/core/repository.py`
- `tags` property (around line 267): `lookup_reference` + `self.repo[ref.target]` wrapped in `try/except KeyError`; re-raised as `GitError(f"Cannot resolve tag {name!r}: …")`. Per DEVELOPMENT_RULES: core must never surface raw `KeyError`.

### Tests added (+3)

`tests/viewmodels/test_commit_panel_viewmodel.py`:
- `test_deleted_index_does_not_crash_panel_refresh` — unlink `.git/index`, refresh, assert `error_occurred` fired and no exception bubbled.

`tests/viewmodels/test_graph_viewmodel.py`:
- `test_graph_viewmodel_handles_stash_list_failure` — monkeypatch `stash_list` to raise `GitError`, assert error message routed.

`tests/core/test_repository.py`:
- `test_repository_tags_returns_git_error_for_missing_ref` — delete a tag, query, assert `GitError("Cannot resolve tag …")`.

## Tests
- `pytest tests/core/test_repository.py tests/viewmodels/test_commit_panel_viewmodel.py tests/viewmodels/test_graph_viewmodel.py` → **99 passed** (37+21+41)
- `ruff check <files>` → **All checks passed**

## Files changed
- M src/core/repository.py
- M src/viewmodels/commit_panel_viewmodel.py
- M src/viewmodels/graph_viewmodel.py
- M tests/core/test_repository.py
- M tests/viewmodels/test_commit_panel_viewmodel.py
- M tests/viewmodels/test_graph_viewmodel.py

## Known issues / deferred
- None related to R2.1 scope.

## Notes for review
Layering is consistent: core wraps `KeyError` → `GitError`; VMs catch `pygit2.GitError`/`OSError`/`GitError` and route to `error_occurred` signal (per DEVELOPMENT_RULES). Graph VM uses an `error_callback` parameter so `_compute_graph` remains a pure function — easy to unit-test.
