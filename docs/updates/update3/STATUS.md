# Статус реализации (update3)

**Итог: все этапы выполнены (2026-07-23).** Полный прогон: `ruff check src/ tests/` — чисто; `python -m pytest` — **1194 passed**. Версия приложения поднята до `0.11.1` (`pyproject.toml`, `src/__init__.py`).

Эталонный репозиторий для верификации: `C:/work/git/other-repos/llm/kilocode`; визуальные фиксы проверялись дампом `tools/dump_graph_cells.py` (env `GIT_PY_DUMP_LIMIT` — окно истории для дампа).

---

## Этап A1 — Обрезанная горизонталь перед изгибом ✅
- `CellInfo.direction` для `HORIZONTAL`/`HORIZONTAL_PIPE` (`d=-1` — только левая половина спана); сериализация ключом `d` в `to_dict` (`src/core/graph_v2.py`).
- Заливка дыр после CROSS помечает последнюю ячейку перед изгибом `direction=-1` — полноширинная odd-ячейка больше не торчит за `MERGE_LEFT` в пустоту.
- Рендер `_trimmed_horiz_len` в `graph_panel._draw_cell_row`.
- Тест: `test_no_gap_between_cross_and_next_fork_bend` (assert `direction == -1` + `to_dict`).
- Дамп `5c7978c2`: `11:HORIZONTAL(c=28,d=-1) 12:MERGE_LEFT` — выступа нет.

## Этап A2 — Обрыв левого мерджа при fork'е из того же коммита ✅
- Причина: fork-оверлей затирал левый мердж-коннектор (стыковку `TEE_RIGHT` на lane 0, `HORIZONTAL_PIPE` на col 2) и заменял `TEE_LEFT` ячейки коммита своим `TEE_RIGHT` → дыра на col 3.
- Фикс: `left_merge_cols` — защита колонок левого мерджа от перезаписи plain-`PIPE`; полуклетка `lane*2-1` заливается `HORIZONTAL`, если fork занял ячейку коммита.
- Тест: `test_fork_commit_with_left_merge_keeps_connector` (69 тестов графа зелёные).
- Дамп `a87ddecf` (row 293): `0:TEE_RIGHT(7) 1:HORIZONTAL 2:HORIZONTAL_PIPE(7,1) 3:HORIZONTAL 4:TEE_RIGHT(9…)` — коннектор непрерывен от main до коммита.
- `docs/FEATURES.md`: правило 5 в матрице приоритетов.

## Этап B — Ветка коммита в правой панели ✅
- Удалена первая версия (`branches_containing` через `descendant_of` по всем refs): фриз на секунды + бессмысленная простыня веток.
- Core: `operations.branch_of_commit` через `git name-rev` + merge-base пре-фильтр; семантика **first-parent lineage** — trunk засчитывается только по first-parent цепочке (суффикс без `^`).
- `BranchAttribution(name, certain)` (`models.py`): `Branch:` (факт) vs `Reconstructed branch:` (эвристика ближайшего ref, истинная ветка могла быть удалена).
- VM: `branch_of_commit(sha)` + memo-кэш (сброс при смене репо).
- Панель: строка после `Parents:`, escape имён.
- Проверка на kilocode: `95d4ab67` → `Reconstructed branch: origin/feat/cli-assistant-links`; `534075e` → reconstructed dependabot; мерджи на main → `Branch: origin/main`. ~60 мс при trunk-хите, ~0.7 с худший случай, повторный клик — мгновенно.
- Тесты: core (5 кейсов в одном тесте + unknown sha), UI (4).

### Известные ограничения (осознанные)
- Git не хранит ветку авторства: 100% нет в общем случае. FF-merge неотличим от прямых коммитов в trunk; при удалённой исходной ветке — ближайший существующий ref с меткой reconstructed.
- `branch_of_commit` синхронный на UI-потоке (до ~0.7 с на первый клик по «дальнему» коммиту); при желании — вынести в QRunnable.

## Этап C — Автоподгрузка истории (infinite scroll) ✅
- `GraphViewModel.load_more_commits()`: +1 страница к `history_limit`, `refresh_graph()`; no-op при полном DAG; `set_repository` сбрасывает окно.
- `GraphTableWidget`: автоподгрузка за 2 строки до низа (`_maybe_request_more_history`, флаг `_loading_more`); «Load more» в лейбле — гиперссылка.
- Позиция скролла сохраняется: строки добавляются снизу (newest-first).
- Тесты: VM (3) + UI (4), новый файл `tests/ui/test_graph_panel.py`.

### Известные ограничения
- Подгрузка синхронная: при окне >5k коммитов страница может занять ~1 с (перестройка графа). Асинхронный вариант — через `MainViewModel.load_repository_data`-инфраструктуру, отдельной задачей.
- ~~`truncated_count` при async-пути первичной загрузки обновляется только через `refresh_graph`~~ — исправлено ниже (Follow-up C1).

## Follow-up C1 (2026-07-23): async-путь не публиковал truncated_count
- Симптом: в реальном приложении автоподгрузка не срабатывала и лейбла «showing N of M (Load more)» не было — async-воркер `load_repository_data` вызывает `_compute_graph` напрямую, минуя `refresh_graph`, поэтому `_truncated_count` оставался 0 (мои тесты шли sync-путём и потому проходили).
- Фикс (`main_viewmodel.py`): воркер получает `history_limit` с main-потока (раньше всегда молча использовался default 500 — разращённое окно сбрасывалось при любом async-рефреше), считает `truncated_count` через `count_all_history` и возвращает в result; `_on_result` присваивает его VM до эмита `graph_updated`.
- Бонус-фикс: сеттер `GraphViewModel.history_limit` теперь синхронизирует `_page_size` (иначе после ручной установки лимита страница оставалась 500).
- Тест: `test_async_load_updates_truncated_count_for_infinite_scroll` (async load → truncated=15, 10 строк; load_more → 5, 20 строк).

## Follow-up C2 (2026-07-23): фантомный горизонтальный скролл колонки Branches
- Симптом (kilocode `232d7f2c`, 3 ветки в попапе): огромный скроллбар в колонке Branches при том, что рендерится один collapsed-чип. Причина: `_measure_branch_row` суммировал полные имена всех refs, а рендер показывает один priority-чип + индикатор ▼.
- Фикс: замер зеркалит рендер — `_suppress_dup_remotes`, primary по `_branch_priority_key`, ширина одного чипа + слот индикатора.
- Тест: `test_branch_overflow_measures_collapsed_row` (3 длинных remote-имени → measured < половины суммы имён). 1196 passed.

## Follow-up C3 (2026-07-23): спиннер при подгрузке истории
- `GraphViewModel.history_loading_changed(bool)` — эмитится вокруг перестройки графа в `load_more_commits` (только при реальной загрузке страницы, не при no-op).
- `MainViewModel._on_history_loading_changed` пробрасывает в `_is_busy` + `busy_changed` → тот же `QProgressBar`-спиннер в статус-баре справа внизу, что и при переключении репозиториев (плюс штатный re-entrancy guard тулбара).
- Тесты: `test_load_more_emits_history_loading_signal` (burst [True, False] на страницу, тишина при drained), `test_history_loading_drives_busy_spinner` (проброс + сброс `is_busy`). 1198 passed.

## Follow-up D (2026-07-23): правая панель — инфа снизу блока, сплит 50/50
- Баг: у коммита без тела сообщения (напр. merge-коммит `182d18bb` в kilocode) info-блок прижимался к верху — виноват trailing `addStretch()` в верхнем контейнере. У коммита с телом растянутый `_body_scroll` толкал инфу вниз, поэтому там было ок.
- Фикс: филлер `addStretch(1)` между темой и `_body_scroll` (stretch=100, доминирует, когда виден) — info-блок всегда прибит к нижнему краю верхней панели. Trailing stretch удалён.
- Сплиттер message+info / changed files: 60/40 → **50/50** (`setStretchFactor(1,1)` + одноразовый `_enforce_initial_split` на первом show/resize, дальше ручной drag пользователя не трогаем).
- Тесты: `_info_bottom_gap` ≤ 6px для коммита без тела и с телом; дефолтный сплит 50/50 (±8px). 1201 passed.

## Follow-up E (2026-07-23): Full document не отображался
- Корень: `DiffViewWidget.view_mode_changed` не был ни к чему подключён — `request_full_document()` (ленивый вариант R3.2 P4) не вызывался никогда, ни для WIP-панели, ни для commit-detail. Full-document слот оставался пустым.
- Фикс (MainWindow): трекинг источника диффа `self._diff_source` (WIP VM или commit-detail, по последнему `diff_pair_ready`), подключение `view_mode_changed` → `_on_diff_view_mode_changed`, `_maybe_request_full_document()` с re-entrancy флагом (запрос → повторный `diff_pair_ready` → тот же слот). Плюс автозапрос при клике на другой файл, когда viewer уже в FULL_DOCUMENT.
- `DiffViewWidget`: публичные пробы `has_changes_only()` / `has_full_document()`.
- Тесты: toggle для commit-файла и WIP-файла (ленивость сохранена: full пуст до переключения), автоподгрузка при смене файла в full-режиме. 1204 passed.
