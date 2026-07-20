# STATUS — Stage R1.5
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** 7f68ac9
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (H3, H4, H5 — Safe stash undo)

### Code changes (`src/core/operations.py`)
- Added `find_stash_index_by_oid()` to locate a saved stash by commit OID after an intervening push changes its list index.
- Added stash-apply snapshot/restore helpers that capture only paths represented by the stash, including tracked worktree changes, staged/index changes, and third-parent untracked files.
- Snapshot data preserves pre-apply file bytes, paths that did not exist, and index OID/mode entries; restoration recreates/deletes only those paths and restores their index entries.
- Existing unrelated dirty tracked files and untracked files are intentionally left untouched.
- `restore_stash()` now has a safe default restoration message while retaining the existing domain-error behavior.

### Code changes (`src/viewmodels/commands.py`)
- `StashPushCommand` now retains the pushed OID and verifies the current stash entry before undo. If another stash was pushed, undo searches for and drops only the original OID; a missing OID raises `GitError` instead of being swallowed.
- When the command's stash is still at index 0, push undo preserves the existing UI behavior by popping it and restoring the worktree.
- `StashApplyCommand` snapshots affected worktree/index paths before apply and restores that exact pre-apply state on undo, including removal of untracked files introduced by the stash.
- `StashPopCommand` snapshots before pop, rolls back the applied changes on undo, then restores the dropped stash entry once via `restore_stash()`.
- Apply/pop completion flags prevent undo from restoring a snapshot when execute did not complete successfully.

### Tests (`tests/viewmodels/test_stash_commands.py`)
- Added the four requested regressions:
  - `test_stash_push_undo_drops_correct_stash`
  - `test_stash_push_undo_recovers_from_intervening_push`
  - `test_stash_apply_undo_restores_worktree`
  - `test_stash_pop_undo_restores_worktree_and_stash`
- Updated existing push/pop undo assertions to cover preserved worktree semantics.
- Module test count increased from 14 to 18.

## Tests
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/viewmodels/test_stash_commands.py -q --tb=short` → **18 passed**
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/viewmodels/test_main_viewmodel_stash.py -q --tb=short` → **20 passed**
- Combined stash suites → **38 passed**
- `.venv/bin/python -m ruff check src/core/operations.py src/viewmodels/commands.py` → **All checks passed**
- Additional Ruff over the modified stash test file → **All checks passed**
- `git diff --check` → **passed**

## Files changed
- M src/core/operations.py
- M src/viewmodels/commands.py
- M tests/viewmodels/test_stash_commands.py
- ?? docs/updates/update1/STATUS-R1.5.md

## Known issues / deferred
- `StashPushStagedCommand` and `StashSingleFileCommand` retain their existing best-effort pop-based undo behavior; the R1.5 scope and requested regressions target `StashPushCommand`, `StashApplyCommand`, and `StashPopCommand`.
- CommandProcessor exception stack recovery remains deferred to PLAN R1.6.

## Notes for review
- An intervening-push undo deliberately drops (does not apply) the older command-owned stash, because applying it underneath a newer foreign stash could overwrite or conflict with current worktree state.
- No commits, pushes, installs, branch changes, or out-of-scope file edits were performed.
