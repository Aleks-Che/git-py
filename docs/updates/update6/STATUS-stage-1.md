# STATUS update6 stage-1 — auto-create ~/.ssh before ssh-keygen

## Что сделано

Пользовательский фидбек (Александр Чесноков) после update5: на Windows у пользователя
нет папки `.ssh` в `Path.home()`. При нажатии `Generate` в `SshKeyDialog` появлялась
ошибка:

```
ssh-keygen failed:
Saving key "C:\Users\User\.ssh\git-py-ed25519" failed: No such file or directory
```

Причина: `ssh-keygen` не создаёт родительскую директорию, а наш prefill выбирает
`~/.ssh/git-py-ed25519` без проверки существования.

## Изменения

### `src/ui/dialogs/clone_dialog.py` — `SshKeyDialog`

- Новый импорт: `import tempfile`.
- Новый helper-метод `_ensure_parent_dir(path: Path) -> tuple[Path | None, bool]`:
  - Пытается `path.parent.mkdir(parents=True, exist_ok=True)`.
  - Если primary падает с `OSError` (например, permission denied, home dir
    readonly, или просто нет прав создать `.ssh`) — fallback на
    `Path(tempfile.gettempdir()) / "git-py-ssh"`. Создаёт там директорию,
    возвращает новый путь для ключа + флаг `fell_back=True`. Показывает
    `QMessageBox.information` с указанием нового пути.
  - Если и fallback падает — `QMessageBox.warning` с деталями обоих ошибок,
    возвращает `(None, False)` для graceful abort.
- В `_on_generate` добавлен вызов `path, fell_back = self._ensure_parent_dir(path)`
  **перед** `subprocess.run`. Если `path is None` — функция возвращает раньше,
  `ssh-keygen` не вызывается.

### Совместимость с update5

- `key_generated` сигнал продолжает эмитить `(private_path, public_path, contents)` —
  если был fallback, эмитятся fallback-пути. `SettingsDialog._on_ssh_key_generated`
  корректно их подхватывает и заполняет поля автоматически.
- Никаких изменений в `SettingsDialog` или `CloneDialog` не потребовалось.

## Тесты

### Изменён

- (нет)

### Добавлены в `tests/ui/test_clone_dialog.py`

- `test_ssh_dialog_creates_missing_parent_directory` — проверяет, что
  `tmp_path / "no" / "sub" / "id_test"` (с несуществующей родительской
  директорией) успешно создаётся ДО вызова `subprocess.run`. Использует
  реальный `mkdir`, чтобы протестировать настоящую логику.

- `test_ssh_dialog_falls_back_to_tempdir_when_home_unwritable` — патчит
  instance-метод `_ensure_parent_dir` чтобы симулировать "primary failed",
  проверяет что ключ создан в `tempfile.gettempdir()/git-py-ssh/`.

- `test_ssh_dialog_aborts_when_no_directory_is_creatable` — патчит
  `_ensure_parent_dir` чтобы вернуть `(None, False)` (полный failure),
  проверяет что `QMessageBox.warning` вызван и `subprocess.run` НЕ вызван.

### Подводные камни тестирования (зафиксировано в комментариях)

1. **НЕ патчить `Path.mkdir` глобально через `monkeypatch.setattr(Path, "mkdir", ...)`**
   — Qt/ctypes использует Path внутри через нативные binding'и, глобальный
   патч вызывает segfault или deadlock. Решение: патчить instance-метод
   `dialog._ensure_parent_dir` вместо глобального `Path.mkdir`.

2. **`monkeypatch.setattr(instance, "method", func)` НЕ передаёт `self` автоматически.**
   Функция должна принимать только аргументы метода (без `self`). Если
   оставить `def fake(self, path):` — будет `TypeError: missing 1 required
   positional argument: 'path'`.

## Гейты

```
$ ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen python -m pytest tests/ui/ -q
566 passed in 43.17s
```

Было 563 в update5 (17 clone_dialog + 5 settings_dialog + 541 other ui).
Стало 566 — три новых теста для update6.

## Commits

```
cb7b5a6 stage-1(update6): auto-create ~/.ssh before ssh-keygen + fallback to tempdir
```

## Что НЕ сделано (out of scope для update6)

- Не добавлял `.ssh` директорию в git config или `ssh-add` интеграцию.
- Не показываю публичный ключ где-либо кроме `SshKeyDialog._output` (это уже было).
- Не реализую автоматический chmod 600 на приватный ключ после генерации
  (ssh-keygen сам это делает при `-N ""` на Linux/macOS; на Windows не нужно).

## Регрессии

Прогон `tests/core/`, `tests/viewmodels/`, `tests/utils/` не делал в этой стадии
(изменения изолированы в `src/ui/dialogs/clone_dialog.py`). Если нужен полный
прогон — добавить в Stage D.
