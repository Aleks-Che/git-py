# Справочник по проекту для разработчиков и ИИ-агентов

Карта текущей рабочей копии, сверенная с кодом **2026-09-05**.
Документ помогает найти точку изменения и связанные проверки; архитектурные
требования остаются в [AGENTS.md](../AGENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md)
и [DEVELOPMENT_RULES.md](DEVELOPMENT_RULES.md).

## Начало новой сессии

1. Прочитайте [AGENTS.md](../AGENTS.md) и запрос пользователя. Выполните
   `git status --short` и просмотрите уже имеющийся diff: рабочая копия может
   содержать незавершённые изменения. Сохраняйте их при выполнении своей задачи.
2. Для структурных решений прочитайте архитектуру, правила разработки,
   [план реализации](IMPLEMENTATION_PLAN.md) и [план тестирования](TEST_PLAN.md).
3. Найдите нужные модули по карте ниже. Сверьте реализацию с ближайшими тестами;
   комментарии об этапах и датированные отчёты могут описывать прежнее поведение.
4. Проверьте интерпретатор и установку `.[dev]`. Команды установки для обеих
   платформ приведены в [README](../README.md).
5. Выберите проверки по затронутому поведению. При начале или завершении этапа
   обновляйте его чек-лист в `IMPLEMENTATION_PLAN.md`; отдельно описывайте
   фактические результаты проверки, ограничения и оставшиеся задачи.

Документация проекта ведётся на русском языке. В отчёте указывайте,
какие команды действительно запускались; результаты старого ревью не являются
результатом проверки текущей рабочей копии.

## Стек, импорты и сборка

- Python **3.10+**, PySide6 **≥ 6.5**, pygit2 **≥ 1.12**; версии и настройки
  инструментов определены в [pyproject.toml](../pyproject.toml).
- `src` — имя верхнеуровневого Python-пакета. Импортируйте, например,
  `from src.core.models import FileChange`. Не смешивайте с `from core...`:
  разные пути могут создавать разные экземпляры одного Python-модуля.
- Запуск из корня: `python -m src.main`. В `pyproject.toml` нет console script;
  сборка использует setuptools и **явный список пакетов**. При добавлении
  подпакета обновляйте `[tool.setuptools].packages`.
- Dev-зависимости: pytest, pytest-qt, pytest-asyncio, Ruff. В pytest заданы
  `testpaths = ["tests"]`, `pythonpath = ["src"]`, `qt_api = "pyside6"`.
  Editable-установка и запуск из корня сохраняют доступность пакета `src`.
- Ruff: длина строки 100, целевой Python 3.10; набор правил — в `pyproject.toml`.
- Git CLI необходим для rebase, SSH clone/fetch/push и вспомогательных операций.
  Обёртки находятся в `core/operations.py`. GitPython сейчас не используется
  и не входит в зависимости.
- [CI](../.github/workflows/ci.yml) работает на Ubuntu с Python 3.10/3.11/3.12,
  устанавливает `libgit2-dev`, запускает Ruff и pytest с offscreen Qt.
  Это не матрица проверки всех настольных ОС.

## Карта файлов

Перечислены рабочие модули; служебные `__init__.py` опущены.

```text
git-py/
├── AGENTS.md                       # краткие инструкции для агентов
├── README.md                       # обзор, установка и запуск
├── pyproject.toml                  # зависимости, пакеты, pytest, Ruff
├── LICENSE
├── .github/workflows/ci.yml
├── src/
│   ├── main.py                     # QApplication, тема, MainWindow
│   ├── core/
│   │   ├── repository.py           # RepositoryManager: репозиторий и чтение состояния
│   │   ├── operations.py           # Git-операции, CLI, staging, конфликты
│   │   ├── models.py               # CommitInfo, BranchInfo, FileChange и другие данные
│   │   ├── exceptions.py           # GitError и специализированные ошибки
│   │   ├── diff_parser.py          # разбор diff на файлы, блоки и строки
│   │   ├── staged_diff.py          # снимок HEAD → index для ИИ-сообщений
│   │   └── graph_v2.py             # раскладка графа по ячейкам, линии и цвета
│   ├── viewmodels/
│   │   ├── main_viewmodel.py       # Git, панели, конфликты и фоновые задачи
│   │   ├── commands.py             # GitCommand, CommandProcessor и конкретные команды
│   │   ├── graph_viewmodel.py      # история, stash, граф, поиск, подгрузка
│   │   ├── commit_panel_viewmodel.py # staged/unstaged, файл, diff, сообщение коммита
│   │   ├── ai_settings_viewmodel.py # фоновое получение моделей и тест LLM
│   │   ├── branch_panel_viewmodel.py # локальные/удалённые ветки, теги, stash
│   │   └── repo_tabs_viewmodel.py  # RepoTabViewModel: пути вкладок и активная вкладка
│   ├── ui/
│   │   ├── main_window.py          # панели, соединение сигналов, меню, toolbar
│   │   ├── icons.py                # иконки интерфейса
│   │   ├── widgets/
│   │   │   ├── graph_panel.py      # активный GraphTableWidget и BranchStackPopup
│   │   │   ├── graph_widget.py     # прежний GraphWidget для совместимости
│   │   │   ├── left_panel.py       # дерево веток, тегов, stash и контекстные действия
│   │   │   ├── right_panel.py      # переключение WIP / деталей коммита
│   │   │   ├── commit_panel.py     # интерфейс подготовки коммита
│   │   │   ├── ai_settings_panel.py # URL, ключ, провайдер и выбор модели
│   │   │   ├── commit_detail_panel.py # сведения и файлы выбранного коммита
│   │   │   ├── diff_view_widget.py # отображение diff и действия над строками
│   │   │   ├── image_view_widget.py # изображения: масштаб, Fit, 100%, перемещение
│   │   │   ├── file_list_model.py  # модель списка файлов
│   │   │   ├── conflict_panel.py   # конфликты, Resolve / Continue / Abort
│   │   │   ├── repo_bar_widget.py  # вкладки репозиториев
│   │   │   ├── search_bar.py       # поиск коммитов
│   │   │   ├── terminal_widget.py  # терминал через QProcess
│   │   │   ├── action_history_widget.py # история действий
│   │   │   └── log_widget.py       # журнал сообщений
│   │   └── dialogs/
│   │       ├── open_or_clone_dialog.py
│   │       ├── clone_dialog.py     # клонирование и SshKeyDialog
│   │       ├── conflict_resolution_dialog.py
│   │       ├── remote_manage_dialog.py
│   │       ├── ai_prompts_dialog.py # промпт, язык и пресеты сообщения
│   │       └── settings_dialog.py  # автор, SSH-ключи и AI
│   └── utils/
│       ├── config.py               # JSON, defaults, пути, сохранение состояния
│       ├── ai_config.py            # настройки и пресеты промптов
│       ├── ai_client.py            # OpenAI-совместимый HTTP-клиент
│       ├── theme.py                # Theme, тёмная палитра и QSS
│       ├── async_worker.py         # AsyncWorker (QRunnable) и сигналы результата
│       ├── signals.py              # общие сигналы; проверяйте фактические подключения VM
│       ├── avatar.py               # генерация аватаров
│       └── debug_mode.py           # диагностические флаги и вывод графа
├── tests/
│   ├── conftest.py                 # Git-фикстуры и настройка шрифтов Qt
│   ├── core/                      # репозитории, операции, diff, граф и регрессии
│   ├── viewmodels/                # состояние, сигналы, команды, async и Undo/Redo
│   ├── ui/                        # интеграция виджетов через pytest-qt
│   └── utils/                     # конфигурация
├── tools/
│   ├── dump_graph_cells.py         # дамп ячеек возле указанных коммитов
│   └── reproduce_31b22352_bug.py    # воспроизведение конкретной регрессии цвета
└── docs/
    ├── PROJECT_GUIDE.md            # этот справочник
    ├── ARCHITECTURE.md
    ├── DEVELOPMENT_RULES.md
    ├── IMPLEMENTATION_PLAN.md
    ├── TEST_PLAN.md
    ├── FEATURES.md                 # подробности графа и UI
    ├── GRAPH_MIGRATION_PLAN.md      # история перехода к графу по ячейкам
    ├── BUG*.md                     # разборы отдельных регрессий
    ├── REVIEW_2026-09-05*.md        # ревью и повторная проверка
    └── updates/update*/            # планы и отчёты серий доработок
```

## Связи слоёв и поток данных

```text
Виджет → метод MainViewModel → GitCommand → core.operations → RepositoryManager / Git
                               ↓
                     CommandProcessor: история
                               ↓
               обновление ViewModel → сигналы → виджеты
```

`MainViewModel` владеет текущим `RepositoryManager`, `CommandProcessor` и
ViewModel графа, коммита и веток. `MainWindow` связывает их с панелями и вкладками.
Смена репозитория сбрасывает относящиеся к нему состояние, кэши и историю команд.

| Сценарий | Где искать |
| --- | --- |
| Открытие и обновление репозитория | `MainViewModel.open_repository`, `set_repository`, `load_repository_data`, `refresh_state` |
| Статус и коммит | `CommitPanelViewModel` → `MainViewModel.commit_changes` → `CommitCommand` |
| Граф | `RepositoryManager.get_all_history` → `GraphViewModel` → `graph_v2.build_graph` → `graph_to_dicts` → `GraphTableWidget` |
| Выбор коммита и diff | `selection_changed`, `request_commit_detail`, `request_commit_file_diff` → `RightPanel` / `DiffViewWidget` |
| Ветки и tracking | `RepositoryManager.branches` → `BranchPanelViewModel` → `LeftPanel`; операции — в `MainViewModel` |
| Конфликт | `MergeConflictError` → `conflict_state_changed` → `ConflictPanel` / `ConflictResolutionDialog` |
| Undo/Redo | `MainViewModel.undo/redo` → `CommandProcessor`; обновление UI через `stack_changed` |

## Правила изменения поведения

- `core/` не импортирует PySide6. Модели и раскладка графа независимы от виджетов.
  Исключения pygit2 оборачиваются в доменные типы из `exceptions.py`.
- ViewModel использует Qt-сигналы, но не знает конкретных виджетов. Публичные
  действия называются глаголами; ошибки передаются через `error_occurred(str)`.
- Виджеты отображают состояние и передают действия. Новые изменяющие Git-операции
  оформляйте как `GitCommand` с регистрацией в `CommandProcessor`.
  Существующие исключения описаны в `DEVELOPMENT_RULES.md`; не расширяйте их
  автоматически на новые действия.
- У команды должны быть определены результат и границы отмены.
  `PushCommand` и `FetchCommand` имеют `is_noop=True` для истории Undo:
  выполняют Git-операцию, но не дают пользователю фиктивный откат.
  Ошибка Undo/Redo сохраняет команду для повторной попытки и передаётся через сигнал.
- При переписывании истории сохраняйте проверки HEAD/ref, индекса,
  tracked/untracked/ignored-файлов и дерева назначения. Undo не должен стирать
  изменения, появившиеся после исходной команды. Примеры — в `commands.py`
  и `operations.ensure_safe_tree_update`.
- Merge из меню и drag-and-drop передаёт `no_ff=True`; программный
  `merge_branch` сохраняет возможность fast-forward. Проверяйте оба сценария.
- Пути, горячие клавиши и параметры интерфейса проходят через `utils/config.py`;
  сохраняйте неизвестные ключи при записи настроек.

### SSH-ключи и настройки подключения

- Генерация по умолчанию использует `~/.ssh/git-py-ed25519`; путь задаёт
  `utils.config.default_ssh_key_path()`. Каталог создаётся при необходимости.
  Если `.ssh` занят файлом или недоступен, диалог сообщает ошибку и сохраняет
  выбранный путь; автоматического переноса в `.ssh-py` или temp нет.
- Генерация из Settings сразу сохраняет только пути ключей, даже при последующем
  Cancel. Генерация из Clone передаёт `key_generated` через MainWindow в
  `MainViewModel.configure_ssh_key()`. Остальные изменения Settings требуют OK.
- `MainViewModel` перечитывает ключ из своего `config_path` перед каждой сетевой
  операцией. Путь передаётся через Push/Pull/Fetch/FetchAndCheckoutCommand в Core;
  clone также получает его явно. Уже сохранённые пользовательские пути, включая
  `.ssh-py`, продолжают работать, если приватный ключ существует.
- `core.operations._ssh_environment()` сохраняет окружение процесса, передаёт
  приватный ключ через `GIT_SSH_COMMAND` с безопасным shell quoting и включает
  `IdentitiesOnly=yes`. Несуществующий выбранный ключ вызывает `AuthError`.
  Без выбранного ключа остаётся системный выбор SSH. Для push учитывается `push_url`.
- Регрессии: `tests/core/test_ssh_auth.py`, `tests/viewmodels/test_ssh_settings.py`,
  `tests/ui/test_ssh_setup.py` и `tests/ui/test_clone_dialog.py`. В тестах генерируемые
  ключи изолированы от домашнего каталога пользователя, внешняя сеть не используется.

### Фоновые операции

Дифф выбранного файла WIP (включая staged и Full document) при
`async_enabled=True` загружается через `utils/latest_worker.py::LatestWorker`.
Этот же исполнитель обслуживает диффы коммитов/stash в `MainViewModel`:
для каждого источника максимум два активных чтения и один заменяемый ожидающий
запрос. Повторный запрос уже загружающегося ключа не запускает второй воркер.
Смена выбора/репозитория и закрытие окна инвалидируют результаты и ошибки;
потоки не прерываются принудительно и GUI не ждёт их завершения.

`core/file_diff.py` просматривает `Diff.deltas` и создаёт патч только для
совпавшего пути через `diff[index]`. Перебор `for patch in diff` для одного
файла использовать нельзя: он вычисляет также патчи остальных файлов.
Чтение WIP и фильтрация staged-строк выполняются в Core на собственном
`RepositoryManager` воркера. Синхронный `build_diff_text` сохранён для Copy Diff.

`MainWindow` отображает только `diff_pair_ready`, чтобы не рисовать дифф
повторно по legacy-сигналу `diff_ready`. `DiffViewWidget` показывает анимацию
после 150 мс ожидания и блокирует действия над строками до результата.
Лимиты отображения: 20 000 строк, 1 000 000 символов, 4 000 символов в одной
строке. Пропуски обозначаются явно и не доступны для построчного staging;
полный исходный текст сохраняется. Регрессии: `test_file_diff.py`,
`test_file_diff_async.py`, `test_diff_loading.py` и `test_right_panel.py`.

`MainWindow` создаёт `MainViewModel(async_enabled=True)`. Значение по умолчанию
у самого ViewModel — `False`, чтобы простые тесты могли работать синхронно.

- Сетевые и долгие операции выполняются через `AsyncWorker`; фоновые мутации
  используют `_mutation_pool` с `maxThreadCount=1`. Чтение деталей и истории
  использует отдельные задачи на глобальном пуле.
- `_run_async` открывает собственный `RepositoryManager` внутри воркера.
  Не передавайте один изменяемый pygit2 handle нескольким потокам.
- Воркер выполняет команду; `CommandProcessor.record_success/record_failure`
  меняют историю в GUI-потоке после получения результата. Это относится
  также к асинхронным Undo/Redo.
- Сохраняйте `is_busy`/`busy_changed`, поколение `_async_generation` для
  отбрасывания устаревших результатов и ссылки `_active_workers` до завершения.
  Проверяйте повторный запуск, смену репозитория и закрытие окна.
- `AsyncWorker.failed` передаёт объект исключения: сначала разбирается его тип
  и контекст конфликта, затем формируется сообщение для UI.

### Конфликты и внешние изменения

Состояние конфликта принадлежит ViewModel. `ConflictPanel` вызывает Resolve,
Continue и Abort через сигналы, подключённые в `MainWindow`.
`continue_operation()` перечитывает индекс, чтобы увидеть в том числе внешний
`git add`. `complete_merge_after_conflict()` использует `CompleteMergeCommand`;
продолжение rebase — `ContinueRebaseCommand` и `RebaseContinueResult`.
`set_repository()` восстанавливает незавершённую merge/rebase-операцию с диска.

Проверяйте полный цикл: конфликт → разрешение → Continue → Undo → Redo,
а также Abort, повторное открытие и разрешение через внешний Git.
Контекст фонового конфликта должен сохранять источник и целевую ветку.

## Где менять и что проверять

### Создание тегов на графе

Меню коммита и чипа локальной/удалённой ветки содержит `Create tag here…`.
`GraphTableWidget.create_tag_requested(sha)` передаёт SHA строки в
`MainWindow._on_create_tag_here()`: диалог запрашивает имя и вызывает
`MainViewModel.create_tag()`. Создаётся локальный lightweight-тег без checkout;
после успеха обновляются граф и левая панель. Отмена, пустое имя или смена
репозитория во время диалога не создают тег. В меню WIP/stash этого пункта нет.

`CreateTagCommand` проходит через `CommandProcessor`. Core `create_tag()`
возвращает OID созданной ссылки: commit для lightweight или объект annotated-тега.
Undo передаёт его в `delete_tag(expected_target=..., missing_ok=True)`, чтобы
сохранить тег, заменённый внешней операцией; ошибки остаются в истории для повтора.
Регрессии: `tests/core/test_tags.py`, `tests/viewmodels/test_tag_commands.py`,
`tests/ui/test_graph_tags.py`.

### Просмотр изображений

Клик по изображению в правой панели открывает `ImageViewWidget` в той же центральной
области, где показывается текстовый diff. `utils/image_preview.py` распознаёт расширения
форматов Qt (включая PNG, JPEG, GIF, BMP, WebP, SVG, ICO, TIFF) и декодирует содержимое
через `QImageReader`. Анимированные файлы показываются первым кадром.
Чтение и декодирование выполняет `MainViewModel.request_file_image()` в фоне; устаревшие
результаты после смены файла, стороны или репозитория отбрасываются.

`RepositoryManager.read_file_content()` возвращает исходные байты: unstaged — рабочая
папка, staged — индекс, коммит/stash — выбранное дерево. Для удаления используется
предыдущая сторона (индекс, HEAD или первый родитель соответственно), а просмотрщик
показывает пометку `before deletion`. Чтение не меняет Git. Виджет получает готовый
`QImage`, отображает размеры и версию, поддерживает колесо мыши и перемещение
перетаскиванием. Повреждённый или неподдерживаемый файл показывает сообщение об ошибке.

Пути тестов в таблице указаны относительно `tests/`.

| Задача | Основные модули | Ближайшие проверки |
| --- | --- | --- |
| Git-операция и отмена | `core/operations.py`, `viewmodels/commands.py`, `main_viewmodel.py` | `core/test_operations.py`, соответствующие `viewmodels/test_*commands*.py`, `viewmodels/test_review_followup.py` |
| Статус, staging, diff | `repository.py`, `diff_parser.py`, `commit_panel_viewmodel.py`, `commit_panel.py`, `diff_view_widget.py` | `core/test_diff_parser.py`, `viewmodels/test_commit_panel_viewmodel.py`, `ui/test_commit_panel.py`, `ui/test_diff_view_widget.py` |
| Линии и цвета графа | `core/graph_v2.py`, `graph_viewmodel.py`, `ui/widgets/graph_panel.py` | `core/test_graph_v2*.py`, `core/test_visual_feat_regression.py`, `ui/test_graph_widget.py`, `ui/test_graph_corners.py` |
| Tracking и ветки | `repository.py`, `branch_panel_viewmodel.py`, `main_viewmodel.py`, `left_panel.py` | `viewmodels/test_branch_tracking_phase3.py`, `viewmodels/test_main_viewmodel_remotes.py`, `ui/test_left_panel.py` |
| Конфликты | `operations.py`, `commands.py`, `main_viewmodel.py`, панель и диалог конфликтов | `core/test_r1_1_merge_mid_state.py`, `viewmodels/test_review_followup.py`, `ui/test_conflict_workflow.py` |
| Async и Qt lifecycle | `async_worker.py`, `main_viewmodel.py`, `main_window.py` | `viewmodels/test_main_viewmodel_async_r2_2.py`, `viewmodels/test_commit_detail_async.py`, `ui/test_qt_lifecycle_r2_6.py` |
| Настройки, тема, вкладки | `config.py`, `theme.py`, `settings_dialog.py`, `repo_tabs_viewmodel.py` | `utils/test_config.py`, `ui/test_settings_dialog.py`, `ui/test_window_persistence.py`, `ui/test_theme.py`, `viewmodels/test_repo_tabs_viewmodel.py` |

### Команды проверок

Примеры предполагают активированное окружение с `.[dev]` и рабочий каталог
в корне проекта. Без активации заменяйте `python` на `.\.venv\Scripts\python.exe`
в PowerShell или `.venv/bin/python` в Bash.

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest tests/core/ tests/viewmodels/ tests/utils/
python -m pytest tests/ui/test_conflict_workflow.py
python -m pytest tests/core/test_graph_v2.py tests/ui/test_graph_widget.py
python -m ruff check src/ tests/
git diff --check
Remove-Item Env:QT_QPA_PLATFORM
```

Полный набор: `python -m pytest` с тем же offscreen-окружением.
Просмотр сценариев без запуска: `python -m pytest --collect-only -q`.
В Bash: `QT_QPA_PLATFORM=offscreen python -m pytest`.
На Windows для Git CLI выбирайте короткий уникальный `--basetemp` вне репозитория:
длинные пути тестовых папок могут ломать rebase/fetch с `Filename too long`.
Например, в PowerShell можно создать имя через
`Join-Path ([IO.Path]::GetTempPath()) ('gp-' + [guid]::NewGuid().ToString('N').Substring(0, 8))`.
Не указывайте существующий пользовательский каталог: pytest очищает `--basetemp`.
Выбирайте связанные с изменением тесты; полный набор нужен для общей регрессии,
а изменение документации проверяется по ссылкам, командам и соответствию коду.

### Фикстуры и изоляция

В [tests/conftest.py](../tests/conftest.py) определены:

- `tmp_git_repo` — путь к пустому временному репозиторию с веткой `main`;
- `committed_repo` — `RepositoryManager` с двумя коммитами и файлом `hello.txt`;
- `make_commit` — фабрика коммитов для нужной топологии истории.

Фикстуры используют `tmp_path` и pygit2; `core.autocrlf=False` изолирует
байтовые проверки от глобальной настройки разработчика. Сетевые сценарии
строятся на локальном bare-репозитории или моках. Не используйте рабочий
репозиторий пользователя как тестовый.

Qt-тестам нужен event loop даже без видимых окон. Для ожидания сигналов
и фоновых результатов используйте средства `qtbot`. Тесты конфигурации передают
`MainWindow(config_path=tmp_path / "config.json")`; `config_path=None` отключает
сохранение окна. Чтение конфигурации в отдельных ViewModel изолируйте отдельно.
На Windows `conftest.py` задаёт `QT_QPA_FONTDIR`, если он ещё не задан:
геометрические и пиксельные тесты зависят от доступных шрифтов.

## Конфигурация и диагностика

Источник значений — `_DEFAULT_CONFIG` в [utils/config.py](../src/utils/config.py).
Путь к JSON вычисляет `default_config_path()` через Qt `QStandardPaths`, поэтому
не зашивайте абсолютный путь пользователя в код или тест.

`load_config()` проверяет известные типы, диапазоны чисел, режим diff, пути
вкладок и вложенные AI/hotkey-поля; неверные значения заменяются defaults,
неизвестные ключи сохраняются. Невалидный UTF-8 обрабатывается как повреждённый
JSON. Размеры, передаваемые в Qt, ограничены диапазоном его целых чисел.
`save_config()` записывает уникальный временный файл рядом с целевым и атомарно
заменяет JSON; параллельные записи больше не используют один `.tmp`.
Это не блокировка нескольких экземпляров приложения: итог полного сохранения
по-прежнему определяется последней успешной записью.

`MainViewModel` загружает начальные настройки из своего `config_path` и явно
передаёт лимиты графу и `CommandProcessor`. Аргументы конструктора для auto-fetch
и порога merge имеют приоритет над JSON; `None` означает значение из конфигурации.
Неположительный интервал отключает auto-fetch. Автор, SSH, таймаут push, AI и
лимит копии Discard перечитываются из того же файла перед соответствующим
действием. У команды Discard лимит фиксируется при создании, включая будущий Redo.

Untracked-файл больше `discard_file_max_backup_bytes` остаётся на диске:
Discard сообщает `GitError` и не добавляется в Undo. Для файла в пределах лимита
работает Undo/Redo; Undo не перезаписывает файл, созданный позже по тому же пути.
Отказ чтения копии также не удаляет исходный файл. Лимит 0 позволяет копировать
только пустые файлы. Это ограничение копии в памяти, а не разрешение удалять без Undo.

Ширины колонок графа запоминаются для фактически открытого репозитория при
`repository_changed` и восстанавливаются для новой вкладки. Сохранение на выходе
объединяет посещённые вкладки с последним JSON на диске; старые варианты записи
пути поддерживаются, новые ключи нормализуются. Если запись при закрытии не
удалась, Cancel оставляет окно и терминал открытыми для повторной попытки,
Discard закрывает без сохранения. Auto-fetch останавливается при закрытии окна.
Регрессии: `tests/utils/test_config.py`, `tests/viewmodels/test_settings_audit.py`,
`tests/ui/test_settings_audit.py`.

| Ключ | Значение по умолчанию / назначение |
| --- | --- |
| `theme` | `"dark"`; при запуске сейчас применяется тёмная тема |
| `window_size`, `splitter_sizes` | `[1280, 800]`, `{}`; размеры окна и панелей |
| `graph_configs` | ширина колонок графа по пути репозитория; добавляется при сохранении |
| `recent_repos`, `active_repo` | `[]`, `null`; вкладки и активный репозиторий |
| `graph_history_limit` | `500`; размер начальной порции истории, далее подгрузка |
| `auto_fetch_enabled`, `auto_fetch_interval_ms` | `false`, `60000` |
| `push_timeout_seconds` | `1800`; общий таймаут SSH push, 1–86400 секунд; Settings → General / SSH, применяется к следующему push |
| `merge_async_threshold` | `50`; порог по числу файлов для фонового merge |
| `command_processor_history_size` | `100`; предел истории команд |
| `discard_file_max_backup_bytes` | `1048576`; предел резервной копии файла для discard |
| `diff_view_mode` | `"changes_only"`; также поддерживается `"full_document"` |
| `author_name`, `author_email` | пустые строки; профиль автора |
| `use_default_git_credentials` | `true`; имя/email из глобального Git config |
| `ssh_private_key`, `ssh_public_key` | пустые строки; пути к SSH-ключам |
| `ai` | URL, ключ, провайдер, модель, язык, пресет и промпт генерации; см. [ИИ-сообщения](AI_COMMIT_MESSAGES.md) |
| `hotkeys` | Undo `Ctrl+Z`, Redo `Ctrl+Y`, Fetch/Pull/Push `Ctrl+Shift+F/P/U`, Stash/Pop `Ctrl+Shift+S/O` |

Справочник ключей не является примером полного файла: сохраняйте текущие
значения пользователя. При отсутствии имени/email `load_author_signature()`
использует `git-py <git-py@localhost>`; при проверке авторства задавайте профиль явно.

Для диагностики графа:

```powershell
$env:GIT_PY_GRAPH_DEBUG = "1"
python -m src.main
Remove-Item Env:GIT_PY_GRAPH_DEBUG
```

`GIT_PY_GRAPH_DEBUG=1` включает строки дампа графа в stderr;
`GIT_PY_DEBUG=1` включает общий `debug_print` и считывается при импорте модуля.
Для дампа возле SHA после editable-установки:

```powershell
python tools/dump_graph_cells.py C:/path/to/repository abc1234 def5678
```

Замените путь и SHA на исследуемый репозиторий. `GIT_PY_DUMP_LIMIT` задаёт
лимит истории скрипта, по умолчанию 6000. Скрипт
`tools/reproduce_31b22352_bug.py` содержит конкретные `REPO_PATH` и SHA
внешнего репозитория; это диагностический пример, не универсальная проверка.

## Что легко перепутать

- **Активный граф:** раскладка — `core/graph_v2.py`, отображение —
  `ui/widgets/graph_panel.py::GraphTableWidget`. `core/graph.py` больше нет;
  `graph_widget.py::GraphWidget` оставлен для совместимости. Имя теста
  `test_graph_widget.py` не означает, что он проверяет только старый виджет.
- **WIP и stash:** WIP создаёт `graph_v2.build_graph`; `GraphViewModel` передаёт
  число изменений и HEAD, а stash вставляет в историю как `CommitInfo`.
  Старые записи плана описывают другую схему.
- **Remote-дубликаты:** в левой панели совпадения имени недостаточно для
  скрытия remote-ветки; учитываются tip и tracking. Проверяйте diverged upstream
  и ahead/behind, а поведение меток на графе — отдельно.
- **Reset-to-remote:** старые `ARCHITECTURE.md` и `DEVELOPMENT_RULES.md`
  описывают обход `CommandProcessor`. В текущем коде путь идёт через
  `FetchAndCheckoutCommand(force=True)` и последовательный исполнитель.
  Undo восстанавливает ссылки, но не явно отброшенные изменения рабочей директории.
  Не воспроизводите старый обход при добавлении новых операций.
- **Светлая тема:** `_LIGHT_THEME_PLACEHOLDER` не зарегистрирован как рабочая
  тема; запуск применяет dark. Наличие ключа `theme` не означает готовность
  переключателя, светлой темы или импорта/экспорта настроек.
- **Статус этапов:** чек-лист плана помечает этапы 0–8 и 10 завершёнными,
  этап 9 — в работе. Старые сводки `8 / 11`, `9 / 11` и числа тестов
  не согласованы между собой. Settings для автора/SSH уже существует.
- **Отчёты ревью:** [ревью](REVIEW_2026-09-05.md),
  [повторная проверка](REVIEW_2026-09-05_VERIFICATION.md),
  [корневой VERIFICATION.md](../VERIFICATION.md) и `updates/` фиксируют
  определённый момент. Исправления после проверки описаны в конце
  `IMPLEMENTATION_PLAN.md` и проверяются регрессионными тестами.
- **Производительность:** 5000 коммитов и раскладка < 1 секунды — цель из
  `TEST_PLAN.md`, а не гарантия измерения на любой машине. Указывайте топологию,
  размер входа и фактическое время при изменении алгоритма графа.

При изменении структуры, зависимостей, команд запуска или важных контрактов
обновляйте этот справочник и соответствующий проектный документ в той же задаче.
