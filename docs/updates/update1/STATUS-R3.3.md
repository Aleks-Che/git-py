# STATUS — R3.3: Windows specifics (subprocess encoding, terminal, pull)
**Branch:** update1
**HEAD before:** 9f46eb7b8638b1691bd39798c685ed05a7a29102
**HEAD after:** <not committed>

## What landed
- src/core/operations.py: subprocess.run + subprocess.Popen — encoding="utf-8", errors="replace" (2 sites)
- src/core/operations.py: pull() raises GitError on no upstream
- src/ui/widgets/terminal_widget.py: output decode fallback to mbcs/utf-8, input encode with filesystem encoding, chcp 65001 on Windows shell spawn
- src/ui/widgets/terminal_widget.py: exited shell clears `_process` so the terminal can restart
- tests/core/test_r3_3_subprocess_encoding.py: NEW
- tests/core/test_r3_3_pull_no_upstream.py: NEW
- tests/ui/test_r3_3_terminal_encoding.py: NEW

## Gates
- pytest test_r3_3_subprocess_encoding → 1 passed
- pytest test_r3_3_pull_no_upstream → 1 passed
- pytest test_r3_3_terminal_encoding → 2 passed
- pytest test_operations → 105 passed, 0 failed (no regression)
- pytest test_repository → 43 passed, 0 failed (no regression)
- pytest test_terminal_widget → 13 passed, 0 failed (no regression)
- ruff check R3.3 files → 0
- ruff check src tests → blocked by pre-existing `tests/viewmodels/test_r3_2.py:250` W292 (out of R3.3 scope)

## Files changed
- M src/core/operations.py
- M src/ui/widgets/terminal_widget.py
- A tests/core/test_r3_3_subprocess_encoding.py
- A tests/core/test_r3_3_pull_no_upstream.py
- A tests/ui/test_r3_3_terminal_encoding.py
- A docs/updates/update1/STATUS-R3.3.md

## Notes for reviewer
- subprocess encoding: applied to 2 sites (all `subprocess.run`/`subprocess.Popen` sites in operations.py; there are no Popen sites)
- terminal decode: cp866 / utf-8 fallback verified by unit test
- pull error: GitError replaces silent False and upstream is validated before attempting fetch
- full ruff gate is blocked only by an unrelated pre-existing untracked R3.2 test file missing a trailing newline; it was not modified because R3.2 is outside this dispatch
- the worktree already contained R3.1/R3.2 changes before this task; no files outside explicit R3.3 scope were modified by this dispatch
