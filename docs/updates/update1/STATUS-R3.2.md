# STATUS — R3.2 (update1): UI thread blocking — async dispatch + pathspec diff + priority cache

**Дата:** 2026-07-21 (коммит создан), 2026-08-11 (STATUS-file дописан при update4)
**Ветка:** update1
**Коммит:** `0cf3d8b` — stage-R3.2(update1): UI thread blocking — async dispatch + pathspec diff + priority cache (P3-P7, H18-H19, M11)

> Note: реализация R3.2 в update1 была закоммичена 2026-07-21, но STATUS-R3.2.md
> не был создан. update4 / PLAN.md (задача 3) поручил задним числом задокументировать
> изменения и прогнанные гейты. Этот файл — синтез из git commit body + смежных
> STATUS-R3.3.md / STATUS-R3.4.md.

## Что сделано

### P3 — убрать `QApplication.processEvents` из синхронных веток
- `src/viewmodels/main_viewmodel.py`: удалены вызовы `QApplication.processEvents`
  из 4 путей:
  - `fetch_and_checkout_remote_branch`
  - `reset_local_branch_to_remote`
  - `checkout_branch`
  - `checkout_commit`
- Это синхронные команды, которые вызывались из GUI; `processEvents` разрывал
  event loop между шагами и оставлял UI в полупоследовательном состоянии.
- Backstop: команды остаются в `CommandProcessor`/`QRunnable`, обработка ошибок
  по-прежнему идёт через `error_occurred` signal.

### P4 — ленивый `request_full_document()` (pathspec diff)
- `src/viewmodels/commit_panel_viewmodel.py`: переход с eager полного
  `request_full_document()` на ленивый — diff-tree читается по pathspec,
  full-tree diff поднимается только когда пользователь реально его открывает.
- Снижает I/O на diff-рендеринг для больших репозиториев.

### P5 — batch refresh для stage_all / unstage_all
- `src/viewmodels/main_viewmodel.py`: `_run_async` batch logic для
  `stage_all_unstaged` / `unstage_all_staged`.
- `src/viewmodels/commit_panel_viewmodel.py`: `set_batch_refresh()` +
  `recompute_selected_diff()` — единая точка обновления diff state после
  batch операций.

### P7 — `branch_priority_cache` в `GraphViewModel`
- `src/viewmodels/graph_viewmodel.py`:
  - `_branch_priority_cache` populated on `refresh_graph()`.
  - Public `branch_priority_for(name)` — read-only API.
  - Helpers: `_head_target_sha()`, `_head_ancestor_tips()`,
    `_update_branch_priority_cache()`.
- `src/ui/widgets/graph_panel.py`: `_branch_priority_key` читает из VM cache;
  `_is_branch_reachable_from_head` помечен deprecated (back-compat shim).

### H18/H19 (UI surface)
- `main_window.py:1454` — throttle на `ApplicationActive` (предотвращает
  refresh storm при переключении фокуса; покрыто тестом
  `test_main_viewmodel_refresh.py`).

### M11
- Никаких Meshgraph-views не вынуто из основного типа; cache живёт поверх.

## Тесты

Создан/обновлён:
- `tests/viewmodels/test_r3_2.py` — 7 тестов: P3 processEvents spy, P4 pathspec
  diff, P5 batch refresh, P7 cache population.
- `tests/viewmodels/test_commit_panel_viewmodel.py` — 1 тест обновлён под R3.2
  P4 contract change (lazy full-document).

Прогнанные gates (per commit body):
- `tests/viewmodels/test_r3_2.py`: 7 passed
- `tests/viewmodels/test_commit_panel_viewmodel.py`: 37 passed
- `tests/viewmodels/test_main_viewmodel_remotes.py`: 35 passed
- `tests/viewmodels/test_graph_viewmodel.py`: 21 passed
- `tests/ui/test_right_panel.py`: 65 passed
- `ruff check src/ tests/`: 0 (после W292 trailing-newline fix в конце test_r3_2.py)

Pre-existing W292 trailing-newline warning в `test_r3_2.py` (задокументирован
в R3.3/R3.4 STATUS-файлах; не блокирующий).

## Файлы изменены

- `src/viewmodels/main_viewmodel.py`
- `src/viewmodels/commit_panel_viewmodel.py`
- `src/viewmodels/graph_viewmodel.py`
- `src/ui/widgets/graph_panel.py`
- `src/ui/main_window.py` (H18 throttle)
- `tests/viewmodels/test_r3_2.py` (new)
- `tests/viewmodels/test_commit_panel_viewmodel.py` (1 test updated)

## Коммит

`0cf3d8b` — stage-R3.2(update1): UI thread blocking — async dispatch + pathspec diff + priority cache

## Заметки (для будущей ретроспективы)

- R3.2 был самым "тихим" из всех R3.* — никаких видимых пользователю
  изменений, только снятие UI-thread blocking под капотом. Именно поэтому
  STATUS-R3.2.md не был создан: автор видел "no user-visible change" и
  не чувствовал потребности в отдельном SUMMARY. Урок для будущих
  сессий: даже чисто-внутренние perf/stability изменения заслуживают
  STATUS-file, иначе они теряются в истории и любой RCA начинается
  с нуля.
- Cache invalidation для `_branch_priority_cache` идёт через
  `refresh_graph()` — это означает, что внешние модификации репа
  (post-force-push, ручной `git reset` через CLI) требуют `refresh_graph`
  для актуализации приоритетов. Это by design, но документировано только
  в этом STATUS — стоит добавить в docstring класса.
