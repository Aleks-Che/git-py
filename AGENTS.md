# AGENTS.md

## Project status
- Stage 1 done. Core layer (`RepositoryManager`, `core/operations.py`, `core/diff_parser.py`, domain exceptions, `FileChange` dataclass) is implemented and covered by 66 unit tests. 2 / 11 stages complete.
- Progress is tracked in `docs/IMPLEMENTATION_PLAN.md`. Update the stage checklist there when starting/finishing a stage.

## Authoritative design docs (all in Russian)
Read these before making structural decisions — they are the source of truth:
- `docs/ARCHITECTURE.md` — layers, modules, MVVM + Command pattern.
- `docs/DEVELOPMENT_RULES.md` — hard constraints (see below).
- `docs/IMPLEMENTATION_PLAN.md` — staged roadmap and current status.
- `docs/TEST_PLAN.md` — test layout and scenarios.

## Stack
- Python 3.10+, PySide6 (Qt GUI), `pygit2` for Git, `GitPython` only as fallback.
- Tests: `pytest`, `pytest-qt`, `pytest-asyncio`.
- Architecture: MVVM + Command pattern (undo/redo via `CommandProcessor`).

## Hard rules (from `docs/DEVELOPMENT_RULES.md`)
- `core/` MUST NOT import PySide6. Core is pure Python + pygit2 only.
- ViewModels use Qt signals/slots but must not know about specific widgets.
- Widgets are passive: read ViewModel state, call methods, never store Git operation state.
- Every mutating Git operation (commit, merge, rebase, branch create, checkout, stash, push, pull, fetch) MUST be a subclass of `GitCommand` and routed through `CommandProcessor`. Toolbar Undo/Redo binds to `CommandProcessor`, not to operations directly.
- Wrap `pygit2` exceptions in `core/` into domain exceptions (`GitError`, `MergeConflictError`, `AuthError`, …). ViewModels surface errors via the `error_occurred(str)` signal, never raw Python exceptions.
- Network or long-running ops (push/pull/fetch/clone/rebase, large merges) MUST run in `QThread`/`QRunnable` with a spinner and re-entrancy guard.
- ViewModel command methods are verbs: `commit_changes()`, `checkout_branch(name)`, `merge_branch(source, target)`.
- Widget classes use the suffix `Widget`, `Panel`, or `Dialog`.
- Paths, hotkeys, theme parameters, and panel layout live in JSON/YAML configs loaded via `utils/config.py` (persisted on exit, restored on launch).
- Follow PEP 8.

## Test layout
- `tests/core/` — unit tests. Use `tempfile.TemporaryDirectory` + `pygit2.init_repository` for fixture repos; mock or use a local bare repo for clone/push/pull tests.
- `tests/viewmodels/` — ViewModel tests, no GUI; verify signals (`repository_changed`, `merge_conflict_in_progress`, `staged_files`, …) and `CommandProcessor` undo/redo bounds.
- `tests/ui/` — integration tests with `pytest-qt`; cover open repo, commit, branch switch, merge, conflict resolution, undo, drag-and-drop.
- Performance: synthetic 5000-commit repo, graph layout < 1s.

## Gotchas for new sessions
- `README.md` is a one-line placeholder — do not treat it as documentation.
- All planning docs are in Russian. If responding in another language is preferable for the user, ask first.
- Lint/format: `ruff check src/ tests/` (configured in `pyproject.toml`, line-length 100, py310).
- Tests: `python -m pytest` (set `QT_QPA_PLATFORM=offscreen` on headless Windows/CI for `pytest-qt`).
- Entry point: `python -m src.main` (no console script in `pyproject.toml` yet — deferred).
- Source layout: `src/` is the top-level package (so imports look like `from src.core.models import ...`). Tests rely on `pythonpath = ["src"]` from `pyproject.toml`'s `[tool.pytest.ini_options]`.
