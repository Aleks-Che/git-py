<!-- Файл: DEVELOPMENT_RULES.md -->
# Правила разработки

## 1. Разделение ответственности
- Код в `core/` не должен импортировать PySide6. Только чистый Python + pygit2.
- ViewModel-слои используют сигналы/слоты Qt, но не знают о конкретных виджетах.
- Виджеты только читают свойства ViewModel и вызывают методы, не хранят состояние Git-операций.

## 2. Команды и Undo/Redo
- Каждое действие, изменяющее репозиторий (commit, merge, rebase, branch creation, checkout), должно быть реализовано как потомок `GitCommand`.
- Объект команды получает всё необходимое для выполнения и отката.
- Все команды проходят через `CommandProcessor`, который хранит стек выполненных команд и вызывает `execute()` / `undo()`.
- Тулбар Undo/Redo связан именно с `CommandProcessor`, а не напрямую с операциями.
- **Destructive операции, которые нельзя откатить через undo** (например, `reset_local_branch_to_remote` — `git reset --hard` обрезает reflog path так, что CommProcessor уже не восстановит утраченные коммиты) **обязаны** идти в обход `CommandProcessor`. Вместо undo через стек они gated'ятся на confirmation dialog (`QMessageBox.question` с дефолтной кнопкой `No` для защиты от случайного Enter). UI вызывает `QMessageBox.question` перед destructive VM-методом; VM-метод сам по себе не показывает диалогов.
- **UI merge-пути передают `no_ff=True`.** Программные вызовы `merge_branch()` сохраняют git-совместимое поведение (fast-forward когда возможен). Drag-and-drop и контекстное меню на графе и в `LeftPanel` форсируют merge-коммит через `no_ff=True` — иначе FF молча перемещает ref и в графе ничего не появляется.

## 3. Асинхронность
- Любая операция, работающая с сетью (push/pull/fetch/clone) или потенциально долгая (rebase, большой merge), выполняется в фоновом потоке (`QThread` или `QRunnable`).
- Прямые обращения к диску (статус файлов, чтение диффов) могут выполняться в основном потоке, но должны быть максимально быстрыми. Для крупных репозиториев допустимо выносить и их в поток.
- При выполнении асинхронной операции виджеты показывают индикатор загрузки (spinner) и блокируют повторный запуск.

## 4. Обработка ошибок
- Все исключения из pygit2 должны перехватываться в `core/` и оборачиваться в доменные исключения (`GitError`, `MergeConflictError`, `AuthError` и т.д.).
- ViewModel перехватывает эти исключения и пробрасывает в UI через сигнал `error_occurred(str)`, а не через стандартные исключения Python.
- Пользователь всегда видит понятное сообщение, а не трейсбек.

## 5. Тестируемость
- ViewModel и Core тестируются без запуска GUI.
- В тестах используется временный Git-репозиторий (создаваемый через `tempfile.TemporaryDirectory` + `pygit2.init_repository`).
- Для UI-тестов применяется `pytest-qt`, который позволяет симулировать клики и проверять состояние виджетов.

## 6. Стиль кода
- Следовать PEP 8.
- Имена методов ViewModel, предоставляющих команды, должны быть глаголами: `commit_changes()`, `checkout_branch(name)`, `merge_branch(source, target)`.
- Названия виджетов — с суффиксом `Widget/Panel/Dialog`.

## 7. Конфигурируемость
- Все пути, горячие клавиши, параметры тем выносятся в отдельные конфиги (JSON/YAML), загружаемые через `utils/config.py`.
- Размеры и позиции панелей сохраняются при выходе и восстанавливаются при запуске.

## Operations outside GitCommand (documented exceptions)

These operations are direct mutations that bypass `GitCommand` (and therefore the undo stack).
Reason: undoing them is either meaningless (no recovery path) or infeasible for ergonomics reasons.

- **`_move_branch_ref(repo, name, target_sha)`** — internal ref move used by synchronous branch ops that ARE wrapped in `GitCommand` (which records their own rollback path). Bypassing inside the helper is allowed because the wrapping command owns the rollback semantics.
- **`delete_file_from_disk(path)`** — user has explicitly opted out of undo via the destructive-action confirm dialog. Recorded in `ActionHistoryWidget`'s non-undoable history instead.
- **`apply_stash_file(stash_oid, path)` / `apply_stash_files(...)`** — application of a stash to a specific path is irreversible for the worktree side; the original stash entry remains in the stash list, so the user can re-apply from there if desired. Undo-via-CommandProcessor wouldn't help.
- **`stage_file(path)` / `unstage_file(path)`** — single-path index mutations triggered by every checkbox click. Bypassing for ergonomics (CommandProcessor push per click would flood the undo stack). Batch operations still use `GitCommand`.
