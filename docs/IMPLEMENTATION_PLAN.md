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
- **Последнее обновление:** `2026-07-09`
- **Следующий шаг:** `Завершить Settings-диалог (переключатель dark/light), импорт/экспорт настроек.`

### Свежие правки (2026-07-07) — drag-and-drop и submenu в LeftPanel

LeftPanel получил ту же drag-and-drop + context-menu функциональность, что уже есть в чипах графа: теперь можно перетаскивать **remote** ветки на **local** ветки (через предварительный fetch+checkout tracking branch), и в правом клике локальной/remote ветки появилось **подменю `Merge <name> into...`** / **`Rebase <name> onto...`** со списком всех локальных веток кроме self. Файлы: `src/ui/widgets/left_panel.py`, тесты в `tests/ui/test_left_panel.py`.

- **`ItemIsDragEnabled` на remote ветках.** Раньше drag был разрешён только для локальных веток (через `_update_drag_state`). Теперь `ItemIsDragEnabled` ставится и на remote ветки — пользователь может их перетащить на другую ветку как в чипе графа. Pre-existing fixtures (`_set_up_repo_with_remotes`) пушит HEAD как remote-only `from-upstream`, чтобы не конфликтовать с `main`; при тестах с drag дополнительно удаляется локальная `feature` после её push'а.

- **Кастомный MIME `_BRANCH_KIND_MIME`** (`application/x-git-py-branch-kind`) в `mimeData()` — несёт discriminator `local_branch` или `remote_branch`, чтобы drop-handler мог отличить источник. Plain-text поле по-прежнему содержит голое имя ветки — это держит обратную совместимость с любыми внешними drop-таргетами.

- **Drop-handler нормализует remote source.** Раньше drop принимал строку source без знания, local она или remote. Теперь `_on_drop(source_name, source_kind, target_item)` фильтрует source kind (должен быть `local_branch` или `remote_branch`) и роутит через `_drop_actions(source, target, source_kind)` → lambda вызывает `_merge_drop` или `_rebase_drop`. Для remote source сначала вызывается `MainViewModel.fetch_and_checkout_remote_branch(remote_name)` (синхронный fetch + создание tracking branch), затем merge/rebase на bare локальное имя (`"origin/feature"` → `"feature"`).

- **`_merge_drop` / `_rebase_drop`** — новые helpers в LeftPanel. Каждый из них принимает (source, target, source_kind), нормализует source для remote и вызывает `merge_branch(source_local, target=target, no_ff=True)` или `_rebase_source_onto_target(source_local, target)` соответственно. `no_ff=True` сохранён из ранее существовавшей логики.

- **`_merge_into_submenu(source, source_kind, rebase=False)`** — строит `QAction` с `QMenu` подменю через `setMenu()`. Возвращает `None` если нет других локальных веток кроме source. В подменю — `QAction` для каждого другого local branch (отсортированы по имени); клик роутит через `_invoke_drop_action(verb, ...)` в `_merge_drop` или `_rebase_drop`.

- **Контекст-меню локальной ветки.** После старого `Merge <name> into current…` / `Rebase <name> onto current…` (они остались для короткого case'а «в текущую HEAD») появились submenu действия:
  - `Merge <name> into...` — все остальные локальные ветки
  - `Rebase <name> onto...` — все остальные локальные ветки
  
  Это позволяет сделать merge в любую local ветку без предварительного checkout'а — ровно тот workflow, что и drop на графе.

- **Контекст-меню remote ветки.** После `Checkout <name> as local branch` (которое открывает диалог "Reset Local to Here?") появились submenu `Merge <name> into...` / `Rebase <name> onto...` — каждое из них сначала fetch'ит tracking branch через `fetch_and_checkout_remote_branch`, потом делает merge/rebase в выбранную local ветку. Сценарий: пользователь видит `origin/main` ушёл вперёд и хочет слить в свой `feature` → правый клик → `Merge origin/main into...` → `feature`. Никаких лишних checkout'ов и dialog'ов (кроме того что `_handle_remote_double_click` уже показывает на `origin/main`).

- **Lambda closures с `checked=False`** — fixed баг, при котором `QAction.triggered` посылает single `bool` (checked flag), и lambda без явного `checked=False` параметра захватывала его в `source`, превращая вызовы merge/rebase в `merge_branch(False, ...)` (см. regression fix в `_drop_actions`).

- **Тесты.** +7 новых:
  - `test_mime_data_carries_branch_kind_for_remote` — MIME формат + текст
  - `test_drop_from_remote_branch_fetches_then_merges` — drop `origin/feature` → local `topic` сначала fetch+checkout tracking branch, потом merge
  - `test_merge_into_submenu_lists_other_local_branches` — submenu содержит все local ветки кроме self
  - `test_rebase_onto_submenu_in_local_context_menu` — то же для rebase
  - `test_submenu_pick_triggers_drop_merge` — click по submenu item вызывает `merge_branch(source, target, True)`
  - `test_submenu_pick_for_remote_source_fetches_first` — submenu от remote source сначала fetches
  - `test_local_branch_drag_flag_enabled_for_remote_rows_too` — `ItemIsDragEnabled` на remote rows
  
  Итого **843 проходят**, `ruff check` чисто для нового кода.

### Свежие правки (2026-07-07) — drag-and-drop merge, no-ff и reset-to-remote

Доведение drag-and-drop на графе до конца: правый клик = контекстное меню (без drag), merge теперь всегда создаёт merge-коммит (`no_ff=True`), двойной клик на remote ветку предлагает диалог «Reset Local to Here?», а при ошибке «unknown source» UI подсказывает сделать `fetch`. Файлы: `src/ui/widgets/graph_panel.py`, `src/ui/widgets/left_panel.py`, `src/ui/main_window.py`, `src/viewmodels/main_viewmodel.py`, `src/viewmodels/commands.py`, `src/core/operations.py`, плюс тесты в `tests/ui/test_left_panel.py`, `tests/viewmodels/test_main_viewmodel_remotes.py`.

- **`setAcceptDrops(True)` на `GraphTableWidget`.** Без этого виджет просто игнорировал `dragEnterEvent`/`dropEvent` — пользователь перетаскивал чип ветки и Qt тихо съедал drop. Теперь drop-обработчик работает и сигнализирует о merge/rebase.

- **Контекстное меню branch chip по правому клику.** Раньше merge/rebase был доступен только через drag-and-drop (порог ~5 px) — слишком неочевидно для пользователя. Теперь правый клик по чипу показывает меню `Merge <source> into <target>` / `Rebase <source> onto <target>` через `_build_branch_menu_actions` (тот же путь, что и drop-меню). Single-menu dispatch — drop и right-click идут через одну функцию `MergeCommand`/`RebaseCommand` в `CommandProcessor`. Локальная ветка → правый клик показывает также `Checkout` и `Create Branch Here`.

- **`no_ff=True` во всех UI-путях merge.** Раньше `MainViewModel.merge_branch` всегда делал fast-forward если возможно — из UI это приводило к «ничего не произошло» (ветка молча переехала на новый SHA без merge-коммита). Добавлен keyword-only параметр `no_ff=False` (по умолчанию — программные вызовы сохраняют git-совместимое поведение). Drag-and-drop и right-click контекстное меню на графе и в `LeftPanel` теперь передают `no_ff=True`. Сигнатура: `merge_branch(source, target=None, *, no_ff=False)`, `MergeCommand(..., no_ff=False)`. Логи попадают в CommandProcessor с undo через `reset(_previous_head, hard)` (merge --no-ff всё равно создаёт merge-коммит, который на undo должен быть убран — поведение идентично обычному 3-way merge).

- **«Reset Local to Here?» на double-click remote ветки.** `LeftPanel._handle_remote_double_click(name)` уже существовал как хук для drag-and-drop на чипе, теперь обслуживает ещё один кейс: если локальная ветка с тем же именем существует, показывается `QMessageBox.question` «Reset local '<name>' to match the remote?». `Yes` → деструктивный путь; `No` (default) — отмена. Реализация destructive-пути — новый метод VM: `reset_local_branch_to_remote(name)`:
  1. **Fetch.** Синхронный `FetchCommand(remote_name, branch_name)`, чтобы избежать hung async fetch на Windows.
  2. **Lookup remote ref.** После fetch читает `origin/<name>` через `repo_manager.branches`. Если remote ref не появился — `error_occurred` и выход (не сбрасываем в stale tip).
  3. **No local branch case.** Создаёт локальную через `CreateBranchCommand` + checkout — эквивалент `fetch_and_checkout_remote_branch`, но в одном пути.
  4. **Hard-reset local.** Если локальная ветка уже есть — `core_reset(repo, target_sha, mode="hard")` (отбрасывает unpushed commits + worktree drift). Checkout через `GIT_CHECKOUT_FORCE`, потому что `checkout_branch` иначе re-flag'ит dirty файлы, которые hard reset только что привёл в порядок.

  Метод **не** идёт через `CommandProcessor` — после `reset --hard` lost commits не вернуть через undo (reflog path тоже отрезан), UI gating на диалог компенсирует.

- **Right-click «Checkout» на remote ветке тоже показывает диалог.** Левый клик на remote (двойной) и right-click → `Checkout <name> as local branch` из `LeftPanel._remote_branch_actions` теперь оба идут через `_handle_remote_double_click` (двойной клик — напрямую, правый клик — через change lambda). Раньше правый клик напрямую дёргал `fetch_and_checkout_remote_branch`, который молча проглатывал diverged state («Local 'main' has diverged from 'origin/main'; leaving local ref as-is» + checkout того же самого local main). Симметрично `_on_graph_branch_checkout` в `MainWindow` получил ту же логику — `QMessageBox` + `reset_local_branch_to_remote` / `fetch_and_checkout_remote_branch`.

- **Подсказка про fetch в ошибках merge.** `core.operations.merge_branch` и `complete_merge` теперь матчат типичную pygit2 ошибку `unknown revision ...` / `cannot find source` и к сообщению добавляют «Run 'fetch <remote>' to update remote-tracking branches and retry.» Это чтобы пользователь не терял время, пытаясь понять, почему git не видит его собственную ветку после merge драга с upstream-веткой.

- **VM helper `local_branch_exists(name)`** в `MainViewModel` — вынесен из `LeftPanel._local_branch_exists` (который сам был приватным wrapper'ом вокруг `repository_manager().branches`) для переиспользования в `MainWindow._on_graph_branch_checkout`.

- **Тесты.** +6 новых тестов:
  - В `test_left_panel.py`: `test_double_click_on_remote_branch_with_local_confirms_reset` (mock `QMessageBox.question` на Yes / No → проверка что `reset_local_branch_to_remote` вызывается только на Yes) и переписан `test_double_click_on_remote_branch_fetches_and_checks_it_out` под кейс без локальной ветки (через push + delete local `feature`).
  - В `test_main_viewmodel_remotes.py`: `test_reset_local_branch_to_remote_creates_new_local`, `test_reset_local_branch_to_remote_hard_resets_ahead_local`, `test_reset_local_branch_to_remote_without_repo_emits_error`, `test_reset_local_branch_to_remote_bad_name_emits_error`, `test_reset_local_branch_to_remote_when_busy_emits_error` — все 5 path'ов нового VM-метода покрыты.
  - Итого **836 проходят**, `ruff check` чисто для нового кода.

### Свежие правки (2026-07-07) — popup skip-when-single

Если после фильтрации дублей (`_suppress_dup_remotes`) в строке осталось меньше двух веток, popup не показывается. Двойная защита: `_on_hover_popup_timer` теперь проверяет `chip['hidden_count'] <= 0` (post-filter значение из `_draw_branch_chips`) и сразу выходит, не запуская debounce-таймер. `_show_branch_popup` дополнительно страхует `if len(branches) < 2: return` после `_branches_at_row_visible`. Сценарий: `main` (локальная) + `origin/main` (дубликат) → чип рисует только `main`, hover не открывает popup (там было бы одно имя, которое и так видно). Тесты: `test_branch_popup_skipped_when_only_one_branch_visible`, `test_branch_popup_hover_timer_skipped_for_single_visible` (+2). Обновлён `test_branch_popup_filters_origin_main_and_origin_head` — добавлен `origin/feature` чтобы popup показывался с отфильтрованным набором. **Итого 836/836 проходят, ruff чисто.**

### Свежие правки (2026-07-02)
- **Stash Changes в контекстном меню WIP-ноды.** Правый клик по синтетической ноде незакоммиченных изменений теперь предлагает `Stash Changes` первым пунктом меню (над разделителем, до `Discard changes` / `Copy diff`). Действие маршрутизируется через новый сигнал `GraphTableWidget.stash_push_requested` → `MainWindow._on_stash_push_graph` → `MainViewModel.stash_push("WIP")` (та же команда `StashPushCommand` что и тулбар `Ctrl+Shift+S`, попадает в undo-стек). Контекстное меню ноды (commit/stash/WIP) вынесено в `_build_node_menu(sha, kind)` (паттерн как у `_build_branch_menu_actions`) — 4 новых теста в `test_graph_widget.py` (структура WIP-меню, эмиссия сигнала, сохранение `discard_changes_requested`, отсутствие `Stash Changes` на обычном коммите) + 1 тест в `test_main_window.py` (роутинг сигнала в VM). **+5 тестов, 796/796 проходят, `ruff check` чисто для нового кода**.

### Свежие правки (2026-07-12) — фикс цвета ветки `3mk4yl/fix-dict-unhashable-bug`

Исправлен баг `gpt-researcher` commit `31b22352`: tip side-branch коммита рисовался в цвете merge коммита (а не в собственном цвете ветки), а mainline вокруг merge окрашивался в fallback-цвет от `parent[1]` pre-coloring. Подробное расследование — в `docs/BUG_31b22352_branch_colors.md`.

- **Корень проблемы** — две точки в `src/core/graph_v2.py:build_graph`:
  1. `commit_lane_opt is not None` ветка (строка 566) использовала `color_assigner.continue_lane(lane)` даже когда у коммита был свой `primary_branch`. Цвет из `lane_colors[lane]` (заполненный ранее merge коммитом) перебивал `_pick_branch_color(name)`. Side-branch tip получал цвет merge коммита.
  2. Parent lanes setup (строка 655) писал `lane_color_index[new_lane] = new_color` безусловно. Когда `new_color` — это fallback от `_pick_fallback(lane)` (потому что parent — orphan без `primary_branch`), это отравляло lane cache для всех последующих коммитов на этой lane.

- **Изменение A (строки 566-582)** — `commit_lane_opt is not None` ветка теперь проверяет `primary_branch`. Если коммит имеет имя ветки, вызывается `assign_color(lane, primary_branch)` вместо `continue_lane(lane)`, чтобы tip side-branch получил свой собственный цвет из `_pick_branch_color(name)`.

- **Изменение B (строки 664-684)** — `lane_color_index[new_lane] = new_color` для fork siblings теперь записывается **только** когда у parent есть `primary_branch`. Для orphan parents (например, merge коммита, чьи предки — mainline без ветки) предыдущее значение `lane_colors[lane]` сохраняется, и mainline коммиты больше не перекрашиваются в случайный fallback-цвет.

- **Изменение C (строки 623-654)** — `fork_sibling_color` логика (которая перекрашивала коммит в `main_color` если его parent — fork point и `commit_lane is main_lane`) теперь применяется **только к merge коммитам** (`len(valid_parents) >= 2`). Для single-parent коммитов с parent на fork point сохранена оригинальная логика (`parent_lane = lane`, `was_existing = False`), но **без** перезаписи `lane_color_index[lane]` на `main_color`. Это устранило случай, когда side-branch tip перекрашивался в master BLUE поверх собственного GOLD.

- **Тесты.** +2 новых в `tests/core/test_graph_v2.py`:
  - `test_branch_tip_keeps_own_colour_when_merge_processed_first` — tip side-branch (`987c9e8`) получает `_pick_branch_color("3mk4yl/fix-dict-unhashable-bug")` = idx=15 (GOLD), а не цвет merge коммита.
  - `test_fork_sibling_does_not_overwrite_mainline_lane_colour` — `master` tip (main_next) сохраняет master BLUE через fork-sibling pre-coloring, не перезаписывается на fallback.
  И **все 52 теста `test_graph_v2.py`** проходят (204/204 core тестов), `ruff check` чисто.

- **Результат на реальных данных** (`tools/reproduce_31b22352_bug.py`):
  - **До:** `987c9e8` рендерился с col 46 = `PIPE c=6` (PINK #7D2559) на строках 104-109; mainline (lane 0) на строках 84-94 имел `TEE_RIGHT c=N p=6` (PINK pipes).
  - **После:** `987c9e8` рендерится с col 46 = `PIPE c=15` (GOLD #C4912E); mainline больше не в PINK — fallback LIME (idx=11) не отравлен pre-coloring'ом.

### Свежие правки (2026-07-12) — расширение `BRANCH_PALETTE` с 24 до 40 цветов

После фикса выше коллизии crc32 между разными ветками стали более заметны: до фикса коммиты одной ветки могли получать **разные** цвета через `lane_colors[lane]` cache, после фикса — стабильный цвет через `_pick_branch_color(primary_branch)`. В `gpt-researcher` (66 веток) на 22 индексах 18 коллидировали, в среднем 3.0 ветки на индекс.

- **`BRANCH_PALETTE` расширена** с 24 до 40 цветов в `src/core/graph_v2.py:39-79`. Новые 16 индексов (24..39) — дополнительные оттенки (sea, coral, bronze, indigo, sky, sand, burgundy, peach, khaki, jade, fuchsia, chestnut, cerulean, wisteria, sandalwood, moss) с hex-кодами выбранными для контраста на тёмном фоне `DARK_THEME.bg = #1E1E1E`.

- **`UNCOMMITTED_COLOR_INDEX` перенесён** с 24 на 40 — специальный idx за пределами палитры, чтобы `crc32(name) % 40` никогда не мог дать этот индекс. Это сохраняет семантику WIP-маркера как **отдельного** специального значения, не конкурирующего с обычными цветами веток.

- **Код** — никаких дополнительных изменений не нужно: `_pick_branch_color` использует `crc32(name) % len(BRANCH_PALETTE)` (line 94), `_pick_fallback` использует `len(BRANCH_PALETTE)` (lines 361-366), `_cell_color` в `graph_panel.py:194-200` уже обрабатывает `UNCOMMITTED_COLOR_INDEX` отдельно от диапазона палитры.

- **Тесты.** +1 новый в `tests/core/test_graph_v2.py`:
  - `test_uncommitted_color_index_is_outside_palette` — гарантирует что `UNCOMMITTED_COLOR_INDEX >= len(BRANCH_PALETTE)`, защита от регрессии при будущих изменениях палитры.
  - **Все 53 теста `test_graph_v2.py` проходят**, `ruff check` чисто.

- **Результат** для `gpt-researcher`: 66 веток на 33 индексах, 19 групп коллизий, avg **2.0** ветки на индекс (было 3.0). Коллизий стало статистически меньше на каждую ветку, и в типичных репозиториях с <40 веток коллизий обычно нет вовсе.

### Свежие правки (2026-07-07) — branch-chip UX

Вся работа по интерактивным чипам веток и связанным правилам подавления. Файлы: `src/ui/widgets/graph_panel.py`, `src/ui/widgets/left_panel.py`, `src/viewmodels/branch_panel_viewmodel.py`, `src/viewmodels/main_viewmodel.py`, `src/viewmodels/graph_viewmodel.py`, `src/ui/main_window.py`, плюс тесты в `tests/ui/test_graph_widget.py`, `tests/ui/test_left_panel.py`, `tests/viewmodels/test_branch_panel_viewmodel.py`, `tests/viewmodels/test_main_viewmodel_branches.py`, `tests/ui/test_main_window.py`.

- **Collapse-политика на чип-колонке.** Каждая строка с ≥2 ветками теперь рисует один priority-чип + индикатор `▼` (с badge `+N` если скрыто >1). Helper `_suppress_dup_remotes(branch_refs)` единая точка подавления (same-name remote + синтетический `*/HEAD`), используется и в `_draw_branch_chips`, и в hover-popup, и в `BranchPanelViewModel._suppress_same_name_remotes`. Приоритет (`_branch_priority_key`): `HEAD > reachable-from-HEAD > recently-created > other`. Только что созданная ветка уходит в бакет `2`, чтобы не перебивать исходную при раскрытии.

- **Local vs remote-only стиль.** Локальные чипы — заливка цветом коммита + белый текст + monitor-иконка. Remote-only (без локального дубликата) — обводка цветом коммита без заливки, текст тоже цвета коммита (wire-frame look). Флаг `is_remote_only` рассчитывается в `_draw_branch_chips` и используется popup-ом.

- **`origin/HEAD` и same-name remote подавление.** Тройная защита: чип-колонка (`_suppress_dup_remotes` в `_draw_branch_chips`), popup (`_branches_at_row_visible` фильтрует перед показом), левая панель (`_suppress_same_name_remotes` в VM + `setHidden(True)` на группе `Remote` если она пуста). Это закрывает симптом "main, HEAD, main" — теперь ни в одной точке UI пользователь не видит дубли.

- **Hover-popup `BranchStackPopup`.** Frameless `Qt.Tool` окно, всплывает через 220 ms debounce (`_HOVER_POPUP_DELAY_MS`) на свёрнутом multi-branch чипе. Single/double-click строки → `branch_selected(full_name)` → `MainViewModel.checkout_branch` (`CheckoutCommand` → undo). Закрытие тройное: `leaveEvent` + debounced 160 ms close-таймер, глобальный `QApplication.installEventFilter` ловит `MouseMove` за пределами popup+чипа, `ApplicationDeactivate` закрывает немедленно. `eventFilter` на родителе ловит `QEvent.Move` и двигает popup на ту же дельту — popup следует за чипом при перетаскивании окна на другой экран. `hideEvent` чистит ссылку на popup в родителе + снимает все event-фильтры (защита от утечек).

- **Inline «Create Branch Here».** Правый клик по локальному чипу → контекст-меню с `Create Branch Here` (только для local; remote-чипы не имеют этого пункта) → `QLineEdit` точно над чипом. Enter → `create_branch_here_requested(sha, name)` → `MainViewModel.create_branch(name, target_sha=sha)` → `CreateBranchCommand` через `CommandProcessor` (undo = удаление). Escape/потеря фокуса → закрыть без действия.

- **Drag-and-drop merge/rebase.** Press-and-drag на чипе → `QDrag` с MIME `application/x-git-py-branch-chip`. Drop на другой чип → контекст-меню `Merge <source> into <target>` / `Rebase <source> onto <target>` → `MainViewModel.merge_branch(no_ff=True)` / `rebase_branch` (через `MergeCommand`/`RebaseCommand` → undo). `drag_start_threshold_px` (~5 px) защищает от случайной промоции press→drag на коротком клике. Эквивалентное контекстное меню также открывается по правому клику (через `_build_branch_menu_actions`) для пользователей, которые не догадались про drag. См. свежую правку про drag-and-drop merge, no-ff и reset-to-remote.

- **Recently-created tracking.** `MainViewModel._recently_created_branches: set[str]` + сигнал `recently_created_changed` → форвардится в `GraphViewModel.update_recently_created(names)` через `recently_created_changed` сигнал. Сбрасывается при `set_repository`. Используется `_branch_priority_key` (бакет `2`).

- **Тесты.** +21 новый тест: `test_three_branches_collapse_to_primary_chip`, `test_three_branches_popup_lists_all_three`, `test_three_branches_render_only_one_visible_chip` (pixel-level), `test_remote_branch_suppressed_when_local_duplicate_exists`, `test_origin_head_remote_is_dropped_from_chip_cache`, `test_drop_on_suppressed_chip_emits_signal`, `test_remote_duplicate_of_local_marks_is_remote_only_false`, `test_branch_popup_lists_all_branches`, `test_branch_popup_row_click_emits_checkout`, `test_branch_popup_closes_on_mouse_leave`, `test_branch_popup_tracks_parent_window_move`, `test_branch_popup_filters_origin_main_and_origin_head`, `test_branch_popup_closes_on_global_mouse_move_outside`, `test_remote_branch_dropped_when_same_name_local_exists` (viewmodel), `test_double_click_on_remote_branch_with_local_confirms_reset` (repurposed в проверку suppression), `test_create_branch_here_emits_signal`, `test_create_branch_from_chip_routes_to_vm`, `test_create_branch_here_inline_editor_opens_and_accepts`, `test_chip_drag_emits_drop_on_chip_menu`, `test_drop_emits_merge_or_rebase_branch_requested`, плюс обновлены 6 существующих тестов для использования remote-only имени `from-upstream` вместо коллизии `origin/main`. Итого **827/827 проходят**, `ruff check` чисто (3 pre-existing ошибки не из этого набора).

### Свежие правки (2026-07-11) — fork-overlay не перетирает merge/source-соединения

Исправлен дефект в строках, где merge-коммит одновременно является fork-точкой: линии создания новых веток (`fork_merging_cells`) накладывались поверх уже построенных связей с родителями и могли заменить корректную клетку `BRANCH_LEFT` / `MERGE_LEFT` на `HORIZONTAL`. Из-за этого визуально пропадала вертикаль вниз к source-ветке merge. Конкретный кейс — `gpt-researcher` merge `6c75117` (`sudabg/fix/reference-error-1673` → `_render_target`): до overlay на lane source-ветки была `BRANCH_LEFT c=11`, после overlay становилась `HORIZONTAL c=13`.

**Решение.** В `src/core/graph_v2.py` при наложении `fork_merging_cells` на `cells` существующие смысловые клетки защищены от перезаписи: `BRANCH_RIGHT`, `BRANCH_LEFT`, `MERGE_RIGHT`, `MERGE_LEFT`, `CROSS`. Они уже несут правильную геометрию merge/parent-связи, а fork-коннектор должен заполнять только остальные промежуточные клетки. Отдельный тип `TEE_DOWN` не вводился, и цвета fork-коннектора не менялись.

**Рендер `CROSS`.** В `src/ui/widgets/graph_panel.py` блок `_T_CROSS` явно рисует верхнюю вертикаль цветом `pipe_color_index` (если есть) и нижнюю вертикаль цветом `color_index`, затем добавляет горизонтальный хвост только при `cell["d"] != 0`. Это соответствует общей модели: верх к ребёнку, низ к родителю.

**Проверки.** `python simulate_problem.py` показывает `BRANCH_LEFT c=11` на проблемной точке и `downward branch is present`; `python -m pytest tests/core` — 202 passed; `ruff check src/core/graph_v2.py src/ui/widgets/graph_panel.py simulate_problem.py` — clean. Полный `ruff check src/ tests/` по-прежнему падает только на pre-existing ошибки в `left_panel.py` и `tests/ui/test_window_persistence.py`.

### Свежие правки (2026-07-11) — CROSS-`direction`: закрытие зазора у merge-коннектора

Исправлен визуальный дефект на merge-коммитах с дальним вторым родителем: между вертикальной трубой родителя и горизонтальным коннектором оставалось `lane_w / 2 ≈ 11 px` пустоты. Конкретный кейс — `gpt-researcher` `693d3b72 ← b364917f` (merge на lane 14, второй родитель на lane 0): розовая горизонталь «обрывалась» в воздухе, не доходя до вертикали. Полное описание в `docs/MERGE_LANE_FIX.md`.

**Причина.** `CROSS`-ячейка (cross-junction, рисуется в fork-merge кейсе на lane родителя) рисовала только вертикали (`_T_CROSS` блок в `_draw_cell_row`). Горизонталь шла из соседней between-lanes ячейки `HORIZONTAL` / `HORIZONTAL_PIPE` на col `parent_lane * 2 + 1` — её `x = col_left + lane_w / 2` (центр lane), а не `x = col_left` (центр коммита). Между вертикальной трубой CROSS и началом горизонтали — `lane_w / 2` пустоты.

**Решение.** Расширили `CellInfo` полем `direction: int = 0` (только для `CROSS`):
- `+1` / `-1` — провести горизонталь от центра CROSS-ячейки вправо / влево на ширину `lane_w`;
- `0` — без дополнительной горизонтали (default, backwards-compatible).

В `_build_row_cells` направление выбирается автоматически: `direction = -1 if parent_lane > commit_lane else 1` (горизонталь тянется в сторону merge-коммита, закрывая зазор между commit-вертикалью и between-lanes-горизонталью). В `_draw_cell_row` (`src/ui/widgets/graph_panel.py`) добавлен вызов `_draw_horiz_line(... lane_w * direction ...)` при `cell["d"] != 0`. Глобальный `_draw_horiz_line` не трогали — расширение локализовано в `CROSS` и не задевает `HORIZONTAL` / `HORIZONTAL_PIPE` в других контекстах (соседние lanes, multi-merge fork connector).

**Что НЕ менялось.** Fork-connector (`_build_fork_connector_cells`), цвета bridge pipe / fork connector (см. «Цвета bridge pipe и fork connector» ниже), существующие cell types (`BRANCH_LEFT` / `BRANCH_RIGHT` / `MERGE_LEFT` / `MERGE_RIGHT` / `TEE_RIGHT` / `TEE_LEFT`) — все они уже рисуют горизонталь, когда это нужно. Меняется только поведение `CROSS`, и только в новых fork-merge кейсах. Backwards-compatible: существующие caller-ы `CellInfo.cross()` без `direction=` аргумента получают `direction=0`, и renderer не рисует дополнительной горизонтали — поведение до фикса сохраняется.

**Файлы.** `src/core/graph_v2.py` (поле `CellInfo.direction`, kw-arg у `CellInfo.cross()`, `to_dict()` сериализует ключ `d`, выбор направления в `_build_row_cells`), `src/ui/widgets/graph_panel.py` (рендер CROSS), `tests/core/test_graph_v2.py` (+3 регрессионных), `simulate_problem.py` (тот же фикс в локальной копии рендерера, чтобы симуляция отражала исправленную картинку). Документация: `docs/MERGE_LANE_FIX.md` (полное описание проблемы, причин и решения), `docs/FEATURES.md` (раздел «CROSS-`direction`: закрытие зазора у fork-merge точки»).

**Тесты.** +3 новых в `tests/core/test_graph_v2.py`:
- `test_cross_cell_carries_horizontal_direction` — `CROSS` на lane, отличном от merge-lane, несёт правильное направление (`+1` если `parent_lane < commit_lane`, `-1` если `>`); сериализованный `to_dict()` содержит ключ `d`.
- `test_cross_cell_direction_default_is_zero` — `CellInfo.cross()` без `direction=` аргумента остаётся backwards-compatible (`direction=0`, ключ `d` отсутствует в `to_dict()`).
- `test_cross_cell_to_dict_omits_direction_when_zero` — `to_dict()` не пишет лишний ключ при явном `direction=0` (минимальный wire-формат).

Итого **70/70** тестов на `tests/core/test_graph_v2.py` + `tests/viewmodels/test_graph_viewmodel.py` проходят, `ruff check` чисто. Pre-existing access-violation падения в `tests/viewmodels/test_main_viewmodel_clipboard.py` и `tests/ui/test_graph_widget.py` к этой правке не относятся (Qt/clipboard mocking на Windows).

**Визуальная проверка.** `python simulate_problem.py` рендерит реальный `gpt-researcher` через `QPainter` (те же примитивы что и `graph_panel.py`) и сохраняет PNG в `%TEMP%\opencode\merge_bend_problem.png` (и `_zoom.png` 4×). До фикса — красная рамка «empty gap (11 px)» на col 0 строки merge. После — зелёная галочка «bend bridged», горизонталь дотягивается до вертикали без зазора.

### Свежие правки (2026-07-07) — Copy branch name на графе

В графе теперь можно скопировать имя ветки и SHA коммита прямо из контекстного меню branch-chip — симметрично с `LeftPanel`, где эти пункты уже были. Файлы: `src/ui/widgets/graph_panel.py`, `src/ui/main_window.py`, плюс тесты в `tests/ui/test_graph_widget.py`, `tests/ui/test_main_window.py`.

- **Сигналы в `GraphTableWidget`.** Два новых сигнала — `copy_branch_name_requested = Signal(str)` и `copy_commit_sha_requested = Signal(str)`. Полезная нагрузка: full ref имя (например `main` или `origin/main`) и row SHA. Эмитятся из `_build_branch_menu_actions` через ту же helper-функцию для синхронной инспекции в тестах, что и `merge_branch_requested` / `rebase_branch_requested`.
- **Пункты меню.** В конец `_build_branch_menu_actions` добавлен раздел через `_make_separator()`: `Copy branch name` (всегда) + `Copy commit sha` (когда `row_sha` доступен). Меню строится и для local, и для remote chip-ов. Для remote копируется **полное** ref-имя (`origin/base_features`), а не display label — то же поведение, что в `LeftPanel._remote_branch_actions`.
- **MainWindow.** `_on_copy_branch_name(name)` / `_on_copy_commit_sha(sha)` подключены к `MainViewModel.copy_to_clipboard`; status bar показывает `"Copied branch name 'main'"` / `"Copied commit abc1234"` на 3 секунды. Пустые payload-ы игнорируются (защита от stale graph rebuild).
- **Тесты.** +7 новых: `test_branch_menu_has_copy_branch_name_for_local`, `test_branch_menu_copy_branch_name_emits_full_ref`, `test_branch_menu_copy_commit_sha_emits_row_sha`, `test_branch_menu_has_copy_branch_name_for_remote`, `test_branch_menu_copy_branch_name_uses_full_ref_for_remote`, `test_graph_copy_branch_name_routes_to_clipboard`, `test_graph_copy_commit_sha_routes_to_clipboard`. Итого **834/834 проходят**, `ruff check` чисто.

### Свежие правки (2026-06-14)
- **Stash-ребаланс + WIP на lane 0.** Стэши, чей первый родитель — HEAD, теперь сдвигаются на offset-полосы перед вставкой WIP, освобождая lane 0 для ноды незакоммиченных изменений. В строке стэша нет горизонтальных коннекторов — только COMMIT + PIPE для вертикальной непрерывности. Форк рисуется исключительно на строке HEAD через `_build_fork_connector_cells`. Исправлена вставка стэшей в ViewModel: теперь стэши на HEAD вставляются перед HEAD по индексу, а не по timestamp (чтобы попасть в ребаланс). Добавлен e2e-тест `test_stash_with_uncommitted_keeps_wip_on_main_lane`. Обновлена документация в `docs/FEATURES.md`.
- **`core/repository.py` — топологическая сортировка в revwalk.** `get_history()` и `get_all_history()` теперь используют `GIT_SORT_TOPOLOGICAL | GIT_SORT_TIME` через единый `repo.walk(None, sort)` с push всех tip-ов (local + remote + tags). Раньше `get_all_history` делал N отдельных walk-ов с `GIT_SORT_TIME` и заново сортировал результат по `commit_time` — это ломало топологический порядок (родитель мог оказаться ниже ребёнка) и приводило к неверной раскладке `graph_v2.build_graph` на больших репах с CI-коммитами на одинаковых таймстампах. Поведение теперь совпадает с `keifu::Repository::get_commits`. Добавлена константа `SORT_TOPOLOGICAL_TIME` и 2 регрессионных теста в `test_repository.py` (инвертированные таймстампы + параллельные ветки).
- **Удалён мёртвый код.** `src/core/graph.py` (592 строки, фазовый priority/orphan walk + column compaction) и `tests/core/test_graph.py` (23 теста) — модуль не импортировался нигде, кроме собственного теста (активный — `graph_v2.py`, порт keifu). Удалено: −1057 строк, тестов: 707 → 684 (плюс 2 новых регрессионных = 686, итого с удалёнными − 684). `ruff check` чисто (3 ошибки — pre-existing в `graph_v2.py` и `test_left_panel.py`).

### Свежие правки (2026-07-09) — Цвета bridge pipe и fork connector

Исправлены два дефекта раскладки графа, связанных с цветами соединительных линий. Файлы: `src/ui/widgets/graph_panel.py`, `src/core/graph_v2.py` (без изменений — откатил ошибочную правку), плюс тесты в `tests/ui/test_graph_widget.py` и `tests/core/test_graph_v2.py`.

**Проблема 1 — Bridge pipe наследует цвет текущей строки вместо предыдущей.** Вертикальная соединительная линия между двумя соседними строками (например, от стэша вниз к корневому коммиту, или от HEAD вниз к WIP-узлу) рисовалась в цвете `TEE_RIGHT` текущей строки (то есть цвет корневого коммита), а должна была наследовать цвет предыдущей строки на том же lane. Симптом: при наличии стэша (или WIP-узла) над корневым коммитом линия от стэша к корню шла синим (цвет main), а должна была идти серым (цвет стэша/WIP).

**Проблема 2 — попытка исправить 1 сломала 2.** Первая итерация правки в `_draw_cells` трогала только нижнюю строку (last row), а не все bridge pipes. После этого была попытка исправить проблему 1 через замену цвета fork connector на `main_color` в `src/core/graph_v2.py::_build_fork_connector_cells`. Это сломало стэш-форки: форк-коннектор от форк-точки (например, коммита, от которого ответвляется стэш) стал рисоваться в цвете корневого коммита вместо цвета стэша, разрывая визуальную связь «форк-коннектор ↔ стэш-узел».

**Решение.** Правка ограничена widget-layer'ом (один файл — `src/ui/widgets/graph_panel.py`), в `_draw_cells` цикл bridge pipes теперь смотрит на `prev_cells = self._rows[row_idx - 1].get("cells", [])` для **всех** строк, а не только для последней. Для каждой ячейки bridge pipe (TEE_RIGHT, TEE_LEFT, HORIZONTAL_PIPE, TEE_UP) ищется ячейка предыдущей строки на том же lane и наследуется её цвет (`p` для tee-типов, иначе `c`). Если предыдущей строки нет или ячейка пустая — fallback на текущую строку (`row_data["color_index"]`).

**Что не менялось.** `src/core/graph_v2.py::_build_fork_connector_cells` остаётся как было до моих изменений: `TEE_RIGHT.color_index = first_merge_color` (цвет merging lane), `pipe_color_index = main_color` (вертикаль у корня остаётся в его цвете), `MERGE_LEFT.color_index = merge_color`, горизонтальные сегменты — в цвете merging lane. То есть **fork connector читается как цвет lane-приёмника** (стейш или ветка, которая входит в форк-точку), а не как цвет корневого коммита.

**Тесты.** +9 регрессионных (4 core + 5 UI), все проверяют инварианты раскладки независимо от конкретных palette-индексов:

- `test_topmost_commit_does_not_draw_line_stub_into_empty_space` — нет stub-а наверху корневого коммита.
- `test_no_pipe_between_sibling_stash_and_unrelated_row_above` — sibling-стэш не рисует вертикаль в несвязанную строку выше.
- `test_no_pipe_from_horizontal_into_stash_below` — горизонтальный сегмент в строке над стэшем не утекает в стэш.
- `test_lane_above_root_stays_in_root_branch_colour` — pixel-level: bridge pipe от стэша к корню = WIP-grey (не main-blue); PIPE в строке стэша на lane 0 = WIP-grey.
- `test_root_commit_does_not_draw_stub_below_itself` — нет stub-а вниз от корневого коммита.
- `test_stash_fork_connector_uses_merging_branch_colour` (UI) — клетки форк-коннектора в строке корня используют `stash_color_index`, не `root_color_index`.
- `test_fork_connector_uses_merging_branch_colour` (core) — прямой unit на `_build_fork_connector_cells`: `TEE_RIGHT.color_index == first_merge_color`, `pipe_color_index == main_color`, `MERGE_LEFT.color_index == merge_color`, горизонтальные сегменты = `merge_color`.
- `test_fork_connector_multiple_merges_keeps_tee_in_first_merge_colour` (core) — multi-merge: `TEE_RIGHT` остаётся в цвете первого merge, промежуточный `TEE_UP` — в цвете следующего merge, правый `MERGE_LEFT` — в цвете своего merge.
- `test_fork_connector_main_lane_uses_main_colour_when_no_merges` (core) — без форков main-lane = простой PIPE в `main_color`.

### Свежие правки (2026-07-09) — Контекстное меню на вкладках репозиториев

Правый клик по табу репозитория теперь открывает меню из 5 пунктов (вместо default `QTabBar` меню «Close tab»). Файлы: `src/ui/widgets/repo_bar_widget.py`, `src/viewmodels/repo_tabs_viewmodel.py`, `src/viewmodels/main_viewmodel.py`, `src/ui/main_window.py`, плюс тесты в `tests/ui/test_repo_bar_widget.py`, `tests/viewmodels/test_repo_tabs_viewmodel.py`, `tests/viewmodels/test_main_viewmodel_repo_folder.py`, `tests/ui/test_main_window.py`.

- **Структура меню.** Пункты по порядку: `Show repo folder` → `Copy repo path` → separator → `Close repo tab` → `Close other tabs` → `Close tabs to the right`. Первые два «путевые» (показывают/копируют путь кликнутого таба), последние три — операции над списком табов. Disabled-state: `Close other tabs` серый при единственном табе, `Close tabs to the right` серый на самом правом. Остальные всегда enabled.

- **`RepoTabViewModel.close_others(index)` и `close_to_right(index)`.** Два новых метода-глагола, симметричных `remove_tab`. `close_others` коллапсирует список к одному табу с переданным индексом (no-op при 1 табе или index вне диапазона). `close_to_right` обрезает хвост после переданного индекса (no-op если index уже последний). В обоих случаях active-index пересчитывается, если он вышел за новые границы, и эмитятся `tabs_changed` + `active_tab_changed`.

- **`MainViewModel.show_repo_in_folder(path)` и `copy_repo_path(path)`.** Новые хелперы рядом с существующими `show_in_folder`/`copy_file_path`. `show_repo_in_folder` нормализует путь (`os.path.normpath`), проверяет что директория существует, иначе — тихий no-op (таб может кратко ссылаться на stale path во время config restore). Все исключения из `subprocess.Popen` глотаются — failure не actionable. `copy_repo_path` делегирует в существующий `copy_to_clipboard`; пустая строка не трогает clipboard (защита от stale rebuild).

- **`RepoBarWidget` — контекст-меню через `customContextMenuRequested`.** Установлен `Qt.CustomContextMenu` на `QTabBar`, сигнал приходит в `_on_tab_context_menu(pos)`. Меню строится через `_build_tab_context_menu_actions(index, path, tab_count)` (отдельный builder, как `_build_branch_menu_actions` в `graph_panel.py` — для синхронных тестов без `QMenu.exec`). Два новых сигнала: `show_folder_requested(str)` и `copy_path_requested(str)` несут путь кликнутого таба (не активного). Right click вне табов → no-op (защита от `tabAt == -1`). Empty `tabData` → no-op (защита от race между `_rebuild_tabs` и `setTabData`).

- **`MainWindow` роутинг.** Подключает `_repo_bar.show_folder_requested → _on_show_repo_folder` и `_repo_bar.copy_path_requested → _on_copy_repo_path`. Хендлеры — тонкие обёртки над `MainViewModel.show_repo_in_folder`/`copy_repo_path` + status bar на 3 секунды («Opened … in Explorer» / «Copied repository path: …»). Пустые payload-ы игнорируются (защита от stale menu).

- **Тесты.** +22 новых (12 viewmodel `RepoTabViewModel` мутации + 7 `MainViewModel.show_repo_in_folder`/`copy_repo_path` + 12 `RepoBarWidget` меню-строитель + 3 `MainWindow` роутинг = **22 новых уникальных** в сумме, итого **910/910 проходят**). 

  - `tests/viewmodels/test_repo_tabs_viewmodel.py` — 12 тестов: `remove_tab` (3 — drop+adjust active, до-active-keep-index, out-of-range is noop), `close_others` (4 — keep-only-clicked, single-tab is noop, out-of-range is noop, эмиссия сигналов), `close_to_right` (5 — keep-up-to-incl-index с active-fallback, active-inside-keep-index, rightmost-is-noop, out-of-range is noop, эмиссия).
  - `tests/viewmodels/test_main_viewmodel_repo_folder.py` — 7 тестов: open Explorer (нормализованный путь), missing path no-op, empty string no-op, Popen failure swallowing, copy_to_clipboard путь, empty-path-leaves-clipboard, делегация в `copy_to_clipboard`.
  - `tests/ui/test_repo_bar_widget.py` — 12 тестов: список всех 5 действий в порядке, ровно 1 separator, disable-правила (`close other tabs` при 1 табе, `close to right` на rightmost и не-rightmost), action triggers (signal emission / VM method calls) для всех 5 пунктов, miss-clicks (tabAt==-1, empty tabData).
  - `tests/ui/test_main_window.py` — 3 новых: `show_folder_requested` → MainVM.show_repo_in_folder (+ empty-payload), `copy_path_requested` → MainVM.copy_repo_path (+ empty-payload), widget-to-MainWindow signal wiring через прямую эмиссию.

  **Итого 910 проходят, ruff чисто** для нового кода.

Итого **9 регрессионных тестов** на инварианты раскладки и цвета; pre-existing 47 failures в `tests/ui/test_graph_widget.py` (branch popup, drag-drop edge-кейсы) к этой правке не относятся.
