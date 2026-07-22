# Отчёт о глубоком ревью кодовой базы git-py

**Дата:** 2026-07-20
**Объём:** `src/` — ~22 000 строк (42 модуля), `tests/` — 1028 тестов
**Состояние на момент ревью:** `ruff check src/ tests/` — чисто; `pytest` — **1028/1028 проходят**
**Метод:** послойное ревью (core / viewmodels / ui), верификация ключевых находок живыми прогонами на pygit2 1.19.2. Все критические баги воспроизведены скриптами.

---

## 1. Общая оценка

Архитектурный каркас правильный и соответствует `docs/ARCHITECTURE.md`: core чист от PySide6, MVVM выдержан, Command pattern и доменные исключения применены системно, тестовая база объёмная, docstring'и детальные. Тем не менее найдено **7 критических багов** (часть — с потерей данных пользователя), ~20 высоких и ~30 средних проблем. Системные корни проблем:

1. **Edge-case'ы состояния HEAD/worktree недообработаны** — detached HEAD, unborn HEAD, untracked-файлы систематически приводят к багам.
2. **Гибрид pygit2 + git CLI без единой инвалидации состояния** — in-memory index pygit2 протухает после CLI-мутаций.
3. **Семантика undo неполна** — много команд с no-op/«best-effort» undo, что при redo даёт повторную мутацию, а при чужом состоянии — потерю чужих данных.
4. **Правило thread-safety libgit2 нарушено в собственном async-пути** — при том что в коде есть честный комментарий о нём.
5. **UI-поток блокируется** синхронными fetch, полнодеревными diff'ами на клик и git-обходами в paintEvent — на целевом репозитории «5000 коммитов» из TEST_PLAN приложение будет подвисать.

---

## 2. Критические баги (воспроизведены)

### C1. `merge X into Y` при HEAD ≠ Y портит репозиторий; undo не чинит
`src/core/operations.py:407-469` · вызовы: `src/ui/widgets/left_panel.py:1005-1023`, `src/ui/main_window.py:965-1026`, подменю «Merge X into...»

`merge_branch(source, target=...)` не делает checkout `target`: `r.merge(source_oid)` мёржит source в **текущий HEAD** (index + worktree пользователя), коммит создаётся с родителями `[HEAD_tip, source]`, при этом `create_commit("HEAD", ...)` двигает **текущую** ветку, а затем `refs/heads/{target}` тоже переводится на этот коммит. Воспроизведено: при HEAD=`feature`, merge `source → main` двигает обе ветки на коммит, чей первый родитель — старый tip `feature`, а история `main` осиротевает. `MergeCommand` видит, что `head.target` не изменился (HEAD был на feature... нет — изменился, т.к. `create_commit("HEAD")` сдвинул feature), — в любом сценарии undo через `reset(_previous_head)` восстанавливает только одну ветку, вторую (target) — нет.

Второй сценарий: remote-drop из left_panel — `fetch_and_checkout_remote_branch` переключает HEAD на свежий tracking branch, затем `merge_branch("feature", target="main")` даёт `UP_TO_DATE` → **молчаливый no-op** с видимостью успеха.

**Фикс:** в `merge_branch`, если `target != head.shorthand` — сначала `checkout_branch(target)` (или merge без checkout через `merge_commits` + запись ref). В `MergeCommand` захватывать `(target_ref, target_sha_before)` и откатывать именно target-ref. Интеграционный тест «merge X into Y при HEAD=Z» на реальном репо обязателен (сейчас drop-тесты мокают VM).

### C2. Untracked-файлы считаются «грязью»: checkout блокируется / FORCE-checkout падает с полуобновлённым деревом
`src/core/operations.py:222-252, 266-268, 294-324`

`_dirty_paths()` возвращает все записи `repo.status()`, включая untracked (`WT_NEW`). Воспроизведено:
- SAFE checkout с любым untracked-файлом возвращает `{"dirty_files": [...]}` — UI показывает предупреждение о грязном дереве там, где git молча переключил бы ветку.
- FORCE checkout (`strategy=GIT_CHECKOUT_FORCE` от вызывающего): `checkout_head(FORCE)` уже перезаписал tracked-файлы, затем post-verify видит untracked → `DirtyWorkTreeError`, а `_rollback_head` возвращает **только HEAD**. Worktree остаётся с содержимым чужой ветки при откаченном HEAD — пользователь видит фантомные изменения и ошибку.

**Фикс:** фильтровать `GIT_STATUS_WT_NEW`/`IGNORED` в `_dirty_paths` (pre-check и post-verify); rollback должен восстанавливать и worktree (`checkout_head(FORCE)` к прежнему дереву), а не только `set_head`.

### C3. Merge в detached HEAD → сырой `KeyError` наружу + полу-выполненный merge
`src/core/operations.py:407, 414, 466`

При detached HEAD `r.head.shorthand == "HEAD"`; `lookup_reference("refs/heads/HEAD")` бросает `KeyError`, который не ловится (except только `pygit2.GitError`) — нарушение правила 4. Воспроизведено: в diverged-сценарии `create_commit("HEAD", ...)` уже создал merge-коммит и сдвинул detached HEAD, и лишь потом lookup падает — пользователь получает трейсбек при фактически выполненном merge.

**Фикс:** в начале проверять `r.head_is_detached` и либо запрещать merge доменной ошибкой, либо двигать `HEAD` напрямую через `set_target`; lookup обернуть в `KeyError`.

### C4. `commit_changes(stage_all=True)` коммитит untracked-файлы вопреки docstring
`src/core/operations.py:103-105, 117`

Docstring: «Untracked files are *not* staged», но `r.index.add_all()` без pathspec добавляет и untracked (воспроизведено: коммит содержит `untracked.txt`). Кнопка «Commit» молча тащит build-артефакты в историю.

**Фикс:** `add_all` с pathspec'ами tracked-файлов из `status()` (исключая `WT_NEW`/`IGNORED`).

### C5. Сырой `pygit2.GitError` долетает до Qt-слота из самого горячего пути — `refresh_status`
`src/viewmodels/commit_panel_viewmodel.py:269-277` (также `:493`, `src/viewmodels/graph_viewmodel.py:129`)

`self._repo.repo.status()` — прямой вызов pygit2, а `except GitError` ловит только доменное исключение, от которого `pygit2.GitError` не наследуется. Битый index / удалённый `.git` → необработанное исключение в слоте. В `GraphViewModel._compute_graph` `repo.stash_list` стоит вне try и вырывается из контракта `(rows, err)`.

**Фикс:** ловить `(GitError, pygit2.GitError, OSError)` → `error_occurred`; `stash_list` перенести внутрь try.

### C6. Async-команды шарят main-thread `pygit2.Repository` с UI-потоком
`src/viewmodels/main_viewmodel.py:2550-2569`; читатели: `src/ui/widgets/graph_panel.py:1095-1138, 2452-2455`, `src/ui/widgets/left_panel.py:840-844`

Собственный комментарий в `load_repository_data` (:257-261): «libgit2 repositories are not thread-safe and sharing them can deadlock» — и там открывается отдельный `RepositoryManager` для воркера. Но `_run_async` исполняет `command.execute()` (merge/rebase/push/pull) на worker-потоке с **тем же** `RepositoryManager`, который UI-поток читает в `select_file`, `get_commit_details` и — хуже всего — в paintEvent (`_is_branch_reachable_from_head` делает `revparse_single`). Busy-флаг закрывает verb-методы, но не read-пути. Конкурентный доступ к одному `pygit2.Repository` из двух потоков — потенциальный краш libgit2.

**Фикс:** worker-owned `RepositoryManager` по `repo.path` внутри `_work` (как в `load_repository_data`); git-чтения из paintEvent убрать (см. P4).

### C7. Race: async-воркеры против смены репозитория и undo
`src/viewmodels/main_viewmodel.py:187-247, 564-576, 2269-2316, 2521-2569`

`set_repository()` не проверяет `_is_busy` и не отменяет воркеры: `_on_result` применит данные **старого** репо к новым панелям. `undo()`/`redo()` без busy-гварда — откат команды, пока worker мутирует репо. `CommandProcessor.execute()` вызывается с worker-потока — deque стеков мутируется из двух потоков. `clone_repository`: за время клона можно открыть другой репо — `_on_success` молча перезапишет его клоном.

**Фикс:** generation-токен/проверка пути в `_on_result`; busy-гвард на undo/redo и `set_repository` (или drain воркеров).

---

## 3. Высокий приоритет

### H1. `CommandProcessor`: команда теряется из обоих стеков при падающем undo/redo
`src/viewmodels/commands.py:122-139` — `pop()` делается **до** `command.undo()`; исключение (напр. `MergeConflictError` при redo конфликтного merge) → команда ни в undo, ни в redo. История необратимо теряет запись. **Фикс:** возвращать команду в исходный стек в `except`.

### H2. Redo первого коммита дублирует его
`src/viewmodels/commands.py:217-223` — при unborn HEAD `undo()` — no-op, но команда уходит в redo-стек; `redo()` → `execute()` → второй коммит с тем же сообщением. Общий класс бага: любой «silent no-op undo» + redo = повторная мутация (`CheckoutCommand` unborn, `MergeCommand` up-to-date). **Фикс:** реальный откат (reset на empty-tree) или не переносить в redo при no-op undo.

### H3. Undo stash-операций вытаскивает чужой stash
`src/viewmodels/commands.py:930-941, 1110-1115` — `StashPushCommand.undo()` делает `stash_pop(0)`, не сверяя `_pushed_oid`; чужой stash (CLI, другой клиент) будет применён в worktree и удалён. Ошибки глотаются `except Exception: pass`. **Фикс:** сверять `stash_oid_at(0)`, иначе искать oid по списку или падать.

### H4. `StashApplyCommand.undo()` уничтожает пользовательские изменения
`src/viewmodels/commands.py:1019-1038` — `git checkout HEAD -- .` откатывает весь worktree, включая изменения, бывшие ДО apply. Потеря данных под видом «отмены». **Фикс:** снапшот worktree-diff перед apply.

### H5. `StashPopCommand.undo()` дублирует изменения
`src/viewmodels/commands.py:988-996` — восстанавливает stash-запись, но не откатывает worktree → изменения и в worktree, и в stash. **Фикс:** undo = откат применённых путей + `restore_stash`.

### H6. `DiscardChangesCommand` — деструктивная команда с фальшивой отменяемостью
`src/viewmodels/commands.py:1251-1274` — `undo() = pass`, но команда занимает undo-стек → кнопка Undo активна и «срабатывает» впустую. По правилу 2 такие операции должны идти в обход процессора с gate на QMessageBox. Аналогично `PushCommand`/`FetchCommand` с no-op undo засоряют стек. **Фикс:** не пушить non-undoable команды в undo-стек (отдельный журнал).

### H7. Завершение конфликтного merge не попадает в undo-стек
`src/viewmodels/main_viewmodel.py:2428-2441` — `complete_merge` в `resolve_conflict` идёт в обход процессора → merge-коммит после ручного разрешения неотменяем, хотя бесконфликтный merge отменяем. **Фикс:** `CompleteMergeCommand` (undo = reset на pre-merge SHA из `_conflict_state`).

### H8. Busy-гвард непоследователен — две параллельные мутации возможны
`src/viewmodels/main_viewmodel.py` — нет проверки `_is_busy` в: `commit_changes` (:386), `undo`/`redo` (:564,:578), `cherry_pick` (:1794), `revert` (:1827), `create_branch` (:1440), `delete_branch` (:1498), `rename_branch` (:1574), `create_tag` (:1549), `resolve_conflict` (:2371), `stage_diff_line` (:435), `ignore_pattern` (:826), `delete_file_from_disk` (:844), `apply_stash_file(s)` (:2048,:2070), `checkout_remote_branch` (:1039). Коммит во время async rebase — прямой путь к порче состояния. **Фикс:** единый декоратор `_guard_mutation()` на все verb-методы.

### H9. Мутации в обход `CommandProcessor` (не из задокументированных исключений)
- `_move_branch_ref` (`main_viewmodel.py:1413-1438`) — перезапись `refs/heads/*` при FF без команды/undo;
- `delete_file_from_disk` (:844-858), `apply_stash_file(s)` (:2048-2114) — мутации worktree+index;
- `stage_file`/`unstage_file` (`commit_panel_viewmodel.py:290-351`) — при том что line-level staging идёт через `StageDiffLineCommand`. Неконсистентность с правилом 2.

**Фикс:** обернуть в `GitCommand` или задокументировать исключения в DEVELOPMENT_RULES.

### H10. `RepoTabViewModel.remove_tab` — неверный активный индекс
`src/viewmodels/repo_tabs_viewmodel.py:71-83` — tabs [a,b,c], active=1 (b), `remove_tab(0)` → активной становится **c** вместо b (обе ветки коррекции не срабатывают). Существующий тест покрывает только active=last. **Фикс:** `elif index < self._active_index: self._active_index -= 1`.

### H11. pygit2-кэш индекса протухает после CLI-мутаций
`src/core/operations.py:1134` (`unstage_changes`), `:1252` (`stash_push_staged`), `:1773` (`discard_file`), rebase-пути — после subprocess-вызовов `r.index` держит устаревший снимок; любой последующий `index.write()` затрёт результат CLI-операции (воспроизведено). **Фикс:** `r.index.read(force=True)` после каждого CLI-вызова, централизованно в `_run_git_in_workdir`.

### H12. `merge_branch` FF-путь двигает ref до checkout, без rollback
`src/core/operations.py:413-419` — `ref.set_target` + `head.set_target` до `checkout(SAFE)`; падение checkout → ref перемещён, worktree старый, весь incoming-дифф виден как «локальные изменения». **Фикс:** rollback `set_target` в except, либо checkout первым.

### H13. `BranchStackPopup` никогда не удаляется — утечка на каждый hover
`src/ui/widgets/graph_panel.py:2976, 3183-3230` — `WA_DeleteOnClose` явно `False`, `deleteLater()` не вызывается нигде; комментарий «popup schedules itself for deletion» — неверен. Каждый hover создаёт новый экземпляр, живущий вечно в `children()`. **Фикс:** `WA_DeleteOnClose(True)` + тест на `findChildren`.

### H14. `_branch_chip_rects` протухает при вертикальном скролле → клики по «чипам-призракам»
`src/ui/widgets/graph_panel.py:1697, 534, 2435-2442` — кэш rect'ов чистится только на `graph_updated`; после скролла клик по пустому месту может открыть меню/checkout чужой ветки. **Фикс:** чистить в `paintEvent`/`_on_scroll` или считать hit-test из `row_idx` арифметически.

### H15. Drop-меню в `LeftPanel` открывается в координатах viewport, а не экрана
`src/ui/widgets/left_panel.py:924` — `menu.exec(event.position().toPoint())` вместо `viewport().mapToGlobal(...)` (ср. корректный :493). Меню merge/rebase появляется со смещением.

### H16. Movable tabs + index-based кнопка закрытия → закрывается не тот репозиторий
`src/ui/widgets/repo_bar_widget.py:112, 44-47` — `setMovable(True)`, но `_CloseTabButton` хранит `_tab_index` с момента установки; после DnD-перестановки клик по × закрывает другую вкладку. Порядок VM не синхронизируется по `tabMoved`. **Фикс:** передавать `tabAt(btn.pos())` или путь, не индекс.

### H17. Терминал: кодировка консоли Windows (cp866/cp1251) не обрабатывается
`src/ui/widgets/terminal_widget.py:231-251` — `decode("utf-8", errors="replace")` превращает вывод русской `cmd.exe` в `����`; fallback `except UnicodeDecodeError` мёртв (`errors="replace"` не бросает); ввод кодируется utf-8, тогда как cmd ждёт OEM. **Фикс:** strict utf-8 → fallback `mbcs`/cp866; `chcp 65001` при старте; кодировать ввод соответственно.

### H18. Синхронный fetch на UI-потоке
`src/viewmodels/main_viewmodel.py:1140-1154` (`fetch_and_checkout_remote_branch`), `:1346` (`reset_local_branch_to_remote`) — сетевой fetch + `QApplication.processEvents` на UI-потоке: фриз на секунды, credential prompt — бессрочно. Прямое нарушение правила 3 (задокументировано как осознанный компромисс, но риск остаётся). **Фикс:** AsyncWorker с worker-owned manager, checkout в result-слоте.

### H19. Клик по файлу считает 2–3 полнодеревных diff'а синхронно
`src/viewmodels/commit_panel_viewmodel.py:513-522, 568-573, 605-612`; `src/ui/widgets/commit_detail_panel.py:688-752` — `repo.diff("HEAD")` по **всему** дереву без pathspec, потом выбор одного файла; плюс diff с context=2³¹−1 (Full document) сразу, плюс третий cached-diff. На репо с тысячами грязных файлов — секундный фриз на клик. Плюс `CommitDetailPanel` сам ходит в pygit2 — нарушение пассивности виджета. **Фикс:** `pathspec=path`, lazy full-document, расчёт в VM.

### H20. Деструктивные действия без подтверждения
`src/ui/widgets/commit_panel.py:408-417, 527-528, 772-791` — «Discard All Changes» (одна кнопка!), batch-«Discard N Files», «Delete File» (удаление с диска) — мгновенно, без диалога. При этом stash drop / delete branch / reset-local — с подтверждением. **Фикс:** `QMessageBox.question` (единый confirm-helper).

---

## 4. Средний приоритет

### Производительность
- **P1. O(n²) в `build_graph`** — `src/core/graph_v2.py:640-642, 531-535, 825-827`: линейные `any(n.commit.sha == ...)` по уже обработанным узлам на каждый коммит×родителя. Замер: 5000 линейных коммитов — 0.80с, ветвистых — 0.96с (требование «< 1с» — впритык, на слабом железе не выдерживается). Perf-тест покрывает только 500 коммитов (`tests/core/test_graph_v2.py:1620`). **Фикс:** `sha→node` dict; perf-тест на 5000.
- **P2. История молча ограничена 500 коммитами** — `src/core/repository.py:381` (default `max_count=500`), `src/viewmodels/graph_viewmodel.py:121,263`. Граф и поиск не видят ничего старше 500, без индикации усечения. **Фикс:** «Load more»/индикатор.
- **P3. Синхронный `_refresh_all_views` после каждой команды и на каждый Alt-Tab** — `main_viewmodel.py:2491-2495, 530-562`, `main_window.py:1259-1274`. Полный `get_all_history` + layout + status на UI после каждого commit/checkout/undo и на `ApplicationActive`. **Фикс:** async-путь (`load_repository_data`) после мутаций + throttle на активизацию.
- **P4. Git-обход в paintEvent** — `graph_panel.py:1054-1139, 1606-1608`: `_is_branch_reachable_from_head` — до 256 revparse-хопов на ветку на строку на каждый repaint + полное перечисление `repo.branches` заново. **Фикс:** считать приоритет один раз на `graph_updated` в VM, кэшировать.
- **P5. `stage_all_unstaged`/`unstage_all_staged` O(n²)** — `main_viewmodel.py:473-495`: полный `refresh_status` на каждый файл. **Фикс:** batch-вариант с одним refresh.
- **P6. `_estimate_merge_size` — синхронный diff всего дерева на UI** — `main_viewmodel.py:2497-2519`: пред-проверка «быть ли merge async» фризит сильнее самого fast-path. **Фикс:** эвристика без полного diff или всегда async.
- **P7. `copy_files_diff` — полный `repo.diff("HEAD")` на каждый файл** — `main_viewmodel.py:706-739, 951-971`.

### Ошибки и состояние
- **M1. `subprocess.run(text=True)` без `encoding=`** — `operations.py:510, 585, 727, 1135, 1252, 1387, 1773`: на Windows вывод git (UTF-8) читается в locale-кодировке → mojibake в сообщениях пользователю, потенциальный `UnicodeDecodeError`. **Фикс:** `encoding="utf-8", errors="replace"`.
- **M2. Неудачный `open()` молча оставляет старый репозиторий открытым** — `repository.py:122-126`: при исключении `_repo`/`_path` остаются от предыдущего repo (воспроизведено); `clone()` корректно обнуляет, `open()` — нет.
- **M3. `commit_changes` запрещает первый коммит** — `operations.py:114-115`: unborn HEAD → `GitError`, хотя `init()` создаёт именно такой репо. **Фикс:** разрешить `parents=[]` при unborn.
- **M4. `tags` property — сырой `KeyError` при битом ref** — `repository.py:267`.
- **M5. `add_all()`/`index.write()` вне try** — `operations.py:117-118`: lock-конфликт → сырой `pygit2.GitError`.
- **M6. Redo конфликтующих merge/pull не выставляет conflict_state** — `commands.py:568-584` + `main_viewmodel.py:578-590`: репо в конфликте, UI не знает. Плюс redo Fetch/Pull/Push — сетевой вызов синхронно на UI.
- **M7. `CherryPickCommand.undo`/`RevertCommand.undo` сносят чужой staged** — `commands.py:663-669, 691-694`: `reset --mixed` откатывает и то, что пользователь staged до cherry-pick.
- **M8. `AsyncWorker`: autoDelete + потеря типа исключения** — `utils/async_worker.py:30-49`: пул удаляет C++-объект, пока Python-ссылки в `_active_workers` → риск `RuntimeError`; `failed.emit(str(exc))` стирает тип → `_on_async_failed` угадывает конфликт по `is_merge_in_progress`. **Фикс:** `setAutoDelete(False)`, передавать тип.
- **M9. Двойной полный refresh → мерцание** — `main_viewmodel.py:1157→1211, 1346→1347`.
- **M10. Неограниченный undo-стек + тяжёлые снапшоты** — `commands.py:111-112`: `deque` без maxlen; `DiscardFileCommand` хранит bytes-бекапы файлов → память растёт бесконечно. **Фикс:** `deque(maxlen=N)`.
- **M11. `_on_auto_fetch_tick` — синхронный `repo.status()` на UI каждые 60с** — `main_viewmodel.py:2364-2368`, плюс `except Exception: pass`.

### UI
- **M12. Клик по connector-строке выбирает пустой SHA** — `graph_panel.py:2401-2409` + `graph_viewmodel.py:171-182`: `commit_selected("")` → RightPanel показывает пустой detail; hit-test — O(n) обход вместо арифметики.
- **M13. После busy не восстанавливаются Close / Undo / Redo** — `main_window.py:1302-1327`: `_on_busy_changed(False)` реанимирует только часть actions; после **неуспешной** async-операции Undo навсегда выключен при `can_undo=True`.
- **M14. Утечка соединений в `_open_remote_manage_dialog`** — `main_window.py:1586-1590`: `stack_changed.connect(lambda...)` без disconnect; N открытий → N вызовов на мёртвых диалогах.
- **M15. Обрезанные рёбра графа на краях viewport** — `graph_panel.py:1371-1450`: `continue` для off-screen строк пропускает bookkeeping → разрыв pipe'ов у кромок.
- **M16. Inline-редактор и popup не следуют за скроллом / неверный x при h-scroll** — `graph_panel.py:918-927, 1237-1240` (content-x вместо widget-x) и `_on_scroll` не закрывает их.
- **M17. Диалог конфликтов: бинарные файлы → `"<binary>"` пишется в файл; CRLF теряется** — `conflict_resolution_dialog.py:71-74, 219-231`: при `UnicodeDecodeError` возвращается строка `"<binary>"`, которая затем записывается в файл — разрешение бинарного конфликта уничтожает файл; cp1251-файлы считаются бинарью; `QPlainTextEdit` нормализует `\r\n→\n`.
- **M18. `load_config` падает на валидном не-dict JSON; `save_config` не атомарен** — `utils/config.py:121, 126-131`: `**json.load(f)` бросает неперехваченный `TypeError` на `42`/`[]` → приложение не стартует; запись поверх файла → обрыв = потеря всех настроек. **Фикс:** `isinstance(data, dict)`; tmp + `os.replace`.
- **M19. Терминал не перезапускается после смерти шелла** — `terminal_widget.py:162-163, 259-265`: `exit` убивает терминал до смены репозитория.
- **M20. Перестройка `LeftPanel` теряет expanded/selection** — `left_panel.py:149-230`: каждый `references_changed` → `clear()` + ребилд.
- **M21. HTML-инъекция через метаданные коммита** — `commit_detail_panel.py:427, 802-816`: author/subject вставляются в HTML без `html.escape`.
- **M22. `CommitDetailPanel.error_occurred` не подключён** — `commit_detail_panel.py:174, 710`: ошибки diff уходят в никуда (пустой diff). MainWindow/RightPanel лезут в `_private` члены друг друга.
- **M23. `QTimer.singleShot(0, _do_scroll)` со stale-курсором** — `diff_view_widget.py:1088-1092`: скролл прыгнет на чужую строку при смене файла до срабатывания таймера.
- **M24. `pull()` тихо возвращает `False` при отсутствии upstream** — `operations.py:1680-1683`, плюс `head.shorthand` при detached = «HEAD».
- **M25. `clone_repository` race** — см. C7.

---

## 5. Низкий приоритет (выборка)

- **Debug-`print` в проде:** `main_viewmodel.py:269-382`, `main_window.py:224-233` — есть `utils/debug_mode.py`, но не используется.
- **Аватар продублирован трижды** (`utils/avatar.py`, `graph_panel.py:1988-2055`, `graph_widget.py:521-589`) с визуально разными результатами; docstring avatar.py неверен.
- **`GraphWidget` — мёртвый код:** 656 строк + тесты; main_window использует `GraphTableWidget`, docstring main_window.py:16 устарел.
- **Docstring-противоречия:** `revert()` (:783-784 — «creates a new commit», коммит не создаётся), `commit_changes` (:103), `stash_push(paths=)` (:1198 vs :1226 — противоречат друг другу, риск потери данных при вызове с paths).
- **Мёртвый код core:** `_ensure_clean` (:85), `_in_fork` (graph_v2.py:331-376), `HEAD_SPECIAL_COLOR_INDEX` (:99), параметры `_build_refs_map` (:1535), `parent_tree` в `_delta_status` (repository.py:55).
- **`_is_valid_sha`** (graph_v2.py:1579) — только 40 строчных hex: SHA-256-репозитории и uppercase отфильтруются.
- **Case-sensitivity путей на Windows:** `delta.new_file.path == path` (repository.py:577, operations.py:1088) — «Src/f.py» ≠ «src/f.py».
- **QSS без `px`** (theme.py:221-222) — игнорируется Qt.
- **Глушение исключений:** `except Exception: continue/pass` в `branches` (repository.py:208,223), `stash_list` (:308), `show_in_folder` Windows-only `explorer` (main_viewmodel.py:876,913).
- **`recently_created_changed.emit(self._recently_created_branches)`** — эмит живого set (main_viewmodel.py:226): подписчик может мутировать состояние VM.
- **`SettingsDialog._on_generate_ssh`** (settings_dialog.py:151-164) — после закрытия SshKeyDialog всегда открывает второй генератор.
- **`_find_ssh_keygen`** продублирован в двух диалогах с двойным subprocess-вызовом.
- **Локальные импорты «avoids cycle»** (~20 мест в main_viewmodel.py) — цикла нет, вынести наверх.
- **VM тянет `QApplication.processEvents`** (main_viewmodel.py:619-620 и др.) — реентерабельный event loop внутри verb-метода.
- **`fetch_changes` при busy молча выходит** (:2203-2204) — остальные verbs эмитят ошибку.
- **rebase передаёт `upstream` без `--`** (operations.py:511) — имя с `-` будет флагом.
- **Ctrl+Shift+D debug-shortcut в проде** (graph_panel.py:418-419); иконка ⛄ для local-ветки в popup (:3270).
- **Невидимая кнопка кликабельна** в `FileListDelegate.editorEvent` (file_list_model.py:259-277).

---

## 6. Консистентность с правилами проекта (`DEVELOPMENT_RULES.md`)

| Правило | Статус | Нарушения |
|---|---|---|
| core без PySide6 | ✅ | Только упоминания в docstring |
| Мутации через `GitCommand`/`CommandProcessor` | ⚠️ | `_move_branch_ref`, `delete_file_from_disk`, `apply_stash_file(s)`, `stage_file`/`unstage_file` (H9); `complete_merge` при разрешении конфликта (H7); `DiscardChangesCommand` с no-op undo в стеке (H6) |
| Сетевые/долгие операции в QThread | ⚠️ | Синхронный fetch ×2 (H18), redo сетевых команд на UI (M6), `_estimate_merge_size` (P6), auto-fetch status (M11) |
| Ошибки только через `error_occurred` | ⚠️ | C5 (refresh_status), C3 (KeyError в merge), M3/M4/M5 (raw исключения из core), M22 (неподключённый сигнал) |
| Виджеты пассивны | ⚠️ | `CommitDetailPanel` ходит в pygit2 (H19), `_is_branch_reachable_from_head` — git-обход в виджете (P4) |
| VM-методы — глаголы | ✅ | |
| Суффиксы Widget/Panel/Dialog | ✅ | |
| Конфигурация через `utils/config.py` | ✅ | С оговоркой M18 |
| Destructive ops в обход undo + QMessageBox | ⚠️ | `reset_local_branch_to_remote` — ✅; но Discard All / Delete File без подтверждения (H20), `StashApplyCommand.undo` деструктивен внутри «отменяемой» команды (H4) |

**Документация расходится с кодом** (опаснее её отсутствия): `revert()`, `commit_changes()`, `stash_push(paths=)`, `refresh_state` (обещает перехват `OSError` — не делает), комментарий про удаление popup (H13).

---

## 7. Пробелы в тестах

Все критические баги живут в непокрытых областях:

1. Нет интеграционного теста «merge X into Y при HEAD=Z» на реальном репо (drop-тесты мокают VM) — **C1**.
2. Нет тестов checkout с untracked-файлами (SAFE и FORCE) — **C2**; merge/checkout в detached HEAD — **C3**.
3. Нет теста семантики untracked в `commit_changes` — **C4**.
4. Нет теста на сырой `pygit2.GitError` из `refresh_status` (удалить `.git/index` между refresh) — **C5**.
5. `async_enabled=True` покрыт только для remotes/merge/rebase; нет тестов гонок: undo-во-время-async, `set_repository` во время загрузки — **C6/C7**.
6. Нет тестов redo-повторов: first-commit redo-дубликат (H2), stash pop/apply undo-семантика (H3-H5), non-undoable контракт Discard (H6).
7. `remove_tab` с active в середине списка не покрыт — **H10** прошёл бы незамеченным.
8. Perf-тест только на 500 коммитов вместо заявленных 5000 — **P1/P2**.
9. Нет тестов: уничтожение `BranchStackPopup` (H13), инвалидация chip-rects при вертикальном скролле (H14), movable-tabs + close (H16), кодировка терминала (H17), восстановление actions после busy (M13), утечка соединений remote-dialog (M14), бинарный конфликт (M17), корруптный конфиг с не-dict JSON (M18), `get_commit_changes`/`get_commit_diff_text`/`apply_file_from_stash` (core API без покрытия), unicode/кириллические пути.

---

## 8. Рекомендуемый порядок исправления

**Волна 1 — потеря данных и целостность репозитория:**
1. C1 (merge X into Y) + интеграционный тест
2. C2 (checkout/untracked) + C3 (detached merge)
3. C4 (untracked в commit)
4. H4, H5, H3 (stash undo), H1 (процессор теряет команду), H12 (FF без rollback), H11 (index cache)

**Волна 2 — стабильность UI:**
5. C5 (raw exceptions в refresh), C6+C7 (потоки и race), C2-верификация через тесты
6. H6, H7, H8, H9 (консистентность команд и busy-гвард), H2, H10
7. H13, H14, H15, H16, M13, M14 (утечки и жизненный цикл Qt)

**Волна 3 — производительность и UX:**
8. P1-P4, M1 (encoding), H17 (терминал), H20 (подтверждения), P2 (лимит 500)
9. Добивка тестами пробелов из раздела 7; чистка low-находок (debug print, мёртвый GraphWidget, docstring'и)
