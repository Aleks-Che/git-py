<!-- Файл: ARCHITECTURE.md -->
# Архитектура GitKraken-подобного клиента на Python

## Общие принципы
- **Язык:** Python 3.10+
- **Графический фреймворк:** PySide6 (Qt for Python) для кроссплатформенного нативного интерфейса с богатыми возможностями кастомизации виджетов.
- **Работа с Git:** библиотека pygit2 (обвязка над libgit2) для прямых и быстрых операций с репозиторием без вызова CLI. В качестве fallback/дополнения можно использовать GitPython для отдельных действий.
- **Архитектурный паттерн:** MVVM (Model-View-ViewModel) с активным использованием сигналов и слотов Qt.
- **Отмена действий:** паттерн Command, каждая мутирующая операция с Git инкапсулируется в объект команды с методами execute/undo, все выполняются через центральный CommandProcessor.

## Слои приложения (снизу вверх)

### 1. Core / Git Service (libgit2 wrapper)
Изолирует прямое взаимодействие с pygit2. Не зависит от GUI.
**Модули:**
- `core/repository.py` – класс `RepositoryManager`: открытие, создание, клонирование, получение состояния (HEAD, ветки, теги, stash, submodules, статус файлов).
- `core/operations.py` – функции-обёртки: commit, branch create/delete/rename, checkout, merge, rebase, cherry-pick, revert, reset, stash push/pop/apply, push, pull, fetch, remote management.
- `core/models.py` – структуры данных: CommitInfo, BranchInfo, TagInfo, FileStatus, DiffEntry, StashInfo – сериализуемые объекты без ссылок на Git-объекты.
- `core/diff_parser.py` – получение диффов в удобном виде.

### 2. Application Logic (ViewModel-слой)
Хранит состояние UI, обрабатывает команды пользователя, вызывает Core и управляет undo/redo.
**Модули:**
- `viewmodels/main_viewmodel.py` – центральный диспетчер, владеет `RepositoryManager`, `CommandProcessor`, предоставляет свойства/сигналы для всех панелей.
- `viewmodels/graph_viewmodel.py` – вычисление структуры графа коммитов (DAG), маппинг веток и тегов, обновление при fetch/изменениях.
- `viewmodels/commit_panel_viewmodel.py` – состояние WIP (изменённые/индексированные файлы), управление staging/unstaging, сообщение коммита.
- `viewmodels/branch_panel_viewmodel.py` – список локальных/удалённых веток, контекстные операции.
- `commands.py` – иерархия классов команд: CommitCommand, MergeCommand, RebaseCommand, CheckoutCommand, StashCommand и т.д., наследующих от базового `GitCommand`.

### 3. UI / View (Qt Widgets)
Полностью пассивные компоненты, только отображают данные из ViewModel и передают пользовательские действия.
**Модули:**
- `ui/main_window.py` – главное окно: меню, тулбар, размещение основных панелей (левая, граф, правая, нижний терминал).
- `ui/widgets/graph_widget.py` – кастомный виджет на `QGraphicsView/QGraphicsScene`, рендерит коммиты как узлы, ветки как цветные линии, позволяет клик, перетаскивание (drag&drop для merge/rebase).
- `ui/widgets/graph_panel.py` – `GraphTableWidget` (активный рендер графа), `BranchStackPopup` (frameless hover-popup со списком веток на multi-branch чипе). Каждая multi-branch строка сворачивается в один priority-чип + `▼`-индикатор (`_branch_priority_key`, `_suppress_dup_remotes`); popup поднимается через debounce `_HOVER_POPUP_DELAY_MS`; тройная защита закрытия (`leaveEvent` + глобальный event-filter + `ApplicationDeactivate`); eventFilter на родителе ловит `Move` для следования за окном.
- `ui/widgets/commit_panel.py` – панель коммита: список файлов с чекбоксами, просмотр диффа выбранного файла, поле сообщения, кнопка commit.
- `ui/widgets/left_panel.py` – дерево ссылок (QTreeWidget) с группировкой по типам: локальные ветки, удалённые, теги, stash. Группа `Remote` скрывается целиком (`setHidden(True)`) если `BranchPanelViewModel._suppress_same_name_remotes` оставила её пустой.
- `ui/widgets/terminal_widget.py` – встроенный терминал (на основе QTermWidget или эмуляция через QProcess).
- `ui/dialogs/` – диалоги: разрешения конфликтов, настройки, клонирования, создания репозитория и т.д.

### 4. Инфраструктура
- `utils/signals.py` – централизованные сигналы приложения (например, `repository_changed`, `operation_finished`).
- `utils/config.py` – работа с конфигурацией (темы, настройки Git, расположение панелей).
- `utils/async_worker.py` – запуск длительных Git-операций в отдельных потоках (QThread) и передача результата через сигналы, чтобы не морозить интерфейс.
