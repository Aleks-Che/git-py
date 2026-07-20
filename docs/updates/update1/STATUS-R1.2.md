# STATUS — Stage R1.2
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** a7d1a83 (R1.1)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (C2 — Checkout with untracked files)

Salvaged after sub-agent hit max_iterations (50/50) before writing STATUS. Code + tests verified independently (pytest + ruff below).

### Code changes (`src/core/operations.py`)
- Module-level `_CONFLICTING_STATUS_FLAGS` mask (all `INDEX_*` + `WT_MODIFIED/DELETED/RENAMED/TYPECHANGE`); explicitly excludes `WT_NEW` (untracked) and `IGNORED`.
- `_dirty_paths` reimplemented: iterate `(path, flags)` keep only entries matching the mask.
- New `_capture_head_state(repo)` → `(was_unborn, symbolic, oid_hex)` snapshot — captures detached HEAD by OID, not refname.
- New `_rollback_head_state(repo, ...)` — best-effort restore of symbolic/detached HEAD + `index.read(force=True)` + `checkout_head(FORCE)` to re-sync worktree; catches `(KeyError, GitError, OSError)`.
- `checkout_branch`: both failure paths (checkout raise + post-verify dirty) use `_rollback_head_state`.
- `checkout_commit`: same treatment; detached-HEAD restore-by-OID now reachable.
- Old `_rollback_head(repo, previous_head)` kept intact — still used by `merge_branch`'s preflight.

### Tests added (`tests/core/test_operations.py`, +5)
- `test_checkout_safe_succeeds_with_untracked`
- `test_checkout_safe_succeeds_with_ignored`
- `test_checkout_safe_blocks_on_modified_tracked`
- `test_checkout_rolls_back_worktree_on_failure` (monkeypatch `Repository.checkout_head`)
- `test_dirty_paths_excludes_untracked_and_ignored` (direct unit test on the mask)

## Tests
- `pytest tests/core/test_operations.py` → **98 passed** (was 93, +5)
- `ruff check src/core/operations.py tests/core/test_operations.py` → **All checks passed**

## Files changed
- M src/core/operations.py
- M tests/core/test_operations.py

## Known issues / deferred
- None related to R1.2 scope.

## Notes for review
The mask definition: `GIT_STATUS_WT_NEW` and `GIT_STATUS_IGNORED` explicitly excluded. pygit2 only surfaces `GIT_STATUS_IGNORED` when `repo.status(ignored=True)` is requested — production call uses defaults, so IGNORED never appears in current `_dirty_paths` output today; the mask is defensive against future callers flipping that switch.
