# План работ по результатам ревью (update1)

Источник находок: `docs/updates/update1/REVIEW.md` (ID находок C1–C7, H1–H20, M*, P*, L* используются ниже без изменений).

**Принципы плана:**
- Каждый этап завершается зелёным `ruff check src/ tests/` и `python -m pytest` (1028+ тестов).
- На каждый исправленный баг — регрессионный тест в том же PR/commit'е.
- Порядок внутри этапа важен: сначала фиксы core, затем команд/VM, затем UI.
- После завершения этапа — отметка в `docs/IMPLEMENTATION_PLAN.md` (это содержание Этапа 10 «Тестирование и стабилизация»).
- Оценки трудоёмкости: S < 1 ч, M — часы, L — день+.

---

## Этап R1 — Потеря данных и целостность репозитория

Цель: устранить все сценарии, где пользователь теряет изменения или репозиторий остаётся в битом состоянии.

### R1.1 — `merge X into Y` при HEAD ≠ Y (C1, H12) — L
**Файлы:** `src/core/operations.py:380-470`, `src/viewmodels/commands.py` (`MergeCommand`), `src/ui/widgets/left_panel.py:1005-1023`, `src/ui/main_window.py:965-1026`.

- [ ] `merge_branch`: если `target != head.shorthand` — checkout `target` перед merge (либо merge без checkout через `merge_commits` + запись ref); запретить молчаливый no-op при remote-drop (возвращать осмысленную ошибку/статус).
- [ ] FF-путь: checkout до движения ref, rollback `set_target` в `except` (H12).
- [ ] `MergeCommand`: захватывать `(target_ref_name, target_sha_before)`; undo — откат именно target-ref + возврат HEAD.
- [ ] **Тесты (новые):** интеграционный «merge X into Y при HEAD=Z» на реальном репо (обе ветки, родители merge-коммита, worktree не тронут); undo после такого merge; remote-drop сценарий без моков VM.

### R1.2 — Checkout и untracked-файлы (C2) — M
**Файлы:** `src/core/operations.py:189-268, 271-325`.

- [ ] `_dirty_paths`: фильтровать `GIT_STATUS_WT_NEW`/`GIT_STATUS_IGNORED` (pre-check и post-verify) — untracked не блокирует checkout.
- [ ] Rollback восстанавливает и HEAD, и worktree (`checkout_head(FORCE)` к прежнему дереву); для detached сохранять OID, а не имя.
- [ ] **Тесты:** SAFE checkout с untracked — успех; FORCE checkout с untracked — успех; откат после неудачного checkout оставляет worktree == исходному.

### R1.3 — Detached HEAD (C3) — S
**Файлы:** `src/core/operations.py:396-470`.

- [ ] `merge_branch`: при `head_is_detached` — доменная ошибка (или `head.set_target` напрямую); `lookup_reference` обернуть в `KeyError` → `GitError`.
- [ ] Проверить `checkout_commit` rollback (OID вместо имени) — см. R1.2.
- [ ] **Тесты:** merge/checkout/revert в detached HEAD — доменная ошибка, HEAD и worktree не тронуты.

### R1.4 — Untracked в `commit_changes` (C4) — S
**Файлы:** `src/core/operations.py:94-132`.

- [ ] `add_all()` заменить на stage только tracked-изменений из `status()` (без `WT_NEW`/`IGNORED`); `add_all`/`index.write()` включить в try (M5).
- [ ] Разрешить первый коммит при unborn HEAD (`parents=[]`) (M3) — согласовать с `CommitPanel` UI.
- [ ] **Тесты:** `stage_all=True` не трогает untracked; коммит в свеже-init репо проходит.

### R1.5 — Undo stash-команд (H3, H4, H5) — L
**Файлы:** `src/viewmodels/commands.py:930-941, 988-996, 1019-1038, 1110-1115, 1199-1205`.

- [ ] `StashPushCommand.undo`: сверять `stash_oid_at(0) == _pushed_oid`; несовпадение — искать oid в списке или `GitError` (не глотать).
- [ ] `StashApplyCommand`: снапшот worktree-diff перед apply; undo — откат только применённых путей + восстановление снапшота.
- [ ] `StashPopCommand.undo`: откат применённых изменений + `restore_stash` (не дублировать).
- [ ] **Тесты:** чужой stash между execute/undo; apply поверх dirty worktree → undo возвращает исходный dirty; pop → undo не дублирует изменения.

### R1.6 — Целостность команд и процессора (H1, H2, H11) — M
**Файлы:** `src/viewmodels/commands.py:122-139, 217-223`, `src/core/operations.py` (`_run_git_in_workdir:585` и все CLI-вызывающие: `unstage_changes:1134`, `stash_push_staged:1252`, `discard_file:1773`, rebase-пути).

- [ ] `CommandProcessor.undo/redo`: при исключении возвращать команду в исходный стек.
- [ ] «Silent no-op undo» + redo: first-commit `undo()` — реальный откат (reset на empty-tree) либо не переносить в redo; то же для `CheckoutCommand` unborn, `MergeCommand` up-to-date.
- [ ] После каждого subprocess-вызова, мутирующего index/worktree: `r.index.read(force=True)` — централизованно в `_run_git_in_workdir` (H11).
- [ ] **Тесты:** падающий undo сохраняет команду в стеке; redo первого коммита не дублирует; index после CLI `git reset` свежий.

### R1.7 — Деструктивные действия и подтверждения (H6, H20) — M
**Файлы:** `src/viewmodels/commands.py:1251-1274`, `src/ui/widgets/commit_panel.py:408-417, 527-528, 772-791`, `src/ui/main_window.py`.

- [ ] `DiscardChangesCommand` (и no-op-undo команды `PushCommand`/`FetchCommand`) не пушить в undo-стек — отдельный журнал истории.
- [ ] `QMessageBox.question` (default `No`) для: Discard All, batch-Discard N, Delete File. Единый confirm-helper.
- [ ] **Тесты:** non-undoable команда не включает Undo; диалог показан, No — без изменений.

**Критерий приёмки R1:** воспроизводящие скрипты из ревью (merge target, checkout untracked, detached merge, untracked commit) проходят без ошибок; новые тесты зелёные; ни один сценарий не оставляет worktree/HEAD в несоответствии.

---

## Этап R2 — Стабильность: ошибки, потоки, консистентность команд

Цель: исключения не долетают до UI, нет гонок, правила DEVELOPMENT_RULES соблюдаются единообразно.

### R2.1 — Обработка ошибок в горячих путях (C5, M4) — S
**Файлы:** `src/viewmodels/commit_panel_viewmodel.py:269-277, 493`, `src/viewmodels/graph_viewmodel.py:129`.

- [ ] Ловить `(GitError, pygit2.GitError, OSError)` → `error_occurred`; `stash_list` внутрь try в `_compute_graph`.
- [ ] `repository.py:267` (tags) — `KeyError` → `GitError`.
- [ ] **Тесты:** удалённый `.git/index` между refresh → `error_occurred`, без исключения в слоте.

### R2.2 — Потоки и async (C6, C7, M8, M25) — L
**Файлы:** `src/viewmodels/main_viewmodel.py:187-247, 2550-2569, 2269-2316`, `src/utils/async_worker.py`.

- [ ] `_run_async`: worker-owned `RepositoryManager` по `repo.path` (как `load_repository_data`).
- [ ] Generation-токен/проверка пути в `_on_result` и `clone_repository`; `set_repository` при busy — блок/отмена воркеров.
- [ ] Busy-гвард на `undo`/`redo`; `CommandProcessor` мутируется только с UI-потока (перенести push в стек в result-слот).
- [ ] `AsyncWorker`: `setAutoDelete(False)`; `failed` передаёт тип исключения (не угадывать по `is_merge_in_progress`).
- [ ] **Тесты:** `set_repository` во время `load_repository_data` — данные старого репо не применяются; undo во время async — отклонён.

### R2.3 — Единый busy-гвард и команды в обход процессора (H7, H8, H9, M9) — L
**Файлы:** `src/viewmodels/main_viewmodel.py` (все verb-методы), `src/viewmodels/commit_panel_viewmodel.py:290-351`.

- [ ] Декоратор/хелпер `_guard_mutation()` на все verb-методы (список из H8: `commit_changes`, `undo`, `redo`, `cherry_pick`, `revert`, `create_branch`, `delete_branch`, `rename_branch`, `create_tag`, `resolve_conflict`, `stage_diff_line`, `ignore_pattern`, `delete_file_from_disk`, `apply_stash_file(s)`, `checkout_remote_branch`).
- [ ] `CompleteMergeCommand` для финализации конфликтного merge (undo = reset на pre-merge SHA) (H7).
- [ ] `_move_branch_ref`, `delete_file_from_disk`, `apply_stash_file(s)`, `stage_file`/`unstage_file` — обернуть в `GitCommand` или задокументировать исключениями в `DEVELOPMENT_RULES.md` (H9).
- [ ] Убрать двойной refresh (`fetch_and_checkout_remote_branch`, `reset_local_branch_to_remote`) (M9).
- [ ] **Тесты:** verb при busy → «Another operation is already in progress»; конфликтный merge → undo после разрешения.

### R2.4 — Undo-семантика прочих команд (M6, M7, M10) — M
**Файлы:** `src/viewmodels/commands.py:568-584, 663-669, 691-694, 111-112, 1226-1244`, `src/viewmodels/main_viewmodel.py:578-590`.

- [ ] Redo конфликтующих merge/pull: ловить `MergeConflictError` отдельно → выставлять conflict_state; redo сетевых команд — не на UI-потоке.
- [ ] `CherryPickCommand`/`RevertCommand`: снапшот индека до execute, undo — точечный откат.
- [ ] Undo-стек: `deque(maxlen=N)` (N в конфиг, default ~100) + `stack_changed` при вытеснении; ограничить bytes-бекапы `DiscardFileCommand`.
- [ ] `IgnoreCommand.undo` — корректный откат при параллельном изменении `.gitignore`.
- [ ] **Тесты:** redo конфликтного merge → conflict dialog; staged пользователя переживает undo cherry-pick; граница стека.

### R2.5 — Мелкие баги состояния (H10, M2, M18, M22) — S
**Файлы:** `src/viewmodels/repo_tabs_viewmodel.py:71-83`, `src/core/repository.py:122-126`, `src/utils/config.py:114-131`, `src/ui/widgets/commit_detail_panel.py:174, 710`.

- [ ] `remove_tab`: `elif index < self._active_index: self._active_index -= 1`.
- [ ] `open()`: присваивать `_repo` только после успеха (как `clone()`).
- [ ] `load_config`: `isinstance(data, dict)` перед merge; `save_config`: tmp + `os.replace` (атомарно).
- [ ] Подключить `CommitDetailPanel.error_occurred` к обработчику MainWindow; убрать доступ к `_private` членам чужих виджетов.
- [ ] **Тесты:** `remove_tab` при active в середине; не-dict JSON в конфиге → defaults; `open` на невалидном пути не портит текущий репо.

### R2.6 — Qt жизненный цикл (H13, H14, H15, H16, M13, M14, M23) — M
**Файлы:** `src/ui/widgets/graph_panel.py`, `src/ui/widgets/left_panel.py:924`, `src/ui/widgets/repo_bar_widget.py:112, 44-47`, `src/ui/main_window.py:1302-1327, 1586-1590`, `src/ui/widgets/diff_view_widget.py:1088-1092`.

- [ ] `BranchStackPopup`: `WA_DeleteOnClose(True)` / `deleteLater()` в `hideEvent`.
- [ ] `_branch_chip_rects`: инвалидация при вертикальном скролле (или hit-test арифметикой `scroll_y // row_height`).
- [ ] Drop-меню: `menu.exec(viewport().mapToGlobal(...))`.
- [ ] Close-кнопка табов: `tabAt(btn.pos())` вместо сохранённого индекса; синхронизация VM по `tabMoved`.
- [ ] `_on_busy_changed(False)`: восстанавливать Undo/Redo/Close.
- [ ] Remote-manage диалог: disconnect по `finished`.
- [ ] `singleShot` со stale-курсором: context-объект + перечитывание по индексу.
- [ ] **Тесты:** `findChildren(BranchStackPopup)` пуст после hover-cycle; клик после скролла не открывает чужое меню; закрытие нужного таба после DnD; actions восстановлены после неуспешной async-операции.

**Критерий приёмки R2:** ни один тест и ни один ручной сценарий не показывает трейсбек; все мутирующие пути либо в `CommandProcessor`, либо задокументированы исключениями; повторные async-запуски отклоняются гвардом.

---

## Этап R3 — Производительность и UX

Цель: приложение отзывчиво на репозитории 5000 коммитов (по TEST_PLAN), Windows-специфика закрыта.

### R3.1 — Граф: O(n²) → O(n) и лимит истории (P1, P2) — M
**Файлы:** `src/core/graph_v2.py:531-535, 640-642, 825-827`, `src/core/repository.py:381`, `src/viewmodels/graph_viewmodel.py:121, 263`.

- [ ] `sha→node` dict вместо линейных `any(...)` поисков.
- [ ] Лимит 500: «Load more»/пагинация или индикатор усечения «показано 500 из N»; поиск — по полной истории (лениво).
- [ ] **Тесты:** perf-тест 5000 коммитов < 1с (линейная и ветвистая топологии); индикатор усечения виден.

### R3.2 — Блокировки UI-потока (H18, H19, P3, P4, P5, P6, P7, M11) — L
**Файлы:** `src/viewmodels/main_viewmodel.py:1140-1154, 2491-2519, 473-495, 2364-2368, 706-739`, `src/viewmodels/commit_panel_viewmodel.py:513-612`, `src/ui/widgets/commit_detail_panel.py:688-752`, `src/ui/widgets/graph_panel.py:1054-1139`, `src/ui/main_window.py:1259-1274`.

- [ ] Синхронные fetch (`fetch_and_checkout_remote_branch`, `reset_local_branch_to_remote`) — в AsyncWorker; убрать `processEvents` из VM.
- [ ] Diff по файлу: `pathspec=path`; Full-document — лениво при переключении режима; расчёт diff'а из `CommitDetailPanel` перенести в VM.
- [ ] `_refresh_all_views` после мутаций — через async-путь; `ApplicationActive` — throttle (2–5 с).
- [ ] Приоритет веток (`_is_branch_reachable_from_head`) считать в VM на `graph_updated`, кэшировать `dict[name, priority]`; виджет не трогает pygit2.
- [ ] `stage_all`/`unstage_all` — batch с одним refresh; `_estimate_merge_size` — эвристика без полного diff или merge всегда async; auto-fetch status-проверка — в воркер.
- [ ] **Тесты:** клик по файлу не делает полнодеревный diff (шпион на `repo.diff` с pathspec); paintEvent без git-вызовов; refresh после коммита — через воркер.

### R3.3 — Windows-специфика (M1, H17, M19, M24) — M
**Файлы:** `src/core/operations.py:510, 585, 727, 1135, 1252, 1387, 1773, 1680-1683`, `src/ui/widgets/terminal_widget.py:231-265`.

- [ ] Все `subprocess.run`: `encoding="utf-8", errors="replace"`.
- [ ] Терминал: strict utf-8 → fallback `mbcs`/cp866; `chcp 65001` при старте шелла; ввод в OEM-кодировке; перезапуск после `exit` (`_process = None` в `_on_finished`).
- [ ] `pull()` без upstream — понятная ошибка вместо молчаливого `False`.
- [ ] **Тесты:** декодирование cp866-вывода; перезапуск терминала; pull без upstream → `error_occurred`.

### R3.4 — Диалог конфликтов и рендер (M17, M15, M16, M12, M20, M21) — M
**Файлы:** `src/ui/dialogs/conflict_resolution_dialog.py:71-74, 219-231`, `src/ui/widgets/graph_panel.py:1371-1450, 918-927, 1237-1240, 2401-2409`, `src/ui/widgets/left_panel.py:149-230`, `src/ui/widgets/commit_detail_panel.py:427, 802-816`.

- [ ] Бинарные конфликты: писать сырые bytes выбранной стороны, редактор гасить; fallback-декодирование cp1251; сохранять EOL оригинала.
- [ ] Bookkeeping `prev_occupied` для всех строк (рёбра у кромок viewport).
- [ ] Inline-редактор/popup: content-x → widget-x (`- _h_scrolls[0]`), закрытие/перемещение при скролле.
- [ ] `_hit_test_commit`: пропускать connector-строки (`sha == ""`), индекс арифметически.
- [ ] `LeftPanel`: сохранять expanded-set и selection при перестройке.
- [ ] `html.escape` для метаданных коммита; `Qt.PlainText` где разметка не нужна.
- [ ] **Тесты:** бинарный конфликт не пишет `"<binary>"` в файл; клик по connector-строке — no-op; expanded состояние переживает refresh.

**Критерий приёмки R3:** на синтетическом репо 5000 коммитов layout < 1с, клики по файлам и переключения веток без заметных фризов; русская консоль в терминале читаема.

---

## Этап R4 — Чистка и документация

Цель: код и документация не расходятся, мёртвый код удалён.

- [ ] Docstring'и: `revert()` (L, `operations.py:783`), `commit_changes` (:103), `stash_push(paths=)` (:1198), `refresh_state` (main_viewmodel.py:547) — привести в соответствие с кодом (или код с docstring — решить по смыслу). **S**
- [ ] Мёртвый код: `GraphWidget` (656 строк + тесты) — удалить или пометить legacy; `_ensure_clean`, `_in_fork`, `HEAD_SPECIAL_COLOR_INDEX`, параметры `_build_refs_map`, `parent_tree` в `_delta_status`. **S**
- [ ] Debug-`print` → `utils/debug_mode.py` или удалить (main_viewmodel.py:269-382, main_window.py:224-233); убрать Ctrl+Shift+D debug-shortcut из прода. **S**
- [ ] Аватар: одна реализация в `utils/avatar.py` (используется в трёх местах с разным видом). **S**
- [ ] Локальные импорты «avoids cycle» (~20 мест) — вынести наверх; `recently_created_changed` эмитить копией set; `fetch_changes` при busy — эмитить ошибку как остальные verbs. **S**
- [ ] Прочие low из REVIEW.md раздел 5: QSS `px`, `_is_valid_sha` (SHA-256/uppercase), case-sensitivity путей, `_blob_line_count` (trailing newline/binary), `_find_ssh_keygen` дубль, `SettingsDialog._on_generate_ssh`, невидимая кнопка в `FileListDelegate`, toast при resize. **M**
- [ ] Обновить `docs/IMPLEMENTATION_PLAN.md`: закрыть Этап 10; зафиксировать решения по исключениям из CommandProcessor в `docs/DEVELOPMENT_RULES.md`. **S**

---

## Сводная таблица

| Этап | Содержание | Находки | Оценка | Новых тестов (ориентир) |
|------|-----------|---------|--------|------------------------|
| R1 | Потеря данных, целостность репо | C1–C4, H1–H6, H11, H12, H20, M3, M5 | ~3–4 дня | ~25 |
| R2 | Ошибки, потоки, команды, Qt-жизненный цикл | C5–C7, H7–H10, H13–H16, M2, M4, M6–M10, M13, M14, M18, M22, M23 | ~4–5 дней | ~30 |
| R3 | Производительность, UX, Windows | P1–P7, H17–H19, M1, M11, M12, M15–M17, M19–M21, M24 | ~3–4 дня | ~20 |
| R4 | Чистка, документация | low-находки, docstring'и, мёртвый код | ~1 день | ~5 |

**Зависимости:** R1 не зависит ни от чего; R2.2 (потоки) желательно после R1 (меньше moving parts); R3.2 опирается на R2.2 (async-инфраструктура); R4 — в любой момент после R1.

**Риски:** R2.2 и R3.2 затрагивают async-архитектуру — самые рискованные изменения, делать отдельными commit'ами с полным прогоном UI-тестов; R1.5 (stash undo) меняет семантику — согласовать поведение до реализации.
