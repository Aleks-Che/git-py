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

## Follow-up F (2026-07-23): async подгрузка истории (спиннер + анфриз UI)
- Баг: `load_more_commits` гонял `refresh_graph()` синхронно на UI-потоке — цикл событий блокировался, спиннер `history_loading_changed` не успевал отрисоваться/анимироваться, всё зависало (кейс: kilocode, подгрузка страниц графа).
- Фикс: `GraphViewModel(async_enabled=…)` — при `True` (продакшн через `MainViewModel`, тот же флаг что и у repo-load) подгрузка страницы идёт через `_load_more_async`: `AsyncWorker` на `QThreadPool`, worker открывает собственный `RepositoryManager` (libgit2 не thread-safe), считает `_compute_graph` + `truncated_count` в фоне, результат применяется на UI-потоке. Re-entrancy гард `_load_more_in_flight` (скролл-триггер стреляет на каждый тик скроллбара), generation-токен `_load_more_generation` бампается в `set_repository` — stale-результат дропается (зеркало R2.2 C7). Сильная ссылка на worker `_load_more_worker`, сброс по `lifespan_finished`.
- Sync-путь (async_enabled=False, тесты) без изменений: burst [True, False] вокруг `refresh_graph`.
- Кэш branch-priority не пересчитывается воркером — набор веток от пейджинга не зависит, а смена репо во время busy заблокирована гардом `set_repository` (R2.2 M8).
- Тесты: async страница приходит с `graph_updated` (states == [True] сразу после вызова), re-entrant вызовы не растят окно, stale-drop после rebind, MainViewModel-интеграция (busy True синхронно → False после воркера). Обновлён `test_async_load_updates_truncated_count_for_infinite_scroll` под async-семантику. 1208 passed.

## Follow-up G (2026-07-24): граф — обрыв линий на границе окна, uniform форк-коридор, Parent singular
- **G1. Обрыв линии под коммитом** (kilocode `4327386f`, окно 500): `build_graph` отбрасывал родителей вне окна (`valid_parents = [p for p in parents if p in oid_to_row]`) — лейн закрывался полуклеткой под коммитом. Фикс: dangling-родители регистрируются на лейне как обычно (`valid_parents = list(commit.parents)`); SHA никогда не матчится со строкой ниже → лейн уходит за нижний край (как в GitKraken). Два in-window коммита с общим off-window родителем сходятся в один лейн (fork-точка под границей) — существующая `was_existing`-логика и lane-merge cleanup (`continues_down = not ending_already_shown`) уже корректно обрабатывают этот случай. Тесты: продолжение лейна до низа, merge с dangling вторым родителем (BRANCH-угол + pipe), конвергенция двух коммитов. Проверено дампом: под `4327386` теперь `PIPE(c=10)` вниз.
- **G2. Цвет форк-коридора** (kilocode `05dadaa`): `_build_fork_connector_cells` красил каждый сегмент коридора в цвет *следующего* ребёнка (relay) — ребро коммита к родителю шло зелёный→янтарь→серый. Фикс: весь коридор uniform в `main_color` (цвет fork-main/родительского лейна — согласуется с конвенцией «коннектор в цвет цели», как у merge-коннекторов в `_build_row_cells`); ребёнок сохраняет цвет только в вертикальном отводе (TEE_UP pipe, MERGE_LEFT bend). Cleanup: CROSS `next_color` → `main_color`; MERGE_LEFT-erasure не трогает коридорный `main_color`. `_rebalance_stashes_for_wip` унаследовал фикс (тот же билдер). 4 теста переписаны под новый контракт (+1 новый на uniform). Проверено дампом: строка под `05dadaa` — сплошной `c=7` с цветными отводами.
- **G3. Parent singular**: `_format_info` — один родитель → `Parent:`, несколько → `Parents:`. Тесты singular/plural, XSS-тест обновлён.
- 1213 passed, ruff чистый.

## Follow-up H (2026-07-24): цвет форк-коридора — финальное правило
- **H1. Коридор ≠ цвет lane-кэша** (kilocode `2c070e6e`): коридор брал `lane_color_index[main_fork_lane]` — там мог лежать цвет постороннего ребёнка (фиолетовый коридор на зелёном коммите). Фикс: `_build_fork_connector_cells` вызывается отложенно, после вычисления `final_color_index` коммита (snapshot лейнов `connector_active_lanes`), коридор = `final_color_index`. Мёртвый fixup TEE_RIGHT-pipe (кейс `22149292`) удалён — pipe теперь по построению final.
- **H2. Один ребёнок → цвет ребёнка** (kilocode `9e1b54d` → `489601e5`): когда у fork-точки ровно один боковой ребёнок, коридор несёт единственное ребро → красится в цвет ветки end-to-end (как merge-коннектор в цвет цели). Несколько детей делят один трек → uniform `final_color_index` родителя (relay запрещён багом G2). Правило живёт в `_build_fork_connector_cells` (`corridor_color`) и продублировано в cleanup build_graph (CROSS `next_color`, MERGE_LEFT-erasure exemption).
- Тесты: `test_fork_connector_single_child_corridor_uses_child_colour` (юнит), `test_fork_connector_multiple_merges_corridor_stays_uniform` (юнит), `test_fork_corridor_takes_commit_colour_not_lane_cache` (мульти, feature-x → 35 ≠ lane-кэш 1), `test_fork_corridor_single_child_keeps_branch_colour_end_to_end` (движок, bend+corridor == цвет ветки). Проверено дампом: `9e1b54d` — коридор c=6 до `MERGE_LEFT(c=6)`; `2c070e6` — коридор c=7 (цвет коммита), отводы детей свои.
- 1215 passed, ruff чистый.

## Follow-up I (2026-07-25): возврат priority-relay + твик bend-клетки + fallback цвета ребёнка
- Расследование (kilocode `bdb9070a`, «все цвета сломались»): сверка через `git worktree` на HEAD + инструментированный прогон показала — старый код без стэша давал relay (trunk c3 → c4 → c30 → c31 → c30), что и есть ожидаемый дизайн («сначала цвет первой ветки в приоритете, потом следующей»). Мой uniform-коридор (G2/H) его сломал. Вывод: relay — правильный дизайн, а жалоба `05dadaa` была про другое: клетка TEE_UP **под коммитом** красилась в цвет *следующего* ребёнка.
- **Откат к relay** в `_build_fork_connector_cells`: trunk = цвет первого merging-ребёнка, каждый сегмент = цвет ребёнка на правом конце.
- **Твик `05dadaa`**: bend-клетка (TEE_UP) несёт цвет **своего** ребёнка (горизонталь и вертикаль) — коридор прямо под коммитом совпадает с его веткой; сегмент следующего ребёнка начинается со следующей колонки.
- **Fallback цвета ребёнка** (деградация со стэшем): при промахе `lane_color_index` цвет merging-лейна берётся из `fork_lane_colors` (child-sha snapshot) вместо цвета fork-точки — старый fallback схлопывал коридор в один цвет при отравленном кэше (переиспользование лейна стэшем). Теперь `bdb9070a` показывает relay даже со стэшем.
- Отложенная сборка коннектора (из H1) сохранена: trunk-pipe = `final_color_index` по построению (бывший fixup `22149292`). Cleanup B1/B4 возвращён к relay-семантике (`next_color` = следующий ребёнок, erasure `!= ml_color`).
- Тесты: `test_fork_connector_multiple_merges_priority_relay` (trunk/сегменты/own-bend), `test_fork_corridor_trunk_uses_first_child_colour` (trunk ≠ цвет коммита), `test_fork_corridor_bend_cell_keeps_own_child_colour` (05dadaa-твик), single-child тесты сохранены (relay == цвет ребёнка), B1/B4 тесты возвращены к оригиналам.
- Дамп-проверка всех 5 кейсов: `bdb9070a` relay c3→c4→c30→c31→c30; `05dadaa` под коммитом c=21 (свой); `f80ebff` trunk c11 + сегмент c20; `9e1b54d` сплошной c=6; `2c070e6` trunk c0, отвод c081f58 янтарный до своего TEE.
- 1216 passed, ruff чистый.

## Follow-up J (2026-07-25): bend-клетка — заворот своим, продолжение следующим
- Уточнение пользователя: заворот `_|` на 90° должен быть одним (своим) цветом, а продолжение вправо — цветом следующей ветки. Мой твик из Follow-up I (TEE_UP целиком своим цветом) давал торчащий `_|_` одним цветом — откачен.
- `TEE_UP` снова несёт `color_index = следующий ребёнок` (горизонталь уходит вправо цветом следующей ветки прямо от развязки), `pipe_color_index = свой ребёнок` (вертикаль вверх). Левый рычаг bend'а приходит из предыдущей нечётной клетки в своём цвете — рендер менять не нужно, переход происходит ровно в центре развязки.
- Тесты: `test_fork_connector_multiple_merges_priority_relay` (TEE_UP: color=next, pipe=own), `test_fork_corridor_bend_splits_own_and_next_colour` (up-pipe + входящий сегмент = свой, исходящая горизонталь = следующий; MERGE_LEFT = свой). Дамп: `bdb9070a` — TEE@6(c=4,p=3), TEE@8(c=30,p=4), TEE@16(c=31,p=30), TEE@20(c=30,p=31).
- 1216 passed, ruff чистый.

## Follow-up K (2026-07-25): merge-коннектор владеет trunk'ом левее fork-коридора
- Баг (kilocode `1a3c7191`, merge PR + создание ветки из того же коммита): янтарная ветка второго родителя заворачивала влево на полклетки и обрывалась — fork-коннектор перекрашивал трассу [коммит..bend] в цвет своего коридора. Ожидание: merge-коннектор идёт прямо в коммит (эталон — `9f7c8e5`, где trunk уже принадлежит merge-цвету через CROSS-правило).
- Фикс движка (`build_graph`): право-сторонний `BRANCH_LEFT` ЛЕВЕЕ всех fork-bend'ов защищает спан `[commit_col, bend_col)` от fork-оверлея целиком (включая gap-ячейку перед bend'ом — отдельный `right_bend_span`, т.к. для CROSS-спанов gap должен оставаться заполняемым); сам bend перекрашивается relay-сплитом: `color_index` = цвет коридора (первый fork-ребёнок), `pipe_color_index` = свой (второй родитель) — как TEE_UP из Follow-up J.
- Сериализация: `CellInfo.to_dict` для `BRANCH_LEFT` пишет `p` только при `pipe != color`; фабрика `branch_left(color)` теперь ставит `pipe = color` (одноцветные bend'ы сериализуются без `p` — рендер не меняется).
- Рендер (`graph_panel`): `BRANCH_LEFT` с `p` рисует продолжение `lane_w/2` вправо цветом коридора + заворот (левый рычаг + вертикаль вниз) своим цветом.
- Дампер `tools/dump_graph_cells.py` печатает `p=` для split-`BRANCH_LEFT`.
- Дамп `1a3c7191` (row 52): `0:TEE_RIGHT(c=3,p=19)` → c3 до `6:BRANCH_LEFT(c=6,p=3)` → коридор c6 до `12:MERGE_LEFT(c=6)`; эталонный `9f7c8e5` (row 164) не изменился.
- Тест: `test_merge_connector_owns_trunk_left_of_fork_corridor` (топология: p2 на lane 2 через BRANCH_LEFT, fork-ребёнок на lane 6; trunk/gap/split/коридор/сериализация).
- Follow-up K1: bridge-pipe между строками (`_draw_cells`, graph_panel) брал цвет из `"c"` клетки верхней строки — для split-`BRANCH_LEFT` это цвет коридора, и сегмент над родительским коммитом становился чужим. Добавлен `_T_BRANCH_LEFT` в оба lookup'а pipe-цвета (для одноцветных bend'ов `p` отсутствует → fallback на `c`, поведение не меняется).
- 1217 passed, ruff чистый.
