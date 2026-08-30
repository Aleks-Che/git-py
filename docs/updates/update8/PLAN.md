# PLAN update8 — не разводить мусор в домашнем каталоге на .ssh file conflict

Дата: 2026-08-30. Источник: пользовательский фидбек после update7 merge.

Контекст: после update7 на Windows у пользователя в `C:\Users\User\` существует файл с именем `.ssh` (не директория). Когда `SshKeyDialog._on_generate` детектит этот конфликт, пользователь видит question dialog с тремя кнопками:

- **Yes** (default) — сохранить ключ в **sibling path**: `C:\Users\User\git-py-ed25519`
- **No** — fallback в `tempdir/git-py-ssh/`
- **Cancel** — abort

Проблема: Yes сохраняет файл ключа **прямо в домашнем каталоге** (`C:\Users\User\`), что разводит мусор. Пользователь явно просил:

> если каталога .ssh нет, то надо его создать, в домашнем каталоге не надо разводить мусор

Правильное поведение:

1. Если `.ssh` **нет** вообще — создать `.ssh` директорию (уже работает в update6).
2. Если `.ssh` это **файл** (конфликт) — создать новую **именованную подпапку** в домашнем каталоге: `~/.ssh-py/git-py-ed25519` или подобное. Не сваливать ключ прямо в `~/`.

## Задачи

### 1. Переименовать Yes-sibling в новую подпапку
- Файл: `src/ui/dialogs/clone_dialog.py` (`_handle_parent_is_file`).
- Вместо `sibling = parent.parent / path.name` (где `path.name = "git-py-ed25519"`, parent = `.ssh`, parent.parent = home) →
  использовать **`Path.home() / ".ssh-py"`** как новую parent-папку.
- Имя файла остаётся `git-py-ed25519` (т.е. итоговый путь: `~/.ssh-py/git-py-ed25519`).
- Если `~/.ssh-py` уже существует как файл (очень редкий случай) — fallback в tempdir без дополнительного вопроса.
- В Yes-варианте question dialog обновить текст: *"Save the key to ~/.ssh-py/ instead?"*.

### 2. Альтернативное имя подпапки
- Используем `.ssh-py` (а не `.git-py-ssh` или `.ssh-keys`), чтобы:
  - Dot-prefix — скрыть на Linux/macOS
  - `ssh` в имени — понятно что это для SSH
  - `py` — указывает что это от git-py приложения (избежать коллизий с другими инструментами)

### 3. Тесты
- `test_ssh_dialog_yes_uses_named_subfolder_when_parent_is_file`:
  Создать `tmp_path/.ssh` как файл, пользователь выбирает Yes, проверить
  что финальный путь — `tmp_path/.ssh-py/git-py-ed25519` (НЕ `tmp_path/git-py-ed25519`).
- `test_ssh_dialog_yes_creates_ssh_py_folder`:
  Проверить что папка `.ssh-py` создаётся автоматически.
- `test_ssh_dialog_yes_falls_back_to_tempdir_if_ssh_py_also_blocked`:
  Если `.ssh-py` тоже нельзя создать — fallback в tempdir без нового вопроса.

### 4. Сохранить обратную совместимость с update5/6/7
- Существующий тест `test_ssh_dialog_offers_alternative_path_on_file_conflict`
  должен быть **обновлён** под новое имя `.ssh-py`.
- Все остальные тесты должны проходить без изменений.

## Этапы
- **Stage A:** Реализация в `_handle_parent_is_file` (новое имя подпапки + recursion через `_ensure_parent_dir`).
- **Stage B:** Обновить 1 старый тест + добавить 3 новых.
- **Stage C:** STATUS-stage-1.md, merge feature/update8 → main, push.

## Тесты / гейты
- pytest: все тесты (включая обновлённый) зелёные.
- ruff: `ruff check src/ tests/`.
