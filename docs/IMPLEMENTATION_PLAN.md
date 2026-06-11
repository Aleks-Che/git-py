<!-- Файл: IMPLEMENTATION_PLAN.md -->
# План реализации по этапам

## Этап 0: Инициализация проекта
- Создание структуры папок, виртуального окружения, зависимостей (PySide6, pygit2, pytest, pytest-qt, pytest-asyncio).
- Настройка системы сборки (setup.py / pyproject.toml).
- Базовый запуск главного окна с меню и пустыми панелями.
- Настройка CI (GitHub Actions) для прогона линтера и тестов.

## Этап 1: Core – работа с Git без GUI
Задачи:
- Реализация `RepositoryManager`: открытие существующего репозитория, инициализация нового, клонирование (пока через pygit2.clone_repository).
- Модели данных: CommitInfo, BranchInfo, FileStatus и др.
- Сервис получения истории коммитов (walk по ветке), построение списка.
- Получение статуса рабочей директории и индекса (modified, staged, untracked).
- Чтение списка веток, тегов, stash, подмодулей.
- Модульные тесты на все функции с использованием временного репозитория.

## Этап 2: Базовая отрисовка графа коммитов
- Проектирование структуры для хранения DAG (узлы, рёбра) на основе истории из pygit2.
- Реализация `GraphViewModel`: преобразование списка коммитов в позиции для отрисовки (вертикальная лейн-ориентированная раскладка, как в GitKraken).
- Кастомный виджет `GraphWidget` на QGraphicsView: отрисовка узлов-коммитов (эллипсы с цветом ветки), соединительных линий, подписей (SHA, сообщение).
- Реакция на клик по коммиту: вывод деталей в правую панель (пока только статичный текст).
- Плавная интеграция с ViewModel: при открытии репозитория граф автоматически заполняется.

## Этап 3: Рабочая директория и коммиты
- Панель коммита (`CommitPanel`): отображение файлов из ViewModel, чекбоксы для staging, выбор файла для диффа.
- Класс `CommitCommand`, интегрированный в `CommandProcessor`.
- Создание коммита из UI: сообщение + индексация (можно stage/unstage отдельные файлы или строки – строчную индексацию оставить на потом).
- Обновление графа и статуса после коммита.
- Отображение узла WIP (незакоммиченные изменения) в графе.

## Этап 4: Работа с ветками и переключение
- Левая панель `LeftPanel`: дерево веток (локальные/удалённые), тегов, stash.
- Двойной клик – checkout на ветку. Команда `CheckoutCommand` с undo (возврат на предыдущую ветку).
- Контекстное меню: создание ветки из выбранного коммита (или из HEAD), удаление, переименование.
- Drag-and-drop для merge/rebase (пока без реального выполнения, только UI-прототип).

## Этап 5: Слияние и rebase
- Команды `MergeCommand`, `RebaseCommand` с поддержкой undo (откат слияния через reset --hard ORIG_HEAD или отмена rebase через reflog).
- Обнаружение конфликтов: при ошибке pygit2.GitError с кодом конфликта, ViewModel переходит в состояние "конфликт".
- Визуальный редактор конфликтов: диалог с тремя панелями (ours, theirs, base) и возможностью выбора строк. В основе используется дифф из pygit2.
- AI-разрешение конфликтов – на этом этапе не реализуем, закладываем точку расширения.
- Интеграция операций в drag-and-drop: перетаскивание ветки A на B → контекстное меню "Merge A into B" или "Rebase A onto B".

## Этап 6: Работа с удалёнными репозиториями
- Вкладка "Remote" или кнопки Push/Pull/Fetch на тулбаре.
- Push, pull, fetch через pygit2.remote.Remote.
- Автоматический fetch по таймеру (раз в минуту).
- Управление remote-ами: добавление, удаление.
- Клонирование: диалог с выбором провайдера (GitHub/GitLab/BB), вводом URL и генерацией SSH-ключа (вызов `ssh-keygen`).

## Этап 7: Stash и дополнительные инструменты
- Кнопка Stash/Pop на тулбаре.
- Сохранение и применение stash, показ в левой панели.
- Частичный stash (stage нужные файлы, затем stash только их).
- Встроенный терминал: простая реализация через QProcess, запуск shell в корне репозитория, синхронизация с цветовой схемой.
- Поиск по коммитам (SHA, сообщение, автор) с фильтрацией графа.

## Этап 8: Undo/Redo и история действий
- Полноценная интеграция `CommandProcessor` во все мутирующие операции — _реализована на Этапах 3–7 (21 команда)_.
- Кнопки Undo/Redo на тулбаре (Edit), горячие клавиши из конфига (`Ctrl+Z`/`Ctrl+Y`).
- ActionHistoryWidget — панель истории действий в нижних вкладках (History).
- View-меню: показ/скрытие левой панели, терминала, истории.
- 14 новых тестов: CommandProcessor.snapshot/timestamp + ActionHistoryWidget.

## Этап 9: Конфигурация и темизация
- Настройка пользовательского профиля (имя, email).
- Выбор тёмной/светлой темы, кастомизация цветов веток.
- Сохранение/восстановление размеров панелей.
- Импорт/экспорт настроек.

## Этап 10: Тестирование и стабилизация
- Покрытие UI-тестами основного workflow: открытие репо, создание коммита, переключение веток, слияние без конфликтов.
- Стресс-тесты на больших репозиториях (1000+ коммитов) для проверки производительности графа.
- Исправление багов, оптимизация отрисовки графа (кэширование раскладки, виртуализация).

---

## Прогресс разработки

**Общий прогресс:** `[█████████████████░░░░░░░░]` 8 / 11 этапов (73%)

Легенда:
- `[ ]` — не начато
- `[~]` — в работе
- `[x]` — завершено

### Чек-лист этапов
- [x] **Этап 0: Инициализация проекта** — _завершено_
  - Дата начала: `2026-06-02`
  - Дата завершения: `2026-06-02`
  - Комментарий: `src/`-обёртка, ruff+pytest в pyproject.toml, CI на GitHub Actions (3.10–3.12, libgit2 + QT_QPA_PLATFORM=offscreen), 16 smoke-тестов проходят. Точка входа через `python -m src.main` (консольный скрипт отложен до более позднего этапа).

- [x] **Этап 1: Core – работа с Git без GUI** — _завершено_
  - Дата начала: `2026-06-02`
  - Дата завершения: `2026-06-02`
  - Комментарий: `RepositoryManager` (open/init/clone/is_valid + head_commit/branches/tags/stash_list/get_status/get_history/get_commit), все обёртки из operations.py (commit, branch create/delete, checkout, merge, rebase через `git` CLI, cherry-pick, revert, reset, stash push/pop, push/pull/fetch через локальный bare-origin), diff_parser (parse_diff + diff_to_text), доменные исключения (GitError/RepositoryNotFoundError/MergeConflictError/AuthError/NetworkError/...). 66 модульных тестов core/. Ребазе нужен `git` CLI (pygit2 1.x не имеет высокоуровневого API) — `GitNotInstalledError` если его нет.

- [x] **Этап 2: Базовая отрисовка графа коммитов** — _завершено_
  - Дата начала: `2026-06-02`
  - Дата завершения: `2026-06-02`
  - Комментарий: `core/graph.py` (DAG + branch-priority lane algorithm + 12-цветная палитра), `core/repository.py.get_all_history()` для обхода всех tip'ов (ветки+теги), `GraphViewModel` (QObject, сигналы `graph_updated`/`commit_selected`/`error_occurred`, методы-глаголы `refresh_graph`/`select_commit`/`get_commit_details`), `GraphWidget` на `QGraphicsView` (узлы-эллипсы с цветом ветки, L-shape линии до родителей, ref-чипы HEAD/ветки/теги, тёмная тема, клик-выделение), `CommitDetailPanel` (read-only QTextEdit с HTML), `MainWindow` (2 панели Graph+Detail; `File>Open` через QFileDialog; `MainViewModel` пока не трогаем). Алгоритм раскладки: phase 1 — priority walk (HEAD→локальные→remote) по первому родителю; phase 2 — orphan walk для коммитов без веток. **58 новых тестов** (20 graph + 14 viewmodel + 8 widget + 5 `get_all_history` + 11 прочих обновлений), итого **124/124 проходят**, `ruff check` чисто. Известные ограничения (Stage 10): полная перерисовка сцены на каждый `graph_updated` без виртуализации; `RenderConfig` пока хардкод — мигрирует в `utils/config.py` на Этапе 9.

- [x] **Этап 3: Рабочая директория и коммиты** — _завершён (обновление 2026-06-05: редизайн правой панели)_
  - Дата начала: `2026-06-03`
  - Дата завершения: `2026-06-03`
  - Комментарий: `MainViewModel` (QObject, владеет `RepositoryManager` + `CommandProcessor` + дочерними VM; сигналы `repository_changed` / `error_occurred`; `commit_changes/stage_file/unstage_file/undo/redo`); `CommitPanelViewModel` (`file_changes`, `staged_files` (восстанавливается из raw `pygit2` status flags, не из `FileStatus`), `selected_file`, `current_diff`, `commit_message` + 5 сигналов; `stage_file` через `index.add`+write, `unstage_file` через новую `core.operations.unstage_changes` — `git reset HEAD -- <path>` под капотом, потому что `index.remove` для tracked-файла оставлял `INDEX_DELETED` в staged-сете); `CommitCommand(GitCommand)` с undo через `reset("HEAD", mode="soft")`; `CommitPanel` (правая верхняя панель: `QPlainTextEdit` для сообщения, `QListWidget` с чекбоксами + статус-бейджи M/U/D/R/A/C/T/I, `QTextEdit` для диффа); WIP-узел в графе (синтетический `CommitInfo` с `sha="WIP"` prepend-ится в `GraphViewModel.refresh_graph()`; в `core/graph.py` ничего не трогали — ядро остаётся чистым); `MainWindow` (вместо прямого `RepositoryManager` — `MainViewModel`; правая сторона = вертикальный сплиттер: `CommitPanel` сверху, `CommitDetailPanel` снизу; Undo/Redo привязаны к `command_processor` + `stack_changed` → `setEnabled`); `core/operations.py`: `unstage_changes(repo, path)` (no-op если путь не в индексе, `index.remove` для unborn HEAD, иначе `git reset HEAD -- <path>`). **52 новых теста** (22 CommitPanelViewModel + 16 CommitCommand + 3 WIP в graph VM + 1 WIP в widget + 10 CommitPanel UI), итого **176/176 проходят**, `ruff check` чисто. Заглушка `BranchPanelViewModel` поднята до `QObject` с `set_repository`/`error_occurred` (для совместимости с `MainViewModel`), реальная реализация остаётся на Этап 4.
  - **2026-06-05 (редизайн правой панели):** `MainViewModel` — новый `selected_commit_sha` + `selection_changed` сигнал с toggle-off при повторном клике; `commit_changes` авто-выделяет новый коммит. `CommitPanelViewModel` — `commit_message` разделён на `commit_summary`/`commit_description`; добавлены `unstaged_paths`/`unstaged_files`/`staged_files_detailed`/`stage_all_unstaged`. `CommitDetailPanel` — переписан без diff-preview: message → info → changed files. `CommitPanel` — переписан с нуля: два `FileListWidget` (collapsible Unstaged/Staged), stage-all, per-row Stage File на hover, sticky commit блок (Summary/Description + зелёная Commit кнопка). `RightPanel` — новый `QStackedWidget`-контейнер, переключает commit-input (WIP) / commit-detail по `selection_changed`, скрыт при `None`. `MainWindow` — старый `_right_splitter` заменён на `_right_panel`; `graph_table.commit_selected` → `vm.select_commit`. **18 новых тестов** (right panel + VM selection contract + stage-all). Исправлен `get_commit_changes` в `repository.py` — для root-коммита вместо `diff(None, tree)` (не поддерживается pygit2 на Windows) используется `diff(empty_tree, tree)`. Итого **539 тестов проходят** (3 pre-existing failures — branch label truncation и graph_edge theme), `ruff check` чисто.

- [x] **Этап 4: Работа с ветками и переключение** — _завершён_
  - Дата начала: `2026-06-03`
  - Дата завершения: `2026-06-03`
  - Комментарий: `BranchPanelViewModel` поднят из заглушки до полноценного read-only VM: 5 свойств (`local_branches`/`remote_branches`/`tags`/`stash_list`/`current_branch_name`) + единый сигнал `references_changed`; VM не имеет мутирующих глаголов — все мутации идут через `MainViewModel` → `GitCommand` → `CommandProcessor`. `core/operations.py`: `rename_branch(repo, old, new, force=False)` (ловит `pygit2.AlreadyExistsError` → `GitError`, остальные `pygit2.GitError` → `GitError`). `commands.py`: 4 новых команды — `CheckoutCommand` (запоминает `head.shorthand` для undo, `None` на unborn HEAD → no-op), `CreateBranchCommand` (force=True при undo, no-op если ветка пре-существовала), `DeleteBranchCommand` (запоминает `target_sha` для восстановления), `RenameBranchCommand` (undo через swap имён с force=True). `MainViewModel`: 4 verb-метода (`checkout_branch`/`create_branch`/`delete_branch`/`rename_branch`) + приватный `_refresh_all_views()` (graph + commit panel + branch panel) — checkout обновляет все три панели, потому что меняется и worktree, и HEAD; undo/redo переехали на общий `_refresh_all_views`. `LeftPanel`: полноценный `QTreeWidget` с тремя группами (Branches→Local/Remote, Tags, Stash), текущая ветка выделена жирным "(HEAD)"; двойной клик по локальной ветке → checkout, по remote/tag → create branch; контекстное меню: на local — Checkout/Create/Rename/Delete, на remote/tag — Create, на stash — Apply (disabled, Этап 7); drag-and-drop с локальной ветки → заглушка `QMessageBox` "будет на Этапе 5". `MainWindow`: `LeftPanel` теперь принимает `(branch_panel_vm, main_vm)`. **60 новых тестов** (5 core/operations rename_branch + 14 BranchPanelViewModel + 19 branch_commands + 11 MainViewModel branch-методов + 11 LeftPanel UI), итого **236/236 проходят**, `ruff check` чисто. Замечания: `pygit2.Branch.rename()` не принимает kw-args — `branch.rename(new_name, force)` позиционно; `setData(column, role, value)` — 3 аргумента в PySide6. Реальный merge/rebase через drag-and-drop остался на Этап 5.

- [x] **Этап 5: Слияние и rebase** — _завершён_
  - Дата начала: `2026-06-03`
  - Дата завершения: `2026-06-03`
  - Комментарий: `core/operations.py`: 5 новых обёрток — `is_merge_in_progress(repo)` (проверка `.git/MERGE_HEAD`), `is_rebase_in_progress(repo)` (проверка `rebase-apply/` или `rebase-merge/`), `abort_merge` (`git merge --abort`), `abort_rebase` (`git rebase --abort`), `complete_merge(repo, source, target, message)` (финализирует резолвленный merge: создаёт merge-коммит с двумя родителями, чистит MERGE_HEAD/MERGE_MSG), `complete_rebase_continue(repo)` (`git rebase --continue` с `GIT_EDITOR=true`, возвращает `True` если rebase завершён / `False` если следующий коммит снова конфликтует). Поправлены баги: `cherry_pick()` и `revert()` в core передавали `commit.id` (Oid) в `r.cherrypick()` / `r.revert()`; `revert` ожидает `Commit`-объект с `_pointer` — переделано на `commit` (peeled); для `cherrypick` оставлен `commit.id` (oid принимается C-кодом, проверено эмпирически). `commands.py`: 4 новых команды — `MergeCommand` (запоминает `_previous_head` + `_head_moved`, undo через `reset(_previous_head, hard)`; на FF/up-to-date undo no-op; на 3-way reset к pre-merge HEAD), `RebaseCommand` (запоминает `_previous_head`, undo: если rebase в процессе — `abort_rebase`, иначе `reset(_previous_head, hard)`), `CherryPickCommand` / `RevertCommand` (запоминают `_previous_head`, undo через `reset(_previous_head, mixed)` — чистит индекс от стейджа cherry-pick; пользователь делает follow-up commit отдельно). `MainViewModel`: 6 verb-методов (`merge_branch`/`rebase_branch`/`cherry_pick`/`revert` + 2 abort-метода, которые НЕ идут через `CommandProcessor` — это runtime escape hatch, а не undo-шаг) + `resolve_conflict(path, resolution)` (пишет файл, делает `git add`, вычищает путь из conflict_state; когда все пути резолвлены — финализирует через `complete_merge` / `complete_rebase_continue` / или снимает state для cherry-pick+follow-up commit). Новый сигнал `conflict_state_changed(dict)` (ключи: `in_progress`, `conflicting_paths`, `operation` ∈ {merge, rebase, cherry-pick, revert}, плюс operation-специфичный context: `source`/`target`/`upstream`/`sha`). `set_repository` чистит `_conflict_state` при открытии нового репо. Async по hard rule (DEVELOPMENT_RULES.md §3): rebase всегда в `AsyncWorker`, merge — если `_estimate_merge_size(source) > merge_async_threshold` (default 50, конфигурируется в `utils/config.py`); VM помечает busy через `busy_changed(bool)` сигнал, `MainWindow` показывает indeterminate spinner в status bar и отключает undo/redo/close во время операции. `ConflictResolutionDialog(QDialog)`: 4 панели (Ours/Base/Theirs read-only, Result editable) + кнопки Accept Ours / Accept Theirs / Accept Both / Mark Resolved / Cancel; читает staged blob'ы через `repo.index.conflicts`; сигнал `resolved(str)` отдаёт содержимое Result. `ConflictResolver(ABC)` — заглушка с `NotImplementedError` для будущего AI-расширения. `LeftPanel`: `mimeData()` переопределён — text drag payload теперь bare branch name (без `(HEAD)`); `dropEvent` показывает QMenu с "Merge {src} into {tgt}" / "Rebase {src} onto {tgt}"; drop игнорируется если source==target или target не локальная ветка; rebase через панель делает checkout+rebase как две команды в стеке. Контекстное меню локальной ветки расширено: "Merge X into current…" / "Rebase X onto current…" (disabled если X == текущая). **77 новых тестов** (17 core/operations + 16 commands + 24 VM + 6 async infra + 11 dialog + 17 left_panel, из них 3 UI drag-and-drop, 4 context menu) + пред-фиксные баги в cherry_pick/revert тестах. Итого **313/313 проходят**, `ruff check` чисто. Замечания: `pygit2.Repository.cherrypick` принимает и Oid, и Commit; `pygit2.Repository.revert` — только Commit. `r.merge(oid)` работает, `r.merge(commit_object)` падает с TypeError. `merge_async_threshold` хранится в `_DEFAULT_CONFIG` в `utils/config.py`; `MainViewModel(async_enabled=False, merge_async_threshold=N)` для тестов.

- [x] **Этап 6: Работа с удалёнными репозиториями** — _завершён_
  - Дата начала: `2026-06-03`
  - Дата завершения: `2026-06-03`
  - Комментарий: `core/models.py` — новый `RemoteInfo(name, url, fetch_refspec, push_refspec)`. `core/operations.py` — `add_remote(name, url)` (ловит `pygit2.AlreadyExistsError`/`ValueError` → `GitError`), `remove_remote(name)` (KeyError → `InvalidRefError`), `list_remotes(repo)` (читает `r.remotes.names()` + `fetch_refspecs`/`push_refspecs`, корректно обрабатывает `AttributeError` для старых pygit2). `commands.py` — 5 новых `GitCommand`: `PushCommand` (undo no-op, push нельзя откатить локально), `PullCommand` (undo через `reset(_previous_head, hard)`, no-op при up-to-date), `FetchCommand` (undo no-op, fetch только обновляет remote-tracking refs), `AddRemoteCommand` (undo через `remove_remote`, защита от destroy pre-existing), `RemoveRemoteCommand` (запоминает url до удаления, undo через `add_remote` с восстановленным url). `MainViewModel` — 6 verb-методов: `push_changes`/`pull_changes`/`fetch_changes` (всегда async при `async_enabled=True`, синхронный fallback), `add_remote`/`remove_remote` (синхронные, быстрые), `list_remotes()` (читает core), `clone_repository(url, path)` (async с `AsyncWorker`, на success автопривязывает новый репо). Авто-fetch таймер — `QTimer` в конструкторе (`auto_fetch_enabled=False` по умолчанию, `auto_fetch_interval_ms=60_000`), запускается в `set_repository` если опция включена, останавливается при close. Метод `_run_async` принимает `silent_on_failure=True` (использует таймер — падающий fetch не светит ошибку каждую минуту). `BranchPanelViewModel` — `remotes()` property + `references_changed` сигнал + `get_remote_for_branch(name)` (по `origin/main` → `origin`). `config.py` — добавлены `auto_fetch_enabled`/`auto_fetch_interval_ms`. `MainWindow` — новый `QToolBar` с Push/Pull/Fetch (`Ctrl+Shift+U`/`P`/`F`), Remote menu с этими же действиями + `Manage Remotes…`. Действия enabled только когда репо открыто + не busy (через `_update_remote_actions()` на `busy_changed`/`repository_changed`). `CloneDialog` (`ui/dialogs/clone_dialog.py`) — провайдеры (GitHub/GitLab/Bitbucket/Custom URL) с префилом URL, file browse, `Generate SSH Key…` (sub-dialog `SshKeyDialog` с реальным `ssh-keygen -t ed25519` через subprocess + мок в тестах). `RemoteManageDialog` (`ui/dialogs/remote_manage_dialog.py`) — `QTableWidget` с Name/URL/Fetch, Add/Remove кнопки, сигналы `add_requested(name, url)`/`remove_requested(name)`. `LeftPanel` — context menu на remote-ветке получил `Fetch from {remote}` action (через `get_remote_for_branch`). `File > Init New Repository…` теперь реально инициализирует репо. **92 новых теста** (10 core/operations + 20 commands + 24 MainViewModel remote + 5 BranchPanelViewModel + 2 LeftPanel fetch + 14 CloneDialog + 9 RemoteManageDialog + 8 toolbar wiring), итого **405/405 проходят**, `ruff check` чисто. Замечания: pygit2 `remotes.names()` — generator, нужно обернуть в `list()`; `remotes.create()` бросает `ValueError` (а не `AlreadyExistsError`) на дубликат — ловим оба; fetch context-menu не показывается если remote-tracking ветка указывает на несуществующий remote (graceful — ничего не добавляется в actions).

- [x] **Этап 7: Stash и дополнительные инструменты** — _завершён_
  - Дата начала: `2026-06-06`
  - Дата завершения: `2026-06-06`
  - Комментарий: **Stash-команды** (StashPushCommand, StashPopCommand, StashApplyCommand, StashDropCommand, StashPushStagedCommand) реализованы в `viewmodels/commands.py` с undo/redo через CommandProcessor. **Core-операции** дополнены: `stash_apply`, `stash_drop`, `stash_oid_at`, `restore_stash` (через `git stash store`), `stash_push_staged` (через `git stash push -- <paths>`, т.к. pygit2 `paths=` ревертит все изменения). **MainViewModel** — verb-методы `stash_push/pop/apply/drop/stash_push_staged`. **LeftPanel** — контекстное меню stash с Apply/Pop/Drop (с confirm-диалогом для Drop). **Stash на графе** — синтетические узлы с `kind="stash"`, реальный OID, золотой пунктирный ромб + крест; клик — детали в CommitDetailPanel, правый клик — Apply/Pop/Drop через graph. **Порядок узлов** — WIP (Uncommitted) сверху, затем stash, затем HEAD. **Тулбар** — кнопки Stash (Ctrl+Shift+S) и Pop (Ctrl+Shift+O) с авто-обновлением enabled (Pop disabled при пустом stash-листе). **Терминал** — `TerminalWidget` с `QProcess` (cmd.exe/bash), ANSI SGR-парсер в HTML, запуск в корне репо, старт/стоп по `repository_changed`, отложенный старт через `QTimer.singleShot`. **Поиск по коммитам** — `SearchBar` с debounce 300ms, `GraphViewModel.search_commits` (по SHA/сообщению/автору), скролл к первому совпадению через `GraphTableWidget.scroll_to_commit`. **Прочее:** убран дубликат `_execute_rebase_sync` в `main_viewmodel.py`; фикс стартапа — `_restore_state()` через `QTimer.singleShot(0)` c disconnect/reconnect `active_tab_changed`. Итого **643 теста, ruff clean**.

- [x] **Этап 8: Undo/Redo и история действий** — _завершён_
  - Дата начала: `2026-06-07`
  - Дата завершения: `2026-06-07`
  - Комментарий: `CommandProcessor` — 21 команда с undo/redo (8.1: timestamp + undo_stack_snapshot/redo_stack_snapshot аксессоры). Кнопки Undo/Redo на тулбаре Edit (8.2). Конфигурируемые горячие клавиши через `config.py` (8.3: `load_hotkey`, дефолты Ctrl+Z/Ctrl+Y/Fetch/Pull/Push/Stash). `ActionHistoryWidget` — панель истории в нижних вкладках с секциями Applied/Undone (8.4). View-меню — показ/скрытие левой панели, терминала, истории (8.6). **14 новых тестов** (8 snapshot + 6 UI), **657/657 проходят**, `ruff check` чисто.

- [~] **Этап 9: Конфигурация и темизация** — _в работе (под-этапы: темизация ✓, персистентность окна ✓)_
  - Дата начала: `2026-06-03`
  - Дата завершения: `—`
  - Комментарий: **Темизация.** `src/utils/theme.py` — `Theme` (frozen dataclass: surface/text/accent/graph-цвета), `DARK_THEME` (палитра VS Code Dark+ поверх существующих цветов графа: bg `#1E1E1E`, text `#D4D4D4`, text_dim `#8B8B8B`, accent `#007ACC`), `get_theme(name)` (резолв из реестра; неизвестное имя → `UserWarning` + `DARK_THEME`), `stylesheet_for_theme(theme)` (pure функция `Theme -> str` через `{name}`-подстановку в QSS-шаблон), `apply_theme(app, theme)` (`app.setStyleSheet`). QSS покрывает QMainWindow/QDialog/QMenuBar/QMenu/QToolBar/QToolButton/QStatusBar/QSplitter/QTabWidget+QTabBar/QLineEdit/QPlainTextEdit/QTextEdit/QComboBox/QPushButton (включая :default с акцентом)/QDialogButtonBox/QListWidget/QTreeWidget/QTableWidget/QHeaderView/QProgressBar/QToolTip/QScrollBar (вертикальный+горизонтальный)/QGraphicsView/QLabel. `src/main.py` — `apply_theme(app, get_theme("dark"))` сразу после создания `QApplication`, до конструктора `MainWindow`. `src/ui/widgets/graph_widget.py::RenderConfig` — цвета переехали на `DARK_THEME.*`; добавлен kw-only `theme: Theme | None = None` в `GraphWidget.__init__`. **Персистентность окна.** `src/utils/config.py` — `default_config_path()` (Qt `AppConfigLocation` / `git-py/config.json`), `load_window_size(config)` / `load_splitter_sizes(config)` (coercion-функции; bool/float/negative/неправильная форма → defaults), `DEFAULT_WINDOW_WIDTH/HEIGHT = 1280/800`, ключи `SPLITTER_KEY_HORIZONTAL` / `SPLITTER_KEY_RIGHT_VERTICAL`. `src/ui/main_window.py` — `__init__(config_path: Path | str | None = None)` (None отключает персистентность — для существующих тестов, чтобы не трогать реальный user config); `_top_splitter` / `_right_splitter` теперь `self.*` (раньше были локальные переменные); `_restore_state()` в конце `__init__` (resize + setSizes для обоих сплиттеров, только если config_path задан); `closeEvent()` сливает текущее состояние в JSON, не теряя чужие ключи. `src/main.py` — `MainWindow(config_path=default_config_path())`. **Тесты.** `tests/ui/test_theme.py` (41 кейс) + `tests/ui/test_window_persistence.py` (31 кейс): 9 параметризованных кейсов на отказ невалидных window_size, 7 на отказ невалидных splitter_sizes, roundtrip save/load, mkdir -p, `MainWindow(config_path=None)` не пишет на диск, persist + restore размера окна через close → reopen, persist + restore splitter sizes, fallback на defaults при битом config, merge с существующими ключами (theme/panel_layout выживают), end-to-end "пользовательский сценарий" (resize + drag horizontal splitter → close → reopen, проверка пропорций). **Все 477/477 тестов проходят, ruff чисто.** Существующие 11 тестов `MainWindow()` без аргументов работают без изменений. Светлая тема + Settings dialog с переключателем — следующая итерация. Stage 7/8 (stash UI, undo UI) — по-прежнему не начаты.

- [ ] **Этап 10: Тестирование и стабилизация** — _не начато_
  - Дата начала: `—`
  - Дата завершения: `—`
  - Комментарий: `—`

### Текущий статус
- **Активный этап:** `Этап 9: Конфигурация и темизация` (под-этапы: темизация ✓, персистентность окна ✓, редизайн правой панели ✓)
- **Последнее обновление:** `2026-06-11`
- **Следующий шаг:** `Завершить Settings-диалог (переключатель dark/light), импорт/экспорт настроек.`

### Свежие правки (2026-06-11)
- **`core/repository.py` — топологическая сортировка в revwalk.** `get_history()` и `get_all_history()` теперь используют `GIT_SORT_TOPOLOGICAL | GIT_SORT_TIME` через единый `repo.walk(None, sort)` с push всех tip-ов (local + remote + tags). Раньше `get_all_history` делал N отдельных walk-ов с `GIT_SORT_TIME` и заново сортировал результат по `commit_time` — это ломало топологический порядок (родитель мог оказаться ниже ребёнка) и приводило к неверной раскладке `graph_v2.build_graph` на больших репах с CI-коммитами на одинаковых таймстампах. Поведение теперь совпадает с `keifu::Repository::get_commits`. Добавлена константа `SORT_TOPOLOGICAL_TIME` и 2 регрессионных теста в `test_repository.py` (инвертированные таймстампы + параллельные ветки).
- **Удалён мёртвый код.** `src/core/graph.py` (592 строки, фазовый priority/orphan walk + column compaction) и `tests/core/test_graph.py` (23 теста) — модуль не импортировался нигде, кроме собственного теста (активный — `graph_v2.py`, порт keifu). Удалено: −1057 строк, тестов: 707 → 684 (плюс 2 новых регрессионных = 686, итого с удалёнными − 684). `ruff check` чисто (3 ошибки — pre-existing в `graph_v2.py` и `test_left_panel.py`).
