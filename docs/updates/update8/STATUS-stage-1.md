# STATUS update8 stage-1 — use ~/.ssh-py/ instead of home sibling

## Что сделано

Пользовательский фидбек (Александр Чесноков) после update7:

> если каталога .ssh нет, то надо его создать, в домашнем каталоге не надо разводить мусор

Проблема: в update7 при конфликте "`.ssh` это файл" и клике **Yes**, ключ сохранялся
прямо в `C:\Users\User\git-py-ed25519` (sibling) — это **мусор в домашнем каталоге**.

## Изменения

### `src/ui/dialogs/clone_dialog.py` — `SshKeyDialog._handle_parent_is_file`

- Yes-вариант теперь сохраняет ключ в **`~/.ssh-py/git-py-ed25519`** вместо sibling path.
- `~/.ssh-py` — именованная подпапка под home:
  - Dot-prefix → скрыта на Linux/macOS
  - `ssh` в имени → понятно назначение
  - `py` → указывает на git-py приложение (избежать коллизий)
- `~/.ssh-py` создаётся автоматически через `mkdir(parents=True, exist_ok=True)`.
- **Убрана рекурсия** через `_ensure_parent_dir` — она вызывала бесконечный цикл
  если `~/.ssh-py` тоже был файлом. Теперь логика явная: если `~/.ssh-py` существует
  как файл → fallback в tempdir **без дополнительного вопроса** (двойной prompt был бы UX-плохо).
- В Yes-варианте текст диалога обновлён:
  *"Would you like to save the key to ~/.ssh-py/git-py-ed25519 instead? (.ssh-py is a dedicated subfolder for git-py SSH keys.)"*

## Тесты

### Изменён

- `test_ssh_dialog_offers_alternative_path_on_file_conflict` — обновлён под новое
  имя `.ssh-py`. Добавлена явная проверка: ключ **НЕ** должен лежать прямо в `~/`
  (assert `not (tmp_path / "git-py-ed25519").exists()`).

### Добавлены

- `test_ssh_dialog_yes_creates_ssh_py_folder_automatically` — проверяет, что
  `~/.ssh-py` создаётся автоматически при Yes-варианте.
- `test_ssh_dialog_yes_falls_back_to_tempdir_if_ssh_py_also_blocked` —
  редкий случай когда `~/.ssh-py` тоже файл: сразу tempdir fallback, без
  бесконечной рекурсии или второго prompt.

## Гейты

```
$ ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen python -m pytest tests/ui/ -q
571 passed in 45.31s
```

Было 569 в update7 (22 → 23 → 24 в clone_dialog, плюс 5 settings + 542 other ui).
Стало 571 — два новых теста для update8 (один старый обновлён).

## Commits

```
a5fa5c1 stage-1(update8): use ~/.ssh-py/ instead of home sibling on .ssh file conflict
```

## Что НЕ сделано (out of scope для update8)

- Не делаем `.ssh-py` постоянной — пользователь может её удалить/перенести.
- Не предлагаем выбрать кастомное имя подпапки (фиксированное `.ssh-py` достаточно).
- Не проверяем, что в `~/.ssh-py/` уже нет ключа с таким же именем (стандартный
  `path.exists()` warning в `_on_generate` ловит этот случай).

## Регрессии

Прогон `tests/core/`, `tests/viewmodels/`, `tests/utils/` не делал в этой стадии
(изменения изолированы в `src/ui/dialogs/clone_dialog.py`).
