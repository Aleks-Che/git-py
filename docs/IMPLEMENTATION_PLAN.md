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
- Полноценная интеграция `CommandProcessor` во все мутирующие операции.
- Кнопки Undo/Redo на тулбаре, горячие клавиши.
- Визуальная история действий (опционально, как в GitKraken).

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

**Общий прогресс:** `[██████████░░░░░░░░░░░░░░░░░░░░░]` 4 / 11 этапов (36%)

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

- [x] **Этап 3: Рабочая директория и коммиты** — _завершён_
  - Дата начала: `2026-06-03`
  - Дата завершения: `2026-06-03`
  - Комментарий: `MainViewModel` (QObject, владеет `RepositoryManager` + `CommandProcessor` + дочерними VM; сигналы `repository_changed` / `error_occurred`; `commit_changes/stage_file/unstage_file/undo/redo`); `CommitPanelViewModel` (`file_changes`, `staged_files` (восстанавливается из raw `pygit2` status flags, не из `FileStatus`), `selected_file`, `current_diff`, `commit_message` + 5 сигналов; `stage_file` через `index.add`+write, `unstage_file` через новую `core.operations.unstage_changes` — `git reset HEAD -- <path>` под капотом, потому что `index.remove` для tracked-файла оставлял `INDEX_DELETED` в staged-сете); `CommitCommand(GitCommand)` с undo через `reset("HEAD", mode="soft")`; `CommitPanel` (правая верхняя панель: `QPlainTextEdit` для сообщения, `QListWidget` с чекбоксами + статус-бейджи M/U/D/R/A/C/T/I, `QTextEdit` для диффа); WIP-узел в графе (синтетический `CommitInfo` с `sha="WIP"` prepend-ится в `GraphViewModel.refresh_graph()`; в `core/graph.py` ничего не трогали — ядро остаётся чистым); `MainWindow` (вместо прямого `RepositoryManager` — `MainViewModel`; правая сторона = вертикальный сплиттер: `CommitPanel` сверху, `CommitDetailPanel` снизу; Undo/Redo привязаны к `command_processor` + `stack_changed` → `setEnabled`); `core/operations.py`: `unstage_changes(repo, path)` (no-op если путь не в индексе, `index.remove` для unborn HEAD, иначе `git reset HEAD -- <path>`). **52 новых теста** (22 CommitPanelViewModel + 16 CommitCommand + 3 WIP в graph VM + 1 WIP в widget + 10 CommitPanel UI), итого **176/176 проходят**, `ruff check` чисто. Заглушка `BranchPanelViewModel` поднята до `QObject` с `set_repository`/`error_occurred` (для совместимости с `MainViewModel`), реальная реализация остаётся на Этап 4.

- [x] **Этап 4: Работа с ветками и переключение** — _завершён_
  - Дата начала: `2026-06-03`
  - Дата завершения: `2026-06-03`
  - Комментарий: `BranchPanelViewModel` поднят из заглушки до полноценного read-only VM: 5 свойств (`local_branches`/`remote_branches`/`tags`/`stash_list`/`current_branch_name`) + единый сигнал `references_changed`; VM не имеет мутирующих глаголов — все мутации идут через `MainViewModel` → `GitCommand` → `CommandProcessor`. `core/operations.py`: `rename_branch(repo, old, new, force=False)` (ловит `pygit2.AlreadyExistsError` → `GitError`, остальные `pygit2.GitError` → `GitError`). `commands.py`: 4 новых команды — `CheckoutCommand` (запоминает `head.shorthand` для undo, `None` на unborn HEAD → no-op), `CreateBranchCommand` (force=True при undo, no-op если ветка пре-существовала), `DeleteBranchCommand` (запоминает `target_sha` для восстановления), `RenameBranchCommand` (undo через swap имён с force=True). `MainViewModel`: 4 verb-метода (`checkout_branch`/`create_branch`/`delete_branch`/`rename_branch`) + приватный `_refresh_all_views()` (graph + commit panel + branch panel) — checkout обновляет все три панели, потому что меняется и worktree, и HEAD; undo/redo переехали на общий `_refresh_all_views`. `LeftPanel`: полноценный `QTreeWidget` с тремя группами (Branches→Local/Remote, Tags, Stash), текущая ветка выделена жирным "(HEAD)"; двойной клик по локальной ветке → checkout, по remote/tag → create branch; контекстное меню: на local — Checkout/Create/Rename/Delete, на remote/tag — Create, на stash — Apply (disabled, Этап 7); drag-and-drop с локальной ветки → заглушка `QMessageBox` "будет на Этапе 5". `MainWindow`: `LeftPanel` теперь принимает `(branch_panel_vm, main_vm)`. **60 новых тестов** (5 core/operations rename_branch + 14 BranchPanelViewModel + 19 branch_commands + 11 MainViewModel branch-методов + 11 LeftPanel UI), итого **236/236 проходят**, `ruff check` чисто. Замечания: `pygit2.Branch.rename()` не принимает kw-args — `branch.rename(new_name, force)` позиционно; `setData(column, role, value)` — 3 аргумента в PySide6. Реальный merge/rebase через drag-and-drop и force-checkout остаются на Этапы 5/6.

- [ ] **Этап 5: Слияние и rebase** — _не начато_
  - Дата начала: `—`
  - Дата завершения: `—`
  - Комментарий: `—`

- [ ] **Этап 6: Работа с удалёнными репозиториями** — _не начато_
  - Дата начала: `—`
  - Дата завершения: `—`
  - Комментарий: `—`

- [ ] **Этап 7: Stash и дополнительные инструменты** — _не начато_
  - Дата начала: `—`
  - Дата завершения: `—`
  - Комментарий: `—`

- [ ] **Этап 8: Undo/Redo и история действий** — _не начато_
  - Дата начала: `—`
  - Дата завершения: `—`
  - Комментарий: `—`

- [ ] **Этап 9: Конфигурация и темизация** — _не начато_
  - Дата начала: `—`
  - Дата завершения: `—`
  - Комментарий: `—`

- [ ] **Этап 10: Тестирование и стабилизация** — _не начато_
  - Дата начала: `—`
  - Дата завершения: `—`
  - Комментарий: `—`

### Текущий статус
- **Активный этап:** `Этап 5: Слияние и rebase`
- **Последнее обновление:** `2026-06-03`
- **Следующий шаг:** `MergeCommand/RebaseCommand с undo (reset --hard ORIG_HEAD / reflog-откат); обнаружение конфликтов через pygit2.GitError → MergeConflictError; визуальный трёхпанельный редактор конфликтов; drag-and-drop ветки A на B → контекстное меню "Merge A into B" / "Rebase A onto B" (в LeftPanel уже есть dropEvent-стаб).`
