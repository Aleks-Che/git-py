# STATUS — Stage R2.2
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** bbf1948 (R2.1)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (C6, C7, M8, M25 — Threads + async)

Salvaged after sub-agent hit max_iterations (80/80) before writing STATUS. Code + tests verified independently (pytest + ruff below).

### Code changes (`src/utils/async_worker.py`)
- `setAutoDelete(False)` in `__init__` — prevents teardown crash when queued signals reach an already-deleted runnable.
- New signal layout: `signals.finished(object)` carries the result; `signals.failed(object)` carries the **exception object** (not `str(exc)`); `signals.lifespan_finished()` is the no-payload lifecycle hook (clears `_is_busy` and drops `_active_workers` reference).
- All call sites in `main_viewmodel.py` migrated to the new `signals.finished`/`signals.failed`/`signals.lifespan_finished`.

### Code changes (`src/viewmodels/main_viewmodel.py`)
- **C7 — Generation token**: `self._async_generation: int = 0` in `__init__`. `set_repository()` bumps on every call (even refused), via new `force=` kwarg. `load_repository_data`, `clone_repository`, `_run_async` capture `generation = self._async_generation` at dispatch and drop stale results in their `_on_result`/`_on_failure` slots.
- **M8 — Busy-guard on `set_repository`**: refuses a *different* path while `_is_busy` is `True`, emits `error_occurred("Another operation is in progress — wait until it completes.")`. Bypassed via `force=True` (used by `clone_repository`'s success handler, by tests, and by auto-reconciles). Refuses to swap to a different repo when a worker is still pending.
- **M25 — `undo`/`redo` busy-guard**: both methods now `return` early with `error_occurred` if `self._is_busy`, protecting libgit2 state during in-flight push/pull/fetch/clone/rebase workers.
- **`_on_async_failed` routes on actual class** (`MergeConflictError`, `RebaseConflictError`) and falls back to `is_merge_in_progress`/`is_rebase_in_progress` only for non-domain failures.

### C6 — partial mitigation (deferred)
- `load_repository_data` already had the right pattern (worker creates its own `RepositoryManager(self._repo.path)`).
- `_run_async` still shares the UI-thread `RepositoryManager` — a true fix requires every `GitCommand` subclass to expose `set_repo()` (would touch `commands.py`, out of scope).
- Operational mitigation: the new M8 busy-guard on `set_repository` + existing busy-flags elsewhere prevent UI-thread concurrency while the worker is in flight. Documented in `_run_async` docstring.

### Tests added (`tests/viewmodels/test_main_viewmodel_async_r2_2.py`, new file, +5)
1. `test_set_repository_during_async_emits_error` — M8 guard.
2. `test_clone_overwritten_by_faster_set_repository_drops_result` — late-arriving clone success does not promote the wrong repo.
3. `test_async_result_from_stale_generation_is_ignored` — generation drop.
4. `test_undo_during_busy_rejected` — M25 (+ `redo` mirror).
5. `test_async_worker_failed_carries_exception` — `failed` signal carries the exception object.

All tests use real local bare repos / `pygit2.init_repository` + `committed_repo` / `origin_and_clone` fixtures — no mocked subprocesses.

## Tests
- `pytest tests/viewmodels/test_main_viewmodel_async_r2_2.py` → **5 passed** (all new)
- `pytest tests/viewmodels/test_main_viewmodel.py` → 3 passed (small file, all smoke).
- `ruff check src/viewmodels/main_viewmodel.py src/utils/async_worker.py` → **All checks passed**.

## Files changed
- M src/utils/async_worker.py
- M src/viewmodels/main_viewmodel.py
- + tests/viewmodels/test_main_viewmodel_async_r2_2.py

## Known issues / deferred
- **C6 root cause in `_run_async`** (worker shares UI-thread `pygit2.Repository`): documented; deferred to a future refactor where each `GitCommand` exposes a `set_repo()` swap.
- 1 pre-existing failure in `tests/viewmodels/test_main_viewmodel_remotes.py::test_push_changes_sync_path` was already failing on `bbf1948` before R2.2 changes (verified via `git stash` round-trip). Not introduced by this stage.

## Notes for review
The key design decision: keep `failed` payload as the **exception object** (not a string) so the slot can route on `MergeConflictError`/`RebaseConflictError` directly. The fallback to `is_merge_in_progress()` becomes a last-resort for non-domain failures (uncaught `OSError`, etc.) where the type is genuinely ambiguous.
