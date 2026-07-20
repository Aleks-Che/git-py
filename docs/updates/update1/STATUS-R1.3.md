# STATUS — Stage R1.3
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** 4363743 (R1.2)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (C3 — Detached HEAD safety)

Salvaged after sub-agent hit max_iterations (45/45) before writing STATUS. Code + tests verified independently.

### Code changes (`src/core/operations.py`)
- `rebase_branch` — added explicit `head_is_unborn` + `head_is_detached` guards (the existing `merge_branch` check from R1.1 was already correct). New error: `GitError("Cannot rebase in detached HEAD state. Switch to a branch first.")`

### Code changes (`src/viewmodels/commands.py`)
- `RebaseCommand`, `CherryPickCommand`, `RevertCommand` — added `_previous_head_was_detached` tracking. undo path was already correct (verified: `reset(OID, mixed|hard)` preserves the detached-vs-symbolic state in pygit2); new attribute + extended docstrings make the contract explicit.

### Tests added
- `tests/core/test_operations.py` (+2):
  - `test_merge_branch_in_detached_head_leaves_repo_untouched`
  - `test_rebase_branch_raises_when_head_detached`
- `tests/viewmodels/test_merge_commands.py` (+2):
  - `test_cherry_pick_undo_preserves_detached_head`
  - `test_revert_undo_preserves_detached_head`

## Tests
- `pytest tests/core/test_operations.py` → **100 passed** (was 98, +2)
- `pytest tests/viewmodels/test_merge_commands.py` → **21 passed** (was 19, +2)
- `ruff check <files>` → **All checks passed**

## Files changed
- M src/core/operations.py
- M src/viewmodels/commands.py
- M tests/core/test_operations.py
- M tests/viewmodels/test_merge_commands.py

## Known issues / deferred
- pre-existing `tests/viewmodels/test_main_viewmodel_clipboard.py:39` segfault unrelated to R1.3 (clipboard API Qt/offscreen issue documented in IMPLEMENTATION_PLAN.md).

## Notes for review
`git cherry-pick` and `git revert` are allowed in detached HEAD by design — they DON'T require a branch. Only `merge_branch` and `rebase_branch` need the branch precondition. `CheckoutCommand` already handles detached HEAD correctly.
