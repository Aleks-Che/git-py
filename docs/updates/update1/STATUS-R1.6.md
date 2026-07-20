# STATUS — Stage R1.6
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** 0c0b5bb (R1.5)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (H1, H2, H11 — CommandProcessor undo/redo + index refresh)

Sub-agent delivered all code + 2 of 4 target tests, then ran out of iterations before writing STATUS. Orchestrator salvaged:
- Fixed 1 test fixture bug (`test_command_processor_no_op_undo_does_not_enter_redo_stack` was using `tempfile.TemporaryDirectory` instead of the `tmp_git_repo` conftest fixture — yielded `RepositoryNotFoundError`). Switched to fixture.
- Added missing `Path` import to `test_command_processor_history.py`.
- Added the 2 missing index-refresh tests (`test_unstage_changes_refreshes_index_after_cli`, `test_stash_push_staged_refreshes_index_after_cli`) at the end of `tests/core/test_operations.py`.
- Fixed `stash_push_staged` invocation in test #2 — function does NOT accept `paths=` kwarg (it stashes all currently-staged changes).

### Code changes (`src/viewmodels/commands.py`)
- `is_noop = False` class attribute on `GitCommand` base (R1.6 §2).
- `CommandProcessor.undo/redo` now catch `Exception`, push the command back onto its source stack, emit `error_occurred` (new `Signal(str)`) so failed operations never silently disappear from history (H1).
- `CommandProcessor.execute` and `redo` skip pushing commands whose `is_noop` is `True`, so up-to-date merge / no-op-undo cases don't pollute the redo stack (H2).
- `CommitCommand.execute` sets `is_noop = head_is_unborn`. `MergeCommand.execute` sets it when `_merge_oid is None and not _head_moved`. `CheckoutCommand.execute` sets it when `_previous_branch is None` (unborn HEAD).

### Code changes (`src/core/operations.py`)
- New `_refresh_index(repo)` helper next to `_run_git_in_workdir`.
- `_run_git_in_workdir` calls `_refresh_index` after every successful subprocess run, accepts optional `env=` kwarg (H11).
- `rebase_branch`, `unstage_changes`, `stash_push_staged`, `complete_rebase_continue`, `discard_file` rewritten to use `_run_git_in_workdir`; bare-repo and CLI-missing checks folded into call site so `workdir` is always in scope.
- `restore_stash` got explicit `_refresh_index` call.

### Tests added
- `tests/viewmodels/test_command_processor_history.py` (+2; salvaged fixture fix):
  - `test_command_processor_undo_keeps_command_on_exception`
  - `test_command_processor_no_op_undo_does_not_enter_redo_stack` (uses `tmp_git_repo` fixture now)
- `tests/core/test_operations.py` (+2; salvaged by orchestrator):
  - `test_unstage_changes_refreshes_index_after_cli`
  - `test_stash_push_staged_refreshes_index_after_cli`

## Tests
- `pytest tests/viewmodels/test_command_processor_history.py` → **10 passed** (was 8 pre-stage + 2 new)
- `pytest tests/core/test_operations.py` → **105 passed** (was 103 + 2)
- `ruff check <files>` → **All checks passed**

## Files changed
- M src/core/operations.py
- M src/viewmodels/commands.py
- M tests/viewmodels/test_command_processor_history.py (fixture fix + Path import)
- M tests/core/test_operations.py (2 salvaged tests appended)

## Known issues / deferred
- Sub-agent delivered STATUS-less. Salvaged.

## Notes for review
`is_noop` semantics: set during `execute`, consulted by `execute`/`redo` *before* the redo push. The undo path doesn't need a guard because no-op commands never reach the undo stack in the first place.
