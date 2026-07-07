<!-- Файл: TEST_PLAN.md -->
# План тестирования

## 1. Модульные тесты (Core)
Располагаются в `tests/core/`. Используют `pygit2` для создания временного репозитория в `setUp`.

### Тесты `RepositoryManager`:
- Открытие существующего репозитория (проверка HEAD, существование .git).
- Инициализация нового репозитория (проверка, что появился .git, есть первый коммит если был инициализирован с Initial commit).
- Клонирование удалённого репозитория (можно замокать clone_repository или использовать локальный bare-репозиторий как исходник).
- Получение статуса файлов: создание нового файла, изменение, удаление, проверка корректности статусов (NEW, MODIFIED, DELETED, RENAMED и т.д.).
- Индексация файла (stage) и проверка состояния индекса.

### Тесты операций (operations.py):
- Создание коммита: проверка количества коммитов, сообщения, автора.
- Создание ветки, переключение, удаление.
- Слияние fast-forward и трехстороннее, проверка наличия merge-коммита.
- Искусственное создание конфликта: проверить, что выбрасывается ожидаемое исключение.
- Rebase: перемещение линейной истории, проверка порядка коммитов.
- Stash push/pop: пропадание/появление изменений.
- Push/pull/fetch с локальным bare-репозиторием (симуляция remote).

## 2. Тесты ViewModel
Располагаются в `tests/viewmodels/`. Зависят от Core, но GUI не требуется.

- Проверка сигналов: после вызова `repository_manager.open(...)` ViewModel генерирует сигнал `repository_changed`.
- При наличии незакоммиченных изменений `GraphViewModel` корректно отображает узел WIP.
- `CommitPanelViewModel.stage_file()` меняет состояние индексации, и свойство `staged_files` обновляется.
- `CommandProcessor`: выполнение команды, undo, проверка границ стека (redo невозможно без undo).
- При возникновении конфликта ViewModel выставляет флаг `merge_conflict_in_progress` и заполняет список конфликтующих файлов.

## 3. UI-тесты (интеграционные)
Располагаются в `tests/ui/`. Требуют `pytest-qt`.

### Сценарии:
- **Открытие репозитория через меню File > Open:** выбираем папку тестового репозитория, проверяем, что граф заполнился коммитами, в левой панели отобразилась ветка main.
- **Создание коммита:** изменяем файл вне программы, в UI видим WIP, ставим галочку на файле, вводим сообщение, нажимаем Commit. Проверяем, что в графе появился новый узел, и узел WIP исчез.
- **Переключение ветки:** двойной клик по ветке в левой панели – проверяем, что метка HEAD переместилась, граф обновился.
- **Слияние через контекстное меню:** создаём ветку, делаем в ней коммит, через контекстное меню сливаем в main. Проверяем появление merge-коммита.
- **Разрешение конфликта:** создаём конфликтную ситуацию, вызываем слияние, открывается диалог конфликтов, выбираем нужную сторону, разрешаем. Проверяем, что конфликтующих файлов не осталось.
- **Undo коммита:** после коммита нажимаем Ctrl+Z, убеждаемся, что коммит исчез (через `git log`).
- **Drag-and-drop ветки:** перетаскиваем ветку B на A, появляется меню выбора действия, после выполнения проверяем результат в истории.

### Branch-чипы и popup (`tests/ui/test_graph_widget.py`)
- **Collapse:** 3 ветки на одном коммите → 1 priority-чип + `▼ +N` badge; проверка через `_branch_chip_rects` (cache ровно на N записей, primary — нужного имени) + pixel-level тест `_draw_branch_column` (только один чип закрашен).
- **Приоритеты:** HEAD-ветка побеждает reachable; recently-created уходит в бакет `2` и не перебивает исходную. Проверяется через `MainViewModel.create_branch` + перерисовку графа.
- **Local vs remote-only стиль:** заливка для локальных, outline для remote-only (без локального дубликата) — проверка пикселей внутри `_branch_chip_rects`.
- **Same-name remote подавление:** `origin/main` не появляется в `branch_refs` если локальный `main` есть (проверка `_branch_chip_rects.keys()`).
- **`origin/HEAD` подавление:** `refs/remotes/origin/HEAD` не появляется в cache чипов и не показывается в popup.
- **Hover-popup:** debounce 220 ms → список всех веток; клик → `checkout_branch_requested` с full ref name; double-click — то же самое (autocomplete-style).
- **Skip-when-single:** если после фильтрации дублей в строке осталась <2 веток, popup не открывается (`widget._branch_popup is None`). Тест: `test_branch_popup_skipped_when_only_one_branch_visible` (прямой вызов `_show_branch_popup`) и `test_branch_popup_hover_timer_skipped_for_single_visible` (через `_on_hover_popup_timer` + `chip['hidden_count'] == 0`).
- **Авто-закрытие popup:** `leaveEvent` → таймер 160 ms → `widget._branch_popup is None` + `popup.isVisible() is False`.
- **Popup следует за окном:** `widget.move(+200, 0)` + `QEvent.Move` → popup.x смещается на ту же дельту.
- **Глобальный фильтр мыши:** `QApplication.installEventFilter` ловит `MouseMove` за пределами popup+чипа → закрытие.
- **Drag-and-drop:** `QDrag` с MIME `application/x-git-py-branch-chip` → drop на другой чип → сигнал `branch_dropped_on_branch`; тест проверяет, что эмитятся `merge_branch_requested`/`rebase_branch_requested` (merge с `no_ff=True`). `setAcceptDrops(True)` уже в `__init__` — без него Qt тихо отбрасывает drop, тест ловит эту регрессию.
- **Right-click context menu:** правый клик по чипу открывает то же merge/rebase меню (через `_build_branch_menu_actions`); тесты проверяют, что для local-чипа есть `Merge <X> into <Y>` + `Rebase <X> onto <Y>` + `Create Branch Here` + `Copy branch name` + `Copy commit sha`, а для remote-чипа — то же самое минус `Create Branch Here`.
- **Chip rect cache по `(row_sha, display)`:** ключи не должны сталкиваться между local `main` и remote `origin/main` на одном коммите (раньше был bug: cache брал только display-name, оба записи перезаписывались).
- **Inline «Create Branch Here»:** правый клик на локальном чипе → `QLineEdit` появляется над чипом; Enter → `create_branch_here_requested(sha, name)`; Escape/потеря фокуса → закрытие без действия.

### Reset-to-remote и no-ff merge (`tests/viewmodels/test_main_viewmodel_remotes.py`, `tests/ui/test_left_panel.py`)
- **`reset_local_branch_to_remote` happy path (create case):** нет локальной ветки → пушится branch на origin → вызов → fetch + create + checkout, HEAD оказывается на новом коммите.
- **`reset_local_branch_to_remote` happy path (hard-reset case):** локальная ветка ahead → fetch обновляет origin/branch → вызов → target sha локальной = origin tip, HEAD = merged commit, merge-коммит на origin отсутствует.
- **`reset_local_branch_to_remote` error paths:** без репо / без remote-branch в имени (`"not-a-remote-name"`) / во время `_is_busy` → `error_occurred.emit(str)` без модификации репо.
- **Double-click на remote без локальной ветки:** пушится `feature` на origin → `panel.itemDoubleClicked.emit(origin_feature_node, 0)` → `fetch_and_checkout_remote_branch` вызывается, `reset_local_branch_to_remote` НЕ вызывается.
- **Double-click на remote с локальной веткой + диалог Yes:** `QMessageBox.question` mock возвращает `Yes` → `reset_local_branch_to_remote` вызывается с правильным именем, fetch+checkout НЕ вызываются напрямую.
- **Double-click на remote с локальной веткой + диалог No:** mock возвращает `No` (default button) → ничего не вызывается (ни fetch, ни reset).

### Drag-and-drop и submenu в LeftPanel (`tests/ui/test_left_panel.py`)
- **`_BRANCH_KIND_MIME` payload:** `panel.mimeData([remote_row])` кладёт plain-text имя ветки + UTF-8 encoded `_KIND_REMOTE_BRANCH` в кастомном MIME формате `application/x-git-py-branch-kind`. Используется drop-handler'ом для различения source kind.
- **`ItemIsDragEnabled` на remote rows:** после `_update_drag_state` каждый child группы Remote имеет `flags() & Qt.ItemFlag.ItemIsDragEnabled` — иначе Qt не даёт начать drag для них.
- **Drop с remote source на local target:** drop `origin/feature` на local `topic` → mock `merge_branch` ловит вызов с `source="feature"` (нормализованный), а `fetch_and_checkout_remote_branch("origin/feature")` вызвался первым. Тест: `test_drop_from_remote_branch_fetches_then_merges`.
- **Submenu `Merge <name> into...`:** контекст-меню локальной ветки содержит `QAction` с `setMenu()` (submenu). В submenu — все другие локальные ветки (кроме self). Click по `main` в submenu триггерит `merge_branch(name, target="main", no_ff=True)`.
- **Submenu `Rebase <name> onto...`:** симметрично для rebase, click триггерит `_rebase_drop(source, target, _KIND_LOCAL_BRANCH)`.
- **Submenu от remote source:** `panel._remote_branch_actions("origin/from-upstream")` содержит `Merge origin/from-upstream into...` submenu. Click по `main` в нём → mock `fetch_and_checkout_remote_branch("origin/from-upstream")` первый, потом `merge_branch("from-upstream", "main", True)`. Тест: `test_submenu_pick_for_remote_source_fetches_first`.

## 4. Производительность
- Синтетический тест: генерируем репозиторий с 5000 коммитов (линейная история + множественные ветвления), замеряем время построения раскладки графа (должно быть < 1 секунды) и FPS при прокрутке.
- Мониторинг потребления памяти при открытии крупного репозитория.

## 5. Системные тесты
- Запуск приложения в разных ОС (Windows, macOS, Linux) через CI-матрицу, проверка, что основные сценарии работают (открытие, commit, push).
