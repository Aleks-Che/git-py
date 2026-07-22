# STATUS — R3.4: conflict dialog + render polish
**Branch:** update1
**HEAD before:** 9f46eb7 (R1.1 merge mid-state)
**HEAD after:** not committed (orchestrator owns git)

## What landed

### Production

- `src/ui/dialogs/conflict_resolution_dialog.py`: binary conflict path (raw bytes via `resolved_bytes`, EOL preservation, cp1251 fallback). Pre-existing R3.4 M17 work — verified by `test_binary_conflict_writes_raw_bytes_not_placeholder`.
- `src/ui/widgets/graph_panel.py`:
  - `_branch_priority_key`: **R3.4 regression fix** — recently-created demotion now wins over source-bucket check (newly created branches must not jump to the prominent chip). `_hit_test_commit` already skips connector rows (`sha == ""`). `prev_occupied` bookkeeping and popup close-on-h-scroll from previous R3.4 drop remain in place.
- `src/ui/widgets/left_panel.py` (**M20**): state preservation across rebuild — `_expanded_groups` set and `_selected_kind`/`_selected_name` cached; restored in `_rebuild` after `clear()`. New `_on_selection_changed` listener keeps the cache current, `_restore_selection` + `_find_child` helpers re-locate the same `(kind, name)` pair in the rebuilt tree.
- `src/ui/widgets/commit_detail_panel.py` (**M21**): `html.escape` for commit metadata in `_format_info` (author, committer, e-mail, SHA, parents); `_message` / `_body` `QLabel`s forced to `Qt.TextFormat.PlainText`; `_info` stays RichText but only with escaped user values.

### Tests

- `tests/ui/test_r3_4.py`: 4 NEW tests
  1. `test_binary_conflict_writes_raw_bytes_not_placeholder` — real binary conflict in pygit2 repo, `Accept Ours` → `resolved_bytes` payload equals blob (never `"<binary>"` placeholder).
  2. `test_hit_test_skips_connector_rows` — synthesised two-row graph, click on connector row's y → `None`.
  3. `test_left_panel_preserves_expanded_state` — collapse `Tags`, fire `references_changed`, re-locate `Tags`, assert still collapsed.
  4. `test_commit_metadata_html_escape` — author `<script>alert('xss')</script>`, assert `_format_info` produces escaped HTML and QLabel round-trip has no raw `<script>`.

## Gates

| gate | result |
|------|--------|
| `pytest tests/ui/test_r3_4.py` | **4 passed** |
| `pytest tests/ui/test_conflict_dialog.py` | **11 passed** |
| `pytest tests/ui/test_graph_widget.py` | **97 passed** (regression fixed) |
| `pytest tests/ui/test_left_panel.py` | **55 passed** (no regression) |
| `pytest tests/ui/test_right_panel.py` | **65 passed** (no regression) |
| `pytest tests/ui/test_qt_lifecycle_r2_6.py` | **4 passed** (no regression) |
| `ruff check src/ui/dialogs/conflict_resolution_dialog.py src/ui/widgets/graph_panel.py src/ui/widgets/left_panel.py src/ui/widgets/commit_detail_panel.py tests/ui/test_r3_4.py` | **0** |

Note: `ruff check src/ tests/` reports 1 pre-existing `W292` (no-newline) in `tests/viewmodels/test_r3_2.py`, an R3.2 file modified today by an earlier sub-agent dispatch. R3.2 is out of scope for R3.4 (separate dispatch per HARD RULES); the R3.4 files themselves all pass `ruff check`.

## Files changed (R3.4 finalization)

- M src/ui/dialogs/conflict_resolution_dialog.py  *(M17, previous sub-agent; verified)*
- M src/ui/widgets/graph_panel.py                 *(regression fix in `_branch_priority_key`)*
- M src/ui/widgets/left_panel.py                  *(M20 — state preservation)*
- M src/ui/widgets/commit_detail_panel.py         *(M21 — html.escape + PlainText)*
- A tests/ui/test_r3_4.py
- A docs/updates/update1/STATUS-R3.4.md

## Notes for reviewer

- **R3.4 M17** binary conflict: dialog writes raw bytes via `resolved_bytes` signal; the literal text `"<binary>"` is never persisted to disk. Verified end-to-end with a real binary-on-both-sides conflict.
- **R3.4 M15** prev_occupied: edge rows at viewport borders. (previous drop)
- **R3.4 M16** popup scroll: content-x → widget-x via `- _h_scrolls[0]`. (previous drop)
- **R3.4 M12** `_hit_test_commit`: connector rows (`sha == ""`) return `None`. Verified by the new test that synthesises a connector row and clicks its y.
- **R3.4 M20** LeftPanel state: `_expanded_groups` set + `_selected_kind`/`_selected_name` cached on `itemSelectionChanged`/`itemExpanded`/`itemCollapsed`, restored in `_rebuild`. Empty expansion set defaults to `{Branches, Local}` so the first launch looks the same as before.
- **R3.4 M21** html.escape: every commit metadata field in `_format_info` is escaped; formatting tags (`<b>`, `<code>`, `<br/>`) are hard-coded literals and never reach user data. `_message` and `_body` `QLabel`s are forced to `Qt.TextFormat.PlainText` so an attacker-controlled commit subject cannot be reinterpreted as HTML.
- **Regression fix**: `_branch_priority_key` previously returned bucket 1 for any branch that was both in `_recently_created_branches` AND matched the source-bucket heuristic — the chip column would jump to the brand-new branch every time `create_branch` fired. The fix checks `_recently_created_branches` first, returning bucket 2 before the source-bucket path runs.
