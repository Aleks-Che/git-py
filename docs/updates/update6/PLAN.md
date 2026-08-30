# PLAN update6 — auto-create ~/.ssh directory before ssh-keygen

Дата: 2026-08-30. Источник: пользовательский фидбек после update5 merge.
Контекст: после UX-фикса update5 (prefill `~/.ssh/git-py-ed25519` в SshKeyDialog),
на Windows у пользователя **нет** папки `.ssh` в `Path.home()`. При нажатии
`Generate` `ssh-keygen` возвращает ошибку:

```
Saving key "C:\Users\User\.ssh\git-py-ed25519" failed: No such file or directory
```

Нужно создавать `.ssh` директорию автоматически (с `mkdir(parents=True, exist_ok=True)`),
если её нет, **до** запуска `ssh-keygen`. Дополнительно: если у пользователя
явно нет прав на запись в `Path.home()` — fallback на temp-директорию
(`tempfile.gettempdir() / "git-py-ssh"`) + warning.

## Задачи

### 1. Создать `.ssh` директорию перед `ssh-keygen`
- Файл: `src/ui/dialogs/clone_dialog.py` (`SshKeyDialog._on_generate`).
- Перед `subprocess.run([ssh_keygen, ...])` вычислить `parent_dir = path.parent`
  и сделать `parent_dir.mkdir(parents=True, exist_ok=True)`.
- Если `mkdir` падает с `OSError` (например, permission denied) — fallback
  на `tempfile.gettempdir() / "git-py-ssh"`, попытка создать там, обновить
  `path` и `pub_path`. Если и там падает — прервать с понятной ошибкой
  ("Cannot create directory for SSH key: ...").
- Если fallback сработал — показать `QMessageBox.information` с новым путём,
  чтобы пользователь знал, где теперь лежит ключ.

### 2. Тесты
- Новый тест `test_ssh_dialog_creates_missing_parent_directory`:
  использовать `tmp_path` как `Path.home()` через monkeypatch, передать путь
  с НЕ существующей родительской директорией (например, `tmp_path / "no" / "sub" / "key"`),
  замокать `subprocess.run`, проверить, что директория создана до запуска
  ssh-keygen.
- Новый тест `test_ssh_dialog_falls_back_to_tempdir_when_home_unwritable`:
  замокать `Path.home()` чтобы возвращал unwritable путь, проверить что
  fallback на tempdir сработал и `path` обновился.
- Обновить `test_ssh_dialog_success` если требуется (минимально).

### 3. Регрессия
- Прогнать полный pytest suite `tests/ui/` — все тесты зелёные.
- `ruff check src/ tests/` чистый.

## Технические заметки

- `pathlib.Path.mkdir(parents=True, exist_ok=True)` — стандартный Python,
  не нужны новые импорты.
- `tempfile.gettempdir()` — переиспользуем для fallback.
- На Windows у пользователя `Path.home()` часто возвращает `C:\Users\User`
  без `.ssh`. На Linux/macOS папка `.ssh` обычно есть.
- Тест `test_ssh_dialog_prefills_default_path` остаётся без изменений —
  prefill это только про текст в поле, не про создание файлов.

## Этапы
- **Stage A:** Реализация в `SshKeyDialog._on_generate` (задача 1).
- **Stage B:** Тесты (задача 2) + гейты (задача 3).
- **Stage C:** STATUS-stage-1.md, merge `feature/update6` → main, push, обновить state.md.

## Тесты / гейты
- pytest: новые + существующие тесты зелёные.
- ruff: `ruff check src/ tests/`.
