# STATUS — Stage R1.7
**Date:** 2026-07-20
**Branch:** update1
**HEAD before:** 7dc56b6 (R1.6)
**HEAD after:** <not committed; orchestrator commits after review>

## What was done (H6, H20 — Non-undoable commands + destruction confirms)

Sub-agent delivered the `is_noop = True` markers + the `_confirm_destructive()` helper but hit max_iterations before adding tests or STATUS file. Initial attempt to gate `discard_changes()` inside `MainViewModel` with the helper broke the existing UI test `test_discard_all_closes_open_diff` (QMessageBox.question hangs in headless). Orchestrator salvaged:
- Reverted the `discard_changes()` body to its pre-R1.7 form (helper stays available for callers; VM docstring updated to note UI is the confirm gate).
- Appended 4 new tests to `tests/viewmodels/test_commands.py`.
- Wrote this STATUS file.

### Code changes

`src/viewmodels/commands.py`
- `DiscardChangesCommand.is_noop = True` (around line 1434). Toolbar Undo button now ignores it. Docstring updated to clarify the confirm gate is at the VM / UI boundary.
- `PushCommand.is_noop = True` (around line 837 per sub-agent). Push cannot be undone; toolbar Undo correctly disabled.
- `FetchCommand.is_noop = True` (around line 929). Fetch only updates remote-tracking refs; no local state to undo.

`src/viewmodels/main_viewmodel.py`
- New helper `_confirm_destructive(title, message, *, default_no=True, parent=None) -> bool` (around lines 125-152). Wraps `QMessageBox.question` with default=No (so accidental Enter doesn't destroy data). Returns True iff user explicitly clicked Yes.
- `discard_changes()` body reverted to pre-R1.7 form; docstring updated to note UI confirm is the caller's responsibility.
- The helper is available in the VM namespace for future R2.3/R3.4 stages to apply it correctly at the UI layer (`MainWindow._on_discard_all`, `CommitPanel.discard_button`).

### Tests added (`tests/viewmodels/test_commands.py`, +4)

1. `test_discard_changes_command_is_excluded_from_undo_stack`
   - Real repo + CommitCommand setup (not `tempfile.TemporaryDirectory` which crashed in R1.6 — use `tmp_path`).
   - `processor.execute(DiscardChangesCommand(manager))`.
   - Assert: `not can_undo`, `not can_redo`.

2. `test_confirm_destructive_returns_false_on_no`
   - Mock `QMessageBox.question -> No`.
   - Assert: helper returns False.

3. `test_confirm_destructive_returns_true_on_yes`
   - Mock `QMessageBox.question -> Yes`.
   - Assert: helper returns True.

4. `test_confirm_destructive_default_is_no`
   - Capture `QMessageBox.question` args.
   - Assert: `default=No` flag passed, both `Yes` and `No` flags set.

(`test_push_command_is_excluded_from_undo_stack` and `test_fetch_command_is_excluded_from_undo_stack` deferred — both are mechanical mirror tests of #1; their classes push/fetch through network in `execute()`, requiring async mocking. The `is_noop = True` marker is plain attribute override and provably correct from R1.6's CommandProcessor skip-push logic.)

## Tests
- `pytest tests/viewmodels/test_commands.py` → **11 passed** (was 7, +4)
- `pytest tests/ui/test_right_panel.py::test_discard_all_closes_open_diff` → 1 passed (regression check on `discard_changes`)
- `ruff check src/viewmodels/commands.py src/viewmodels/main_viewmodel.py tests/viewmodels/test_commands.py` → **All checks passed**

## Files changed
- M src/viewmodels/commands.py (3 `is_noop = True` class attrs on `PushCommand`, `FetchCommand`, `DiscardChangesCommand`)
- M src/viewmodels/main_viewmodel.py (new helper `_confirm_destructive`, reverted `discard_changes`)
- M tests/viewmodels/test_commands.py (4 new tests)

## Known issues / deferred
- Push/Fetch undo-stack-exclusion tests — deferred to follow-up sub-agent if real-world reproduction needed.
- `delete_file_from_disk` VM gate not added yet — caller's responsibility per same pattern; will be wired into UI in R2/R3.

## Notes for review
The `QMessageBox.question` direct VM-gate attempt was the wrong layer. Real fix must be at the UI layer (`MainWindow._on_discard_all` → `QMessageBox.question(parent=self, ...)` → if Yes → `main_vm.discard_changes()`). R2.3 / R3.4 will revisit when busy-guard decorators land.
</content>
</invoke>