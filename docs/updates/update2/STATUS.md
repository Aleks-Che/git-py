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
