# Отчёт о проверке реализации плана update1

**Дата:** 2026-07-21
**Объект проверки:** реализация `docs/updates/update1/PLAN.md` (коммиты `a7d1a83`..`dae6de7`, 13 штук)
**Метод:** ревью коммитов, прогон тестов, повторный запуск воспроизводящих скриптов из REVIEW.md, точечные живые проверки.

---

## 1. Сводка

| Этап плана | Статус | Коммиты |
|---|---|---|
| R1 (потеря данных) | ✅ реализован (с оговоркой — см. §3) | R1.1–R1.7 |
| R2 (стабильность) | ✅ реализован | R2.1–R2.6 |
| R3 (производительность/UX) | ❌ **не реализован** | коммитов нет |
| R4 (чистка) | ❌ **не реализован** | коммитов нет |

**Тесты:** было 1028 → стало **1099** (1082 passed / 17 failed после фикса запуска; до фикса — 97 failed).
**Линт:** `ruff check src/ tests/` — чисто.

**Приложение не запускалось** — причина найдена и исправлена (см. §2).

---

## 2. Падение запуска (исправлено)

`AttributeError: 'MainWindow' object has no attribute '_log_widget'`

В `src/ui/main_window.py:_build_central` сигнал `commit_detail.error_occurred` подключался к `self._log_widget.append_log` (строка ~626) **до** создания `LogWidget` (строка ~660) — регрессия из R2.5 (подключение нового сигнала вставили не в то место). Тот же краш каскадом ронял ~70 UI-тестов, конструирующих `MainWindow`.

**Фикс (внесён):** подключение перенесено ниже создания виджета. После фикса `MainWindow` конструируется, приложение стартует, каскадные падения тестов исчезли (97 → 17 failed).

---

## 3. Проверка ключевых исправлений (живые прогоны)

### ✅ R1.2 — checkout и untracked
SAFE checkout с untracked — успех (было: блокировка); FORCE checkout с untracked — успех (было: `DirtyWorkTreeError` + разорванный worktree). Rollback восстанавливает HEAD и worktree, detached-HEAD сохраняется по OID.

### ✅ R1.3 — detached HEAD
`merge_branch`/`rebase_branch` в detached HEAD → доменная `GitError` («merge_branch requires a target branch…»), репозиторий не тронут. Сырой `KeyError` устранён.

### ✅ R1.4 — untracked в commit
`commit_changes(stage_all=True)` стейджит только tracked-изменения (явный pathspec), первый коммит на unborn HEAD разрешён. Побочный эффект на старые тесты — см. §5.

### ⚠️ R1.1 — merge X into Y: порча ref'ов устранена, но остался баг mid-state
**Устранено:** целевая ветка получает merge-коммит с правильным первым родителем, текущая ветка не двигается, undo корректно откатывает ref и возвращает HEAD.

**Новый/оставшийся дефект (HIGH):** после `merge X into Y` при HEAD≠Y репозиторий остаётся в неконсистентном состоянии до первого undo/checkout:
1. `HEAD` молча переключён на target (пользователь был на своей ветке — контекст потерян);
2. workdir/index не соответствуют новому HEAD: файлы из merge-коммита **отсутствуют на диске**, `status` показывает фантомный `INDEX_DELETED` (проверено прогоном: после merge — `{'s.txt': INDEX_DELETED}`, в workdir только `a.txt`; файлы прежней ветки удалены с диска);
3. визуально в приложении это выглядит как «грязный» репозиторий сразу после успешного merge.

Причина: `operations.py:559-571` делает `checkout(target)` перед merge, но после `create_commit` не выполняет ни checkout объединённого дерева, ни возврат HEAD на исходную ветку; `r.merge()` трогает только index, работа с worktree не завершается.

**Фикс (на выбор):**
- (a) после `create_commit` — `checkout_head`/checkout merge-дерева + остаться на target (синхронно с тем, что undo уже делает), либо
- (b) вариант из плана: merge без checkout (`merge_commits` в память + запись ref) — HEAD и workdir пользователя вообще не трогаются. Предпочтителен для UX «merge в фоне».
- В любом случае добавить тест: после merge X→Y при HEAD=Z статус чист и HEAD там, где ожидается.

### Остальное из R1/R2 (по тестам и коду)
- R1.5 (stash undo с OID-проверкой и снапшотом worktree), R1.6 (процессор возвращает команду в стек при падающем undo, `index.read(force=True)` после CLI), R1.7 (non-undoable команды вне стека + `_confirm_destructive`), R2.1–R2.6 — реализованы; новые тесты на это есть и проходят.
- H9 задокументирован в `docs/DEVELOPMENT_RULES.md:46-49` (исключения из CommandProcessor) — соответствует плану.
- Изменённая семантика процессора (конфликтная команда остаётся в undo-стеке для abort-undo) разумна и задокументирована в коде, но противоречит старым тестовым контрактам — см. §5.

---

## 4. Не реализовано (R3, R4) — точечные проверки

| Пункт плана | Проверка | Факт |
|---|---|---|
| R3.1 лимит истории 500 | `graph_viewmodel.py:274` | `max_count=500` на месте, пагинации нет |
| R3.1 O(n²) в графе | `graph_v2.py` | не тронут |
| R3.2 git-обход в paintEvent | `graph_panel.py:1085` | `_is_branch_reachable_from_head` на месте |
| R3.2 полнодеревные diff'ы | `commit_panel_viewmodel.py` | `pathspec` не используется |
| R3.2 синхронный fetch | `main_viewmodel.py` | без изменений |
| R4 debug-`print` | `main_viewmodel.py` | 20 `print(` на месте |
| R4 мёртвый `GraphWidget` | `graph_widget.py` | на месте |

---

## 5. Оставшиеся 17 падений тестов — разбор

Ни одно из них не является регрессией функциональности; это **битые новые тесты** и **устаревшие старые** (не обновлены после намеренной смены контрактов).

### 5.1. Битые новые тесты — хардкод путей Linux (5 шт.)
`Path("/root/projects/git-py/...")` — тесты написаны на Linux-машине, на Windows падают с `FileNotFoundError`:
- `tests/viewmodels/test_main_viewmodel_r2_3.py:217`
- `tests/ui/test_qt_lifecycle_r2_6.py:10, 28, 49, 63` (4 теста)

**Фикс:** путь от корня репо, например `Path(__file__).resolve().parents[N] / "docs" / ...`.

### 5.2. Устаревшие сетups после R1.4 (4 шт.)
Тесты пишут **новый** файл и коммитят его через `commit_changes(...)` — раньше это работало (untracked стейджился), теперь файл остаётся untracked и сценарий рассыпается:
- `test_main_viewmodel_merge.py::test_cherry_pick_clean_stages_in_index` (`f.txt` не в коммите)
- `test_main_viewmodel_merge.py::test_merge_async_threshold_routes_to_worker` (diff пуст → merge не уходит в worker)
- `test_main_viewmodel_merge.py::test_resolve_conflict_writes_file_and_finalizes_merge`, `test_resolve_conflict_keeps_state_when_other_paths_remain` (ожидают `world.txt` в конфликт-сете)

**Фикс:** в сетапах стейджить новые файлы явно (`repo.index.add(path)`) или через VM `stage_file` — заодно это закрепит новый контракт.

### 5.3. Устаревшие контракты после R1.6/R1.7/R2.4 (7 шт.)
Намеренная новая семантика: конфликтная merge/pull-команда **остаётся в undo-стеке** (undo = abort операции); Push/Fetch/Discard **не пушатся** в стек (`is_noop`). Старые тесты утверждают обратное:
- `test_merge_commands.py::test_merge_command_conflict_is_not_pushed`
- `test_main_viewmodel_merge.py::test_merge_branch_conflict_emits_conflict_state`, `test_abort_merge_clears_conflict_state`
- `test_remote_commands.py::test_pull_command_conflict_is_not_pushed`, `test_push_command_via_processor_can_be_redone`, `test_fetch_command_undo_is_noop`
- `test_main_viewmodel_remotes.py::test_push_changes_sync_path`

**Фикс:** переписать ожидания на новый контракт (can_undo == True после конфликта; undo → abort; Push/Fetch не появляются в стеке) и синхронизировать docstring контракта в шапке `test_main_viewmodel_merge.py:1-12`.

### 5.4. Устаревший popup-тест после R2.6 (1 шт.)
`test_graph_widget.py::test_branch_popup_closes_on_global_mouse_move_outside` держит Python-ссылку на popup и вызывает `isVisible()` после close — с `WA_DeleteOnClose(True)` C++-объект уже удалён (`RuntimeError`). Продакшен-код ссылку очищает корректно (`graph_panel.py:1188-1202, 3216-3222`).

**Фикс:** проверять отсутствие popup через `findChildren(BranchStackPopup)` вместо удержания ссылки.

---

## 6. Рекомендации (приоритет)

1. **Дочинить R1.1 mid-state** (§3) — единственный реальный баг уровня HIGH из оставшихся: после merge X→Y репозиторий «грязный» и HEAD переключён. + регрессионный тест на чистый статус.
2. **Починить 5 тестов с хардкод-путями** (§5.1) — 10 минут, снимает треть падений.
3. **Обновить устаревшие тесты** (§5.2–5.4) под новые контракты — контракты осознанные, код менять не нужно.
4. После этого — отдельным этапом **R3** (производительность) и **R4** (чистка): они не начинались, находки из REVIEW.md (P1–P7, M1, H17–H19, мёртвый код, debug-print'ы) в силе.

**Итог:** критические исправления R1/R2 в основном реализованы качественно и покрыты новыми тестами; запуск приложения восстановлен; остались один HIGH-баг mid-state в merge, косметика в тестах и два целых этапа плана (R3, R4).
