# STATUS — Stage R1.4
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** 6b8c0d3 (R1.3)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (C4, M3, M5 — Safe commit staging and first commit)

### Code changes (`src/core/operations.py`)
- `commit_changes(stage_all=True)` now builds an explicit pathspec from `repo.status()` using the tracked-change subset of `_CONFLICTING_STATUS_FLAGS`.
- Untracked (`WT_NEW`) and ignored entries are explicitly excluded, so unrelated worktree files cannot leak into a commit.
- Already staged `INDEX_NEW` entries remain in the index and can still be committed, while `stage_all` only refreshes tracked modifications, deletions, renames, and type changes.
- Status/index staging, index write, tree creation, parent lookup, commit creation, and result lookup now share one exception boundary that translates pygit2/key/value/type failures to domain `GitError("Commit failed: ...")`.
- Unborn HEAD is supported with `parents=[]`; existing repositories continue to use the current HEAD as the single parent.

### Code changes (`src/viewmodels/commands.py`)
- Updated `CommitCommand` documentation to state that unborn HEAD is a valid execute state and creates a parentless first commit. The command already records that state as `_previous_head = None` and calls the core operation with the explicitly managed index.

### Tests (`tests/core/test_operations.py`)
- Replaced the obsolete unborn-HEAD rejection test with a parentless empty-first-commit regression test.
- Added the three requested R1.4 regressions:
  - `test_commit_changes_does_not_stage_untracked_files`
  - `test_commit_changes_allows_first_commit_in_unborn_head`
  - `test_commit_changes_wraps_exceptions_in_git_error`
- Net test count: +3 (100 → 103 in this module).

## Tests
- `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/core/test_operations.py -q --tb=short` → **103 passed**
- `.venv/bin/python -m ruff check src/core/operations.py` → **All checks passed**
- Additional: `tests/viewmodels/test_commit_command.py` → **16 passed, 1 pre-existing pytest-qt application-class warning**
- Additional: Ruff over all three modified Python files → **All checks passed**
- `git diff --check` → **passed**

## Files changed
- M src/core/operations.py
- M src/viewmodels/commands.py
- M tests/core/test_operations.py
- ?? docs/updates/update1/STATUS-R1.4.md

## Known issues / deferred
- `CommitCommand.undo()` for a first commit remains a no-op by existing design; PLAN R1.6 explicitly defers replacing that behavior with a real unborn-HEAD rollback.

## Notes for review
- The existing `test_commit_changes_creates_a_new_head` creates only an untracked file and therefore now intentionally creates an empty follow-up commit; the new focused C4 test verifies a tracked modification is committed while the untracked file remains on disk, absent from the commit tree, and reported as `WT_NEW`.
- No commits, pushes, installs, branch changes, or out-of-scope file edits were performed.
