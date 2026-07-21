# STATUS — Stage R2.6
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** 852206b (R2.5)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (H13, H14, H15, H16, M13, M14, M23 — Qt lifecycle)

Three-agent execution with orchestrator salvage. Agent 1: H13/H14/H15 + exploration. Agent 2: H16 only. Agent 3 (orchestrator): M13, M14, M23, 4 tests, STATUS, ruff cleanup.

### Code changes

**`src/ui/widgets/graph_panel.py`** (H13/H14 — agent 1)
- **H13**: `BranchStackPopup.__init__` sets `self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)` (the flag was previously `False`; toggle to True means Qt destroys the popup after close).
- **H14**: top of `paintEvent` now calls `self._branch_chip_rects.clear()` (Option A — invalidate cache, simpler than arith hit-test).

**`src/ui/widgets/left_panel.py`** (H15 — agent 1)
- **H15**: drop menu construction now uses `menu.exec(self.viewport().mapToGlobal(event.position().toPoint()))` (was `menu.exec(event.position().toPoint())` which is in widget-local coords, not global screen).

**`src/ui/widgets/repo_bar_widget.py`** (H16 — agent 2)
- **`_CloseTabButton.__init__`**: dropped `tab_index` parameter; `clicked_signal` is now `Signal(QPoint)` carrying the click position in button-local coordinates.
- **`mousePressEvent`**: emits `event.pos()`.
- **`eventFilter` (Enter key branch)**: no longer reads `obj._tab_index`; resolves via `self._tab_bar.tabAt(obj.mapTo(self._tab_bar, QPoint(0, 0)))`.
- **`_install_close_button`**: no longer passes index into the button constructor.
- **`_on_tab_close_requested(self, local_pos: QPoint)`**: bounds-checks via `self._tab_bar.tabAt(local_pos)` (≥0 and < count).

**`src/ui/main_window.py`** (M13/M14 — orchestrator)
- **M13 `_on_busy_changed(False)`**: added an `else` branch that re-evaluates undo/redo/close action enabledness — `command_processor().can_undo/can_redo` and `repository_manager() is not None`. Without this, actions stayed disabled after a busy operation.
- **M14 `_open_remote_manage_dialog`**: assigned `on_add`/`on_remove`/`on_stack` to **named** `def` callbacks (was anonymous lambda) so they can be disconnected on dialog close. Added a `dialog.finished.connect(_cleanup)` hook that disconnects all three callbacks and calls `dialog.deleteLater()`. Without this, a second dialog instance received stale slot calls from the first.

**`src/ui/widgets/diff_view_widget.py`** (M23 — orchestrator)
- **`_do_scroll` (singleShot 0 callback)**: re-derives the cursor inside the callback (`doc.findBlockByLineNumber(target_idx)` and `QTextCursor(block_now)`) instead of capturing the cursor in the closure. The document may have changed between scheduling and firing, making the captured cursor stale.

### Tests added (`tests/ui/test_qt_lifecycle_r2_6.py`, new file, +4)

1. `test_branch_popup_uses_wa_delete_on_close_true` — grep inside the `BranchStackPopup` class body for `setAttribute(...WA_DeleteOnClose, True)`. Accepts both `Qt.WA_DeleteOnClose` and `Qt.WidgetAttribute.WA_DeleteOnClose` flag styles.
2. `test_left_panel_drop_uses_exec_with_viewport` — confirms `QMenu.exec(viewport().mapToGlobal(...))` is present and raw `.popup(` calls are absent (excluding `setPopupMode`).
3. `test_repo_bar_widget_no_saved_tab_index_attribute` — confirms `_tab_index` substring is NOT in `repo_bar_widget.py` at all (would indicate a stale cached index).
4. `test_diff_view_widget_do_scroll_rederives_cursor_per_tick` — confirms `_do_scroll` body contains `findBlockByLineNumber(...)` (re-derivation marker), not just a captured cursor set in the enclosing scope.

These are contract-level grep/regex assertions (no live QMenu.exec spy), to avoid headless-Qt segfaults in tests.

## Tests
- `pytest tests/ui/test_qt_lifecycle_r2_6.py` → **4 passed**
- `ruff check <files>` → **All checks passed**

## Files changed
- M src/ui/widgets/graph_panel.py (H13/H14)
- M src/ui/widgets/left_panel.py (H15)
- M src/ui/widgets/repo_bar_widget.py (H16)
- M src/ui/main_window.py (M13/M14)
- M src/ui/widgets/diff_view_widget.py (M23)
- + tests/ui/test_qt_lifecycle_r2_6.py (new file)

## Known issues / deferred
- **Three agents** were needed to finish R2.6 (45-iter cap each). Salvage pattern from `multi-stage-subagent-orchestration` skill applied cleanly.
- 3 pre-existing failures in `tests/viewmodels/test_main_viewmodel_merge.py` (baseline) — unchanged.
- Qt-headless-test segfaults (workaround: run files individually).

## Notes for review
The H14 cache-clear is Option A from the plan (simpler than arith hit-test). If performance becomes an issue on repositories with thousands of rows, the arith approach (compute hit-test from `scroll_y // row_height` + cached offsets) can replace it without changing the public API. Both `_branch_chip_rects` and `_draw_branch_chips` would be cleaned up.
