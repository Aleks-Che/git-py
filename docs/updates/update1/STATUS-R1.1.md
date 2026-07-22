# STATUS — Stage R1.1
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** 914632f
**HEAD after:** <not committed; orchestrator will commit after review>

## What was done
Salvaged after sub-agent hit max_iterations (60/60) before writing the STATUS file. Code work verified independently via pytest + ruff (results below). Markdown-only recovery.

### Code changes (verified on disk)
- `src/core/operations.py` (`merge_branch` ~lines 362-470 + new helper `_restore_head_reference` near line 256):
  - Resolve `target` early; raise `GitError` if HEAD is detached (`"merge_branch requires a target branch (HEAD is detached)"`).
  - Resolve and validate the target ref → `GitError("Unknown target branch: …")` for missing target (kills the silent no-op for `target="nonexistent"`).
  - When `current_branch != target_name`: checkout the target ref with `GIT_CHECKOUT_SAFE` + `index.read(force=True)` before `merge_analysis` (avoids phantom uncommitted changes from in-memory stale index — first attempt failed because `set_head+checkout_head` alone left the prior branch's index in memory).
  - FF block wrapped in preflight `checkout_tree(r[source_oid].tree, SAFE)`. If it raises, ref + HEAD are untouched. If post-move `r.checkout()` fails, both `ref.set_target(previous_oid)` and HEAD (symbolic or detached) are restored — this is H12.
- `src/viewmodels/commands.py` (MergeCommand ~lines 524-645):
  - `__init__` captures `_target_ref_name`, `_target_sha_before`, `_head_ref_name_before`, `_head_sha_before`, `_merge_oid` (added to existing `_head_moved`).
  - `execute` resolves target before calling `core.merge_branch`, looks up the target ref (raises `GitError` on miss), snapshots HEAD refname + SHA, records `_merge_oid = new_target_sha` when target moved.
  - `undo` restores target ref to `_target_sha_before`, and (when target ≠ HEAD before) re-points HEAD to `_head_ref_name_before` / `_head_sha_before` and `checkout_head(FORCE)`. Up-to-date merges still no-op. Conflicts still propagate.

### Tests added
- `tests/core/test_operations.py` (+4):
  - `test_merge_x_into_y_when_head_on_z` — HEAD on Z, merge X into Y → HEAD ends on Y, Y has two-parent merge commit `[Y_before, X_tip]`, Z unchanged.
  - `test_merge_x_into_y_with_detached_head_raises`.
  - `test_ff_merge_rolls_back_target_on_checkout_failure` — local edit on tracked file → `GitError("Fast-forward merge failed")`, target ref + HEAD restored.
  - `test_ff_merge_no_op_when_target_not_local_branch` — `target="nonexistent"` raises `GitError("Unknown target branch…")`.
- `tests/viewmodels/test_merge_commands.py` (+2):
  - `test_merge_command_captures_target_state_for_undo` — `MergeCommand(repo, "x", target="y")` while HEAD on Z; undo leaves HEAD on Z, Y back to pre-merge tip.
  - `test_merge_command_no_ff_captures_merge_oid_for_undo` — asserts `cmd._merge_oid == merge commit SHA`, undo returns to base.

### `viewmodels/main_viewmodel.py:1603-1637`
Not modified — sub-agent verified `merge_branch(source, target=None, *, no_ff=False)` already passes `target=target` to `MergeCommand`; signature is unchanged.

## Tests
- pytest tests/core/test_operations.py → **93 passed** (was 89, +4)
- pytest tests/viewmodels/test_merge_commands.py → **19 passed** (was 17, +2)
- ruff check src/core/operations.py src/viewmodels/commands.py src/viewmodels/main_viewmodel.py → **All checks passed**

## Files changed
- M src/core/operations.py
- M src/viewmodels/commands.py
- M tests/core/test_operations.py
- M tests/viewmodels/test_merge_commands.py

## Known issues / deferred
- None related to R1.1 scope.

## Notes for reviewer
The trickiest part was the FF preflight `checkout_tree(source_tree, SAFE)`. The original code did `ref.set_target + r.checkout(refname)` AFTER moving the ref; if the post-move SAFE checkout failed, the ref was already moved → H12 rollback path triggered but as the primary defence, not a safety net. New code makes the SAFE-checkout the primary defence (ref doesn't move if worktree would be dirty) — `except` rollback remains as belt-and-suspenders for the actual `r.checkout` call.
