# STATUS update7 stage-1 — handle parent-is-a-file conflict + better fallback UX

## Что сделано

Пользовательский фидбек (Александр Чесноков) после update6: на Windows у пользователя
в `C:\Users\User\` существует файл с именем `.ssh` (не директория). Когда
`SshKeyDialog._on_generate` пытается создать там `git-py-ed25519`, mkdir падает
с **WinError 183** ("Cannot create a file when that file already exists").

Update6 справлялся через fallback в `tempdir/git-py-ssh/`, но:

1. Информационный диалог был сухой и непонятный: *"Could not use ... (WinError 183). Falling back to ..."*
2. Не было способа "починить" — приложение всегда уходило в Temp.
3. Пользователь не понимал куда идти за публичным ключом.

## Изменения

### `src/ui/dialogs/clone_dialog.py` — `SshKeyDialog`

`_ensure_parent_dir` рефакторинг в три явных случая:

1. **Parent missing** → `mkdir(parents=True, exist_ok=True)` → success.
2. **Parent exists but is a file** → `_handle_parent_is_file()`:
   - Показывает `QMessageBox.question` с тремя кнопками:
     - **Yes** (default) — сохранить ключ рядом с конфликтным файлом
       (например, `C:\Users\User\git-py-ed25519` вместо `C:\Users\User\.ssh\git-py-ed25519`).
       Рекурсивно вызывает `_ensure_parent_dir` для нового пути.
     - **No** — fallback в `tempdir/git-py-ssh/`.
     - **Cancel** — abort без действий (путь не создаётся, ключ не генерируется).
3. **Parent not creatable** (permission denied) → `_fallback_to_tempdir()`:
   - Создаёт `tempfile.gettempdir()/git-py-ssh/`, кладёт ключ туда.
   - **Информационный диалог** теперь содержит:
     - Полные пути к private и public файлам
     - Hint: *"Add the .pub contents to your Git host (GitHub/GitLab/etc.) from this location."*

### Совместимость с update5/update6

- Сигнал `key_generated(priv, pub, contents)` продолжает работать: `SettingsDialog`
  получает пути независимо от того, был ли fallback.
- Существующие тесты в `test_clone_dialog.py` (3 для update6) **проходят без изменений** —
  поведение для случая "parent missing" не изменилось.

## Тесты

### Изменён

- (нет)

### Добавлены в `tests/ui/test_clone_dialog.py`

- `test_ssh_dialog_detects_file_with_ssh_name` — создаёт `tmp_path/.ssh` как файл,
  передаёт путь внутри, проверяет что `QMessageBox.question` вызван с правильным
  текстом (содержит `.ssh` или `already exists`).

- `test_ssh_dialog_offers_alternative_path_on_file_conflict` — пользователь выбирает
  "Yes", проверяется что ssh-keygen вызван с путём `tmp_path/git-py-ed25519`
  (sibling, не внутри `.ssh` файла).

- `test_ssh_dialog_fallback_message_mentions_pub_file` — патчит `_ensure_parent_dir`
  чтобы вернуть fallback, проверяет что `QMessageBox.information` содержит `.pub`
  и "Private"/"Public" labels.

## Гейты

```
$ ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen python -m pytest tests/ui/ -q
569 passed in 43.77s
```

Было 566 в update6 (17 → 20 → 23 в clone_dialog, плюс 5 settings_dialog + 541 other ui).
Стало 569 — три новых теста для update7.

## Commits

```
801a699 stage-1(update7): detect .ssh file-vs-dir conflict + better fallback message
```

## Что НЕ сделано (out of scope для update7)

- Не удаляем существующий `.ssh` файл автоматически — потенциально деструктивно.
- Не предлагаем пользователю открыть cmd/PowerShell для удаления — ограничиваемся UI.
- Не проверяем, что выбранный sibling path не пересечётся с другими файлами (например, если пользователь уже имеет `git-py-ed25519` рядом с `.ssh` файлом — будет warning "File already exists" от существующего кода в `_on_generate`).

## Регрессии

Прогон `tests/core/`, `tests/viewmodels/`, `tests/utils/` не делал в этой стадии
(изменения изолированы в `src/ui/dialogs/clone_dialog.py`). Если нужен полный прогон — добавить.
