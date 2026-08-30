# PLAN update7 — лучше обрабатывать конфликт "файл с именем .ssh"

Дата: 2026-08-30. Источник: пользовательский фидбек после update6 merge.

Контекст: после update6 на Windows у пользователя в `C:\Users\User\` **.ssh уже существовал как файл** (не как папка). Возможно артефакт от Cygwin/MSYS, либо какой-то софт создал файл-заглушку.

Когда `SshKeyDialog._on_generate` пытался выполнить `path.parent.mkdir(parents=True, exist_ok=True)` — mkdir **падал** с WinError 183 ("Cannot create a file when that file already exists"), потому что target существует, но не как директория.

Поведение update6:
1. mkdir падает с WinError 183 → fallback на `tempdir/git-py-ssh/`
2. Показывает информационный диалог: *"Could not use C:\Users\User\.ssh (WinError 183). Falling back to C:\Temp\git-py-ssh"*
3. Ключ создаётся в `C:\Temp\git-py-ssh\git-py-ed25519` (и работает)
4. Поля SSH Private/Public Key в SettingsDialog заполняются путём к fallback-ключу

Проблема UX:
- Пользователь не понимает что произошло и почему. "Файл .ssh уже существует" звучит странно.
- Непонятно, куда теперь смотреть за публичным ключом для добавления в GitHub.
- Если пользователь хочет чтобы ключ лежал именно в `~/.ssh/` (а не в Temp), у него нет инструментов это починить.

Решение:

### Задачи

1. **Детектировать конфликт "путь существует, но не как директория"** и обрабатывать отдельно:
   - Если `path.parent.exists() and not path.parent.is_dir()` — это файл, не папка.
   - Показать warning с конкретным объяснением: "Cannot use ~/.ssh because a file with that name exists. Please remove or rename it, or choose a different path."
   - Дать пользователю выбор через Yes/No диалог: "Use a different path" (по умолчанию — `~/git-py-ed25519` рядом с `.ssh` файлом) или "Use tempdir".

2. **Улучшить информационный диалог fallback**:
   - В update6 сообщение: *"Could not use ... Falling back to ..."* — сухое.
   - В update7: показать путь к **.pub файлу** в fallback-директории, инструкцию как его добавить в GitHub/GitLab, и почему так вышло.

3. **Тесты**:
   - `test_ssh_dialog_detects_file_with_ssh_name`: создать файл с именем `.ssh` в `tmp_path`, передать путь внутри, проверить warning с конкретным текстом.
   - `test_ssh_dialog_offers_alternative_path_on_file_conflict`: пользователь выбирает "Use different path" → путь меняется на `tmp_path / "git-py-ed25519"` (без `.ssh/`).
   - `test_ssh_dialog_fallback_message_mentions_pub_file`: информационный диалог содержит `.pub` в тексте.

### Что НЕ делаем
- Не удаляем существующий `.ssh` файл автоматически — это деструктивно и опасно.
- Не пытаемся использовать файл `.ssh` как директорию (это невозможно).
- Не показываем пользователю инструкцию как открыть cmd/PowerShell — ограничиваемся UI.

### Этапы
- **Stage A:** Реализация в `SshKeyDialog._ensure_parent_dir`: детект file-vs-dir конфликта, Yes/No диалог с альтернативным путём.
- **Stage B:** Тесты + гейты.
- **Stage C:** STATUS-stage-1.md, merge feature/update7 → main, push.

### Тесты / гейты
- pytest: новые + существующие тесты зелёные.
- ruff: `ruff check src/ tests/`.
