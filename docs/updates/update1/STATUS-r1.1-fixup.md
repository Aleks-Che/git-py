# STATUS — r1.1-fixup: merge mid-state (variant a) + outdated contracts cascade
**Branch:** update1
**HEAD before:** 4dddc15
**HEAD after:** not committed

## What landed
- `src/core/operations.py`: real libgit2 merge path retained; successful merge now calls `checkout_head(SAFE | RECREATE_MISSING)` to refresh the worktree. Conflict path restores symbolic HEAD to the starting branch without checking out, preserving merge conflict state and `MERGE_HEAD` for resolution.
- `tests/core/test_r1_1_merge_mid_state.py`: 11 regression tests for variant (a) — successful merges switch HEAD to target, refresh worktree to merge tree, advance target ref, clear MERGE_HEAD, no phantom INDEX_DELETED. Conflicts preserve starting branch + conflict state.
- `tests/core/test_operations.py` cascade — 105 passed (was 8 failed under variant b).
- Hardcoded path fixes (VERIFICATION §5.1): `tests/viewmodels/test_main_viewmodel_r2_3.py`, `tests/ui/test_qt_lifecycle_r2_6.py`.
- R1.4 setup fixes (VERIFICATION §5.2): `tests/viewmodels/test_main_viewmodel_merge.py` — explicit `repo.index.add()` for untracked files in 4 tests.
- R1.6 contract (VERIFICATION §5.3): conflict stays in undo stack across `tests/core/test_merge_commands.py`, `tests/viewmodels/test_main_viewmodel_merge.py`, `tests/viewmodels/test_merge_commands.py`.
- R1.7 contract (VERIFICATION §5.3): push/fetch are `is_noop`, pull conflict stays in undo — `tests/core/test_remote_commands.py`, `tests/viewmodels/test_remote_commands.py`, `tests/viewmodels/test_main_viewmodel_remotes.py`.
- Docstring sync in `tests/viewmodels/test_main_viewmodel_merge.py:1-12`.
- Popup test fix (VERIFICATION §5.4): `tests/ui/test_graph_widget.py` — uses `findChildren(BranchStackPopup)` instead of holding Python ref.

## Variant decision
Pivoted from in-memory variant (b) to variant (a) (VERIFICATION §3 option a). Variant (b) "merge in memory" broke the production `MERGE_HEAD → resolve_conflict → complete_merge` workflow (no MERGE_HEAD written) and 8 cascade tests in `test_operations.py`. Variant (a) keeps the real libgit2 merge path but adds `checkout_head(SAFE | RECREATE_MISSING)` after success to eliminate phantom INDEX_DELETED entries.

## Gates (final, file-by-file, no batch — Qt segfault workaround)
| File | Result |
|---|---|
| `tests/core/test_r1_1_merge_mid_state.py` | **11 passed** |
| `tests/core/test_operations.py` | **105 passed** (was 8 failed) |
| `tests/viewmodels/test_merge_commands.py` | **21 passed** |
| `tests/viewmodels/test_remote_commands.py` | **20 passed** |
| `tests/viewmodels/test_main_viewmodel_merge.py` | **26 passed** (was 5 failed) |
| `tests/viewmodels/test_main_viewmodel_remotes.py` | **35 passed** (was 1 failed) |
| `tests/viewmodels/test_main_viewmodel_r2_3.py` | **5 passed** |
| `tests/ui/test_qt_lifecycle_r2_6.py` | **4 passed** |
| `tests/ui/test_graph_widget.py` | **97 passed** (was 1 failed) |
| **TOTAL** | **324 passed, 0 failed** |

(`tests/core/test_merge_commands.py` and `tests/core/test_remote_commands.py` segfault in batch but pass individually — pre-existing Qt+offscreen issue, not addressed in this cascade.)

- `ruff check src/ tests/`: **exit 0** (8 F841/W292 fixed in `test_r1_1_merge_mid_state.py` + unused imports/vars in `test_graph_v2_r3_1.py`)

## Files changed
- M `src/core/operations.py` — variant (a) merge_branch with post-merge checkout_head
- M `src/ui/main_window.py` — already fixed in 4dddc15 (commit pre-pull)
- A `tests/core/test_r1_1_merge_mid_state.py` — 11 regression tests
- M `tests/core/test_operations.py` — (verifies cascade still green after variant a)
- M `tests/core/test_merge_commands.py` — R1.6 contract
- M `tests/core/test_remote_commands.py` — R1.7 contract
- M `tests/viewmodels/test_merge_commands.py` — captures-state variant (a) assertions
- M `tests/viewmodels/test_remote_commands.py` — R1.7 contract
- M `tests/viewmodels/test_main_viewmodel_merge.py` — R1.4 setups + R1.6 contract + docstring sync
- M `tests/viewmodels/test_main_viewmodel_remotes.py` — push is_noop assertion
- M `tests/viewmodels/test_main_viewmodel_r2_3.py` — hardcoded path → `__file__`-relative
- M `tests/ui/test_qt_lifecycle_r2_6.py` — 4 hardcoded paths fixed
- M `tests/ui/test_graph_widget.py` — popup test uses findChildren
- M `tests/core/test_graph_v2_r3_1.py` — ruff cleanup (unused imports/vars)
- A `docs/updates/update1/STATUS-r1.1-fixup.md` (this file)
- A `docs/updates/update1/VERIFICATION.md` (already present before cascade — verification report that triggered this fixup)

## Notes for reviewer
- R1.1 mid-state root cause confirmed: old `merge_branch` did `checkout(target)` BEFORE merge but never called `checkout_head` AFTER merge, leaving workdir in mid-merge state with phantom INDEX_DELETED entries. Variant (a) fix is minimal: keep real merge, add `checkout_head(SAFE | RECREATE_MISSING)` post-success.
- Conflict path now restores HEAD to user's starting branch (better UX than stranding on conflict target).
- All 17 previously-failing tests from VERIFICATION §5 are now green.
- R3 (perf) and R4 (cleanup) still NOT done — separate stage per VERIFICATION §6.
- AP9 lesson: previous sub-agent rewrote core/operations.py but didn't run full pytest file-by-file, breaking 8 cascade tests in test_operations.py. Verified fix here.
