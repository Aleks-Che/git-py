# PLAN update5 — UX-фикс: автозаполнение путей и обратная связь в Settings → Generate SSH Key

Дата: 2026-08-30. Источник: пользовательский фидбек (Александр Чесноков).
Контекст: при работе в `File → Settings… → Generate SSH Key…` обнаружены два UX-разрыва, из-за которых пользователь вынужден вручную копировать значения между диалогами:

1. Поле «Key file» в открывшемся `SshKeyDialog` пустое — приходится набирать полный путь вручную вместо того, чтобы получить готовый `~/.ssh/git-py-ed25519`.
2. После успешной генерации приватный и публитый ключи **не прокидываются** обратно в поля `SSH Private Key` / `SSH Public Key` родительского `SettingsDialog` — пользователь видит итог только в `SshKeyDialog` и должен сам скопировать/вставить. Это противоречит «нулевым телодвижениям».

## Задачи

### 1. Префилл пути в `SshKeyDialog`
- Файл: `src/ui/dialogs/clone_dialog.py` (`class SshKeyDialog`).
- В `__init__` подставить в `self._path_edit` дефолт: `Path.home() / ".ssh" / "git-py-ed25519"`. Заменить текущий placeholder `"C:/Users/you/.ssh/git-py-ed25519"` на актуальный дефолт из `Path.home()`. Если родитель передал значение по умолчанию через новый параметр `default_path: str | Path | None = None`, использовать его.
- Также заполнить поле `Comment` дефолтом из git config `user.email` (если доступен), иначе оставить пустым.
- Acceptance: открытие `SshKeyDialog` без аргументов даёт заполненное поле `Key file` (= `~/.ssh/git-py-ed25519`) и `Comment` (= email из git config или пусто).

### 2. Прокидывание путей из `SshKeyDialog` в `SettingsDialog`
- Файл: `src/ui/dialogs/clone_dialog.py`.
- Расширить сигнал `key_generated(str)` → `key_generated(str private_key_path, str public_key_path, str public_key_contents)`. Имя файла и путь берутся из переменной `path` в `_on_generate`.
- Файл: `src/ui/dialogs/settings_dialog.py`.
- В `_on_generate_ssh` создать `SshKeyDialog` с `default_path` (текущее значение из `self._ssh_priv_edit`, иначе дефолт из задачи 1), подключить сигнал к слоту, который **напрямую** выставляет текст в `self._ssh_priv_edit` и `self._ssh_pub_edit`. Также обновить placeholder, если поля пустые.
- Acceptance: после `Generate SSH Key…` → `Generate` → `Close` поля `SSH Private Key` / `SSH Public Key` в `SettingsDialog` заполнены путями, сгенерированными в дочернем диалоге.

### 3. Тесты
- Расширить `tests/ui/test_clone_dialog.py`:
  - `test_ssh_dialog_prefills_default_path`: создать `SshKeyDialog()` и проверить, что `_path_edit.text()` непустое и заканчивается на `/git-py-ed25519`.
  - `test_ssh_dialog_emits_paths_on_success`: с `monkeypatch`-обходом `ssh-keygen` через подмену `_find_ssh_keygen`/`subprocess.run` проверить, что сигнал `key_generated` эмитит тройку `(priv, pub, contents)`.
- Добавить `tests/ui/test_settings_dialog.py`:
  - `test_settings_dialog_generate_fills_ssh_fields`: открыть `SettingsDialog` (через `qtbot`), мокнуть `SshKeyDialog.exec` чтобы он сразу эмитил сигнал — убедиться, что `SSH Private Key` / `SSH Public Key` поля заполнены.
  - `test_settings_dialog_generate_uses_current_path_as_default`: убедиться, что `SshKeyDialog` получает `default_path` равный текущему значению `_ssh_priv_edit`.
- Гейты: `ruff check src/ tests/` чистый; `QT_QPA_PLATFORM=offscreen python -m pytest tests/ui/test_clone_dialog.py tests/ui/test_settings_dialog.py -q` зелёный; полный `pytest` без регрессий.

## Технические заметки
- `Path.home()` корректно резолвится на Windows и POSIX, дополнительных импортов не нужно.
- В `clone_dialog.py` уже есть `_find_ssh_keygen` и `subprocess.run` — переиспользуем.
- Существующий публичный API: `key_generated = Signal(str)` — это breaking change. Поскольку `SshKeyDialog` экспортируется через `__all__` в `clone_dialog.py` и используется только в `SettingsDialog` и `CloneDialog` внутри проекта, безопасно расширить сигнал.
- `SshKeyDialog` создаётся в двух местах: `SettingsDialog._on_generate_ssh` и `CloneDialog._on_generate_ssh_key` (нужно проверить и при необходимости тоже подключить сигнал).

## Этапы
- **Stage A:** SshKeyDialog — префилл и сигнал (задачи 1 + часть задачи 2).
- **Stage B:** SettingsDialog + CloneDialog — подключение сигналов (задача 2).
- **Stage C:** Тесты (задача 3) + гейты.
- **Stage D:** STATUS-stage-1.md, merge `feature/update5` → `main`, push, обновить `state.md` (`завершён`).

## Тесты / гейты
- pytest: новые и существующие тесты зелёные.
- ruff: `ruff check src/ tests/`.
