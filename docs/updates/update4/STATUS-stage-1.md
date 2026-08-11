# STATUS — Этап 1 (update4)
**Дата:** 2026-08-11
**Ветка:** feature/update4
**HEAD до:** 2935c9b
**HEAD после:** <после commit>

## Что сделано

### Задача 1: docs/IMPLEMENTATION_PLAN.md sync ✅
- `docs/IMPLEMENTATION_PLAN.md:142`: Этап 10 checklist `[ ] не начато` → `[x] завершено (R4, 2026-07-21)`, добавлены start/end dates и комментарий-ссылка на STATUS-R4.
- `docs/IMPLEMENTATION_PLAN.md:147–150`: раздел «Текущий статус» переписан: Этап 10 завершён, версия v0.11.1, последнее обновление 2026-08-11.
- `grep -n "Этап 10" docs/IMPLEMENTATION_PLAN.md` показывает согласованные 73 (section), 142 (checklist), 148 (status) — противоречий нет.

### Задача 2: Самодостаточный test_build_graph_pipe_color_zero_does_not_fall_back_to_oid_color ✅
- Файл: `tests/core/test_graph_v2.py::test_build_graph_pipe_color_zero_does_not_fall_back_to_oid_color`.
- Полностью переписан: вместо зависимости от локальной ветки `visual-feat` строит synthetic repo через `tmp_git_repo` fixture + `pygit2.Repository`.
- Topology: `root → beta1 → beta2` потом `merge_b`; `xyz1 → xyz2` под `refs/heads/xyz0` (crc32("xyz0") % 40 == 0 → GREEN idx 0 — bug trigger); `merge_x` объединяет xyz0 в main; затем `m1, m2`.
- Helpers: `_commit(parents, message, files)` (handles first commit with no parents), `_commit_chain(message, files)` (linear commits, caches `head_oid`).
- Bootstrap fix: первый commit идёт с `parents=[]` (пустой список), последующие linear — с `[head_oid]`. Это было основным багом первой попытки (`assert head_oid is not None` падал).
- Side-branch fix: `_commit_side(branch_name, parent_oid)` — commit'ы в side-ветках пишутся в `refs/heads/{branch}`, не в main. Раньше все шли на main → конфликтовало с реальной fork-merge-топологией.
- Property check: после fix `pipe_color_index == 0` (GREEN) сохраняется на xyz0 lane one row past chain — главный regression target.
- Replaced `n.commit.branch_names` → `n.branch_names` (NodeInfo attribute, не CommitInfo).
- Assert now reads `pipe_color_index` for CROSS/HORIZONTAL_PIPE/TEE cells (где вертикальная pipe-color живёт) and `color_index` for plain PIPE.
- Verification: целевой тест PASSED, полный `tests/core/test_graph_v2.py` 80/0 PASS.

### Задача 3: STATUS-R3.2.md (update1) ✅
- Файл: `docs/updates/update1/STATUS-R3.2.md` — синтезирован задним числом из коммита `0cf3d8b` + смежных STATUS-R3.3.md/R3.4.md.
- Покрывает: P3 (processEvents removal), P4 (lazy pathspec diff), P5 (batch refresh), P7 (branch_priority_cache), H18/H19 (ApplicationActive throttle), M11.
- Перечислены тесты: test_r3_2.py (7), test_commit_panel_viewmodel.py (37), test_main_viewmodel_remotes.py (35), test_graph_viewmodel.py (21), test_right_panel.py (65).
- Указано pre-existing W292 trailing-newline warning в test_r3_2.py (не блокирующий).
- Заметки для ретроспективы: почему R3.2 не получил STATUS сразу (автор видел "no user-visible change" + считал perf/stability изменения неотчётными); рекомендация добавить cache invalidation note в docstring GraphViewModel.

## Что упало / известные проблемы

- **Salvage process:** первый sub-agent dispatched на 60 iter original hit budget cap before writing STATUS-stage-1.md. Salvage: orchestrator dispatched narrow fixup sub-agent (25 iter) for the broken test, then wrote STATUS-R3.2.md and STATUS-stage-1.md orchestrator-side (markdown exception). Тест brittle: первая попытка не учла empty-repo HEAD. Учтено в отдельной заметке про TDD/iter budget в финальном cron-отчёте.
- **Pre-existing failing test:** `tests/core/test_remove_remote_deletes_it` (1 failure observed в full `tests/core/` прогоне) — about `remote.origin.prune` config cleanup в pygit2. Не связано с update4, документировано в более ранних STATUS-файлах.
- **No `state.md` for update4 yet** — будет написан post-merge.

## Тесты

- `pytest tests/core/test_graph_v2.py::test_build_graph_pipe_color_zero_does_not_fall_back_to_oid_color`: **1 passed** (0.49s)
- `pytest tests/core/test_graph_v2.py` (full file): **80 passed** (1.42s)
- `pytest tests/core/` (full dir): **316 passed**, 1 pre-existing failure (`test_remove_remote_deletes_it`, out of scope)
- `ruff check src/ tests/`: **All checks passed!**

## Файлы изменены

- `M docs/IMPLEMENTATION_PLAN.md` (Task 1)
- `M tests/core/test_graph_v2.py` (Task 2)
- `+ docs/updates/update1/STATUS-R3.2.md` (Task 3, new)
- `+ docs/updates/update4/STATUS-stage-1.md` (this file, new)
- `+ PLAN-improve/update4/state.md` (orchestrator-owned, new)

## Коммит

не делался (orchestrator commit)

## Заметки для Дипсика (ревью)

- **Salvage note:** этот stage-1 salvaged — original sub-agent hit 60/60 iter cap with code & test in place but unverified; re-dispatched narrow fixup (25 iter) for the test only; orchestrator wrote STATUS files (markdown exception). Production code (`docs/IMPLEMENTATION_PLAN.md`, `tests/core/test_graph_v2.py`) edited only by sub-agents, never by orchestrator.
- **Test fragility:** the rewritten test exercises a very specific branch-name → color_idx mapping. If `_pick_branch_color("xyz0")` ever changes (e.g. palette size bumped from 40 to 64), the test would need a new branch name. Consider adding a comment-doc-link to the BRANCH_PALETTE definition.
- **STATUS-R3.2.md was a documentary synthesis, not a code review.** Done as a single-shot from commit body + sibling STATUS files. If R3.2 work areas need a fresh review, that's a separate task.
- **Pre-existing test failure (`test_remove_remote_deletes_it`) should be looked at in update5 or beyond** — not in update4 scope.
