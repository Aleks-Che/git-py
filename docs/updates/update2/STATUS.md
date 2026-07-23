# STATUS — update2 (все этапы выполнены)

План: `docs/updates/update2/PLAN.md`. Дата завершения: 2026-07-23.
Проверка: `ruff check src/ tests/` — чисто; `python -m pytest` — **1180 passed** (было 1140 на старте update2).

## Этап A — аватар на графе (пункт 3)
- Корневая причина: update1 (`681057d`) заменил pixel-snapped локальный рендер на общий `make_avatar_pixmap` (full-bleed сетка 3.8 px + круглый клип + белое кольцо).
- Фикс: `src/utils/avatar.py` — для `shape="circle"` pixel-snapping сетки (15×15 при size=19); параметр `inner_border` (граф передаёт `False`). `graph_panel._avatar_for` — ключ кэша включает `inner_border`.
- Тесты: `tests/core/test_avatar.py` (4).

## Этап B — цвета рёбер графа (пункты 4–7)
Общий фикс в `src/core/graph_v2.py` (overlay fork-коннектора + пост-проход):
- **B3** (п.6, kilocode `22149292`): `pipe_color_index` главной ячейки fork-коннектора постфиксируется в `final_color_index` — вертикаль под коммитом больше не в цвете дочерней ветки.
- **B1** (п.4, sql-skill `8ee78fc`): ячейка слева от `CROSS(d=-1)` перекрашивается в цвет следующего fork-сегмента — нет полклетки цвета ветки за изгибом.
- **B2** (п.5, sql-skill `460f62c`): чужая горизонталь слева от `MERGE_LEFT` удаляется (`HORIZONTAL`→`EMPTY`, `HORIZONTAL_PIPE`→`PIPE`) — нет полклетки «в пустоту».
- **B4** (п.7, kilocode `9c0e4f76`): приоритет мерджа — overlay не перезаписывает ячейки коннектора родителей от коммита до `CROSS` (`merge_own_cols`); цвет ветки только вверх; дыры между `CROSS` и следующим изгибом заполняются.
- Документация: матрица приоритетов в `docs/FEATURES.md`, кейсы в `docs/TEST_PLAN.md`.
- Инструмент: `tools/dump_graph_cells.py` — дамп ячеек строк реального репозитория.
- Тесты: `tests/core/test_graph_v2.py` (5 новых). Ручная верификация дампом на обоих репозиториях.

## Этап C — меню коммита (пункт 1)
- Core: `cherry_pick(create_commit=)`; `drop_commit` (tip → reset --hard; иначе `rebase --onto`; запрет merge/root/detached/not-ancestor); `edit_commit_message` (tip → pygit2 amend; иначе rebase -i reword со scripted editors); `is_commit_pushed`.
- Команды: `CherryPickCommand(auto_commit=)`, `DropCommitCommand`, `EditCommitMessageCommand` — undo по шаблону RebaseCommand.
- VM/UI: глаголы `cherry_pick_commit`/`drop_commit`/`edit_commit_message`; 3 сигнала + пункты меню (Drop disabled для merge); `QInputDialog.getMultiLineText` для сообщения; `_confirm_history_rewrite` (QMessageBox, default No) для запушенных.
- Тесты: core (9), команды (5), UI-меню (5).

## Этап D — Shift-выделение + Squash (пункт 2)
- Виджет: `_selected_shas` + `_selection_anchor`; Shift+ЛКМ — диапазон по строкам; подсветка; ПКМ внутри → «Squash (N) commits», вне — сброс; валидация `_squash_range_validity` (цепочка, без merge/root/stash/WIP).
- Core: `squash_commits` — tip-диапазон через `reset --soft`, середина через interactive rebase (`squash`-строки, scripted editors).
- Команда/VM: `SquashCommitsCommand`, `squash_commits(shas, message)`; диалог объединённого сообщения; push-guard по самому старому коммиту.
- Тесты: core (6), команда (2), UI (4).

## Follow-up (2026-07-23): выступ горизонтали за изгибом
- Симптом (оба на kilocode): `9c0e4f76` и `5c7978c2` — линия продолжается вправо на полклетки за `MERGE_LEFT`-изгиб в пустоту. Причина: заливка дыры (правило 4 этапа B4) писала полноширинную odd-ячейку перед изгибом.
- Фикс: `CellInfo.direction` для `HORIZONTAL`/`HORIZONTAL_PIPE` (`d=-1` — рисовать только левую половину спана, «right-trimmed»); заливка помечает последнюю ячейку перед изгибом; рендер `_trimmed_horiz_len` в `graph_panel._draw_cell_row`. Wire-формат: ключ `d` в `to_dict`.
- Тест: `test_no_gap_between_cross_and_next_fork_bend` дополнен проверкой `direction == -1` и сериализации. 1180 passed.

## Follow-up 2 (2026-07-23): обрыв левого мерджа при fork'е из того же коммита
- Симптом (kilocode `a87ddecf`): коммит одновременно fork-точка (ребёнок на лейне справа) и мердж со вторым родителем на лейне 0 слева. Линия уходила влево от коммита, пересекала один параллельный пайп и обрывалась: fork-коннектор затирал своими `PIPE` ячейками `TEE_RIGHT`-стыковку на лейне родителя (col 0), `HORIZONTAL_PIPE`-пересечение (col 2), а его `TEE_RIGHT` в ячейке коммита заменял `TEE_LEFT` мерджа — дыра на col 3.
- Фикс: `left_merge_cols` в оверлее fork-коннектора — колонки левого мердж-коннектора защищены от перезаписи plain-PIPE; после цикла полуклетка `lane*2-1` заливается `HORIZONTAL`, если fork занял ячейку коммита.
- Тест: `test_fork_commit_with_left_merge_keeps_connector` (синтетика: main + parallel + topic/side дети мерджа). 1181 passed.

## Follow-up 3 (2026-07-23): ветка коммита в правой панели
- Инфо-блок коммита (`commit_detail_panel._format_info`) показывает строку `Branch:` — ветка, которой принадлежит коммит.
- Core: `operations.branch_of_commit(repo, sha) -> str | None` через `git name-rev --name-only --no-undefined --refs=...`: сначала local heads, затем remote-tracking (префикс `remotes/` срезается); суффиксы `~N`/`^N` убираются. VM: `branch_of_commit(sha)`.
- Первая версия (`branches_containing` через `descendant_of` по всем refs) удалена: подвисала на репо с сотнями remote-веток и выдавала бессмысленную простыню «Remote: …+28 more». name-rev — 1–2 git-вызова, ~350 мс.
- Тесты: core (2: local/remote fallback, unknown sha), UI (3: рендер, пропуск, escape). 1186 passed.
