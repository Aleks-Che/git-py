# План работ (update3): правки графа, ветка коммита в панели, автоподгрузка истории

Источник: запросы пользователя по реальному репозиторию `C:/work/git/other-repos/llm/kilocode` (сессия 2026-07-23). Все кейсы воспроизводимы на нём; эталонные коммиты указаны в каждом этапе.

**Карта запросов → этапов:**
| Запрос | Суть | Этап |
|---|---|---|
| «Линия уходит на полклетки вправо в пустоту» | Обрезанная горизонталь перед изгибом | A1 (S) |
| «Линия влево от коммита пересекает параллельную и обрывается» | Защита левого мердж-коннектора от fork-оверлея | A2 (M) |
| «Добавить ветку коммита в правую панель» | `Branch:` / `Reconstructed branch:` в инфо-блоке | B (L) |
| «GitKraken подгружает историю при скролле» | Infinite scroll поверх `graph_history_limit` | C (M) |

**Принципы плана (как в update2):**
- На каждый баг — сначала падающий тест, затем фикс.
- Каждый этап завершается зелёным `ruff check src/ tests/` и `python -m pytest`.
- Активный виджет графа — `GraphTableWidget` (`src/ui/widgets/graph_panel.py`); `graph_widget.py` — deprecated.
- Верификация каждого визуального фикса — дампом `tools/dump_graph_cells.py` на kilocode + ручная проверка в приложении.
- Правило приоритета цветов (update2, B4) не нарушать: мердж владеет горизонталью до CROSS, цвет ветки — только вверх и за CROSS.

---

## Этап A1 — Обрезанная горизонталь перед изгибом (right-trimmed) — S

**Кейсы:** kilocode `9c0e4f76` (col 11) и `5c7978c2` (col 11): заливка дыры из update2-B4 писала полноширинную нечётную ячейку перед `MERGE_LEFT`-изгибом — правая половина торчала за изгиб в пустоту.

- [x] `CellInfo.direction` для `HORIZONTAL`/`HORIZONTAL_PIPE`: `d=-1` — рендер рисует только левую половину спана (`graph_v2.py`, `to_dict` ключ `d`).
- [x] Заливка дыр после CROSS помечает последнюю ячейку перед изгибом `direction=-1`.
- [x] Рендер `_trimmed_horiz_len` в `graph_panel._draw_cell_row`.
- [x] Тест: расширен `test_no_gap_between_cross_and_next_fork_bend` (assert `direction == -1` + сериализация).
- [x] Верификация дампом: `5c7978c2` → `11:HORIZONTAL(c=28,d=-1) 12:MERGE_LEFT`.

**Критерий приёмки A1:** линия останавливается у центра лейна изгиба, выступа нет.

---

## Этап A2 — Обрыв левого мерджа при fork'е из того же коммита — M

**Кейс:** kilocode `a87ddecf` («Merge origin/main into johnnyeric/...»): коммит одновременно fork-точка (ребёнок на lane 8 справа) и мердж со вторым родителем `88afe3d` на lane 0 слева. Fork-оверлей затирал plain-`PIPE` ячейками `TEE_RIGHT`-стыковку на lane 0 (col 0), `HORIZONTAL_PIPE`-пересечение (col 2), а его `TEE_RIGHT` в ячейке коммита заменял `TEE_LEFT` мерджа — дыра на col 3: линия шла влево, пересекала один параллельный пайп и обрывалась.

- [x] `left_merge_cols` в оверлее fork-коннектора: колонки левого мердж-коннектора защищены от перезаписи plain-`PIPE` (`graph_v2.py`).
- [x] Полуклетка `lane*2-1` заливается `HORIZONTAL`, если fork занял ячейку коммита (линия дотягивается до точки).
- [x] Тест: `test_fork_commit_with_left_merge_keeps_connector` (синтетика: main + parallel + два ребёнка мерджа).
- [x] Верификация дампом: строка 293 → `0:TEE_RIGHT(7) 1:HORIZONTAL 2:HORIZONTAL_PIPE(7,1) 3:HORIZONTAL 4:TEE_RIGHT(9…)` — непрерывно.

**Критерий приёмки A2:** коннектор непрерывен от main (lane 0) до коммита; fork-коннектор вправо не ломается.

---

## Этап B — Ветка коммита в правой панели — L

**Запрос:** в инфо-блок (`Author/Committed/SHA/Parents`) добавить ветку, которой принадлежит коммит. Итерации по фидбеку: (1) `branches_containing` через `descendant_of` по всем refs — удалено (подвисание на сотнях remote-веток, простыня «+28 more»); (2) name-rev nearest — выбирал соседнюю feature-ветку для коммитов, влитых в main; (3) trunk-first contains — «слипал» большую часть репозитория в main. Финал — first-parent lineage + флаг уверенности.

- [x] Core `operations.branch_of_commit(repo, sha) -> BranchAttribution | None`: `git name-rev --name-only --no-undefined --refs=...`; trunk засчитывается только по first-parent цепочке (суффикс без `^`); приоритет HEAD → main/master (local, origin) → ближайшая local → ближайшая remote; merge-base пре-фильтр (~20 мс отсев); 1–3 git-вызова на клик.
- [x] `BranchAttribution(name, certain)` (`models.py`): `certain=True` — коммит на first-parent цепочке ветки (структурный факт; оговорка: FF-merge неотличим от прямого коммита) → `Branch:`; `certain=False` — достижим только через мердж, ближайший существующий ref → `Reconstructed branch:`.
- [x] VM `branch_of_commit(sha)` + memo-кэш `_branch_of_commit_cache` (сброс в `set_repository`).
- [x] Панель `_format_info(info, branch)` — строки после `Parents:`, escape имён.
- [x] Тесты: core (first-parent: merged feature ≠ main, удалённая ветка → `("main", False)`, remote fallback, unknown sha); UI (обе метки, пропуск, escape).

**Критерий приёмки B:** клик по коммиту не подвисает (< 1 с худший случай, повторный клик мгновенно); `95d4ab67` → `Reconstructed branch: origin/feat/cli-assistant-links` (не main); мерджи/прямые коммиты main → `Branch: origin/main`.

---

## Этап C — Автоподгрузка истории (infinite scroll) — M

**Запрос:** история обрезана `graph_history_limit` (default 500; нижний коммит kilocode `2855ebbe`, 13.07), а GitKraken при прокрутке подгружает (напр. `dfcb6235`, 29.05). Нужна такая же автоподгрузка.

- [x] `GraphViewModel.load_more_commits()`: `history_limit += _page_size` + `refresh_graph()`; no-op при `truncated_count == 0` / без репо. `_page_size` = начальный лимит; `set_repository` сбрасывает окно на одну страницу.
- [x] `GraphTableWidget._on_scroll` → `_maybe_request_more_history`: порог 2 строки от низа; флаг `_loading_more` против реентерабельности (перестройка графа дёргает `valueChanged`).
- [x] «Load more» в truncation-лейбле — гиперссылка (`linkActivated`), тот же путь.
- [x] Тесты: VM (3: постраничный рост, no-op без репо, сброс окна), UI (4: подгрузка внизу, не-срабатывание вверху, линк, остановка на полной истории).

**Критерий приёмки C:** на kilocode скролл до низа подгружает страницы вплоть до полного DAG; позиция скролла не скачет (строки добавляются снизу); при полной истории лейбл скрывается.

---

## Прочее
- [x] Версия поднята до **0.11.1** (`pyproject.toml`, `src/__init__.py`).
