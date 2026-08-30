# PLAN update11 — clone использует ключ из Settings + авто-имя папки

Дата: 2026-08-30. Источник: пользовательский фидбек после update10.

Контекст: пользователь вставил `git@github.com:Aleks-Che/git-py.git`,
попробовал клонировать. Получил:

```
git clone ... failed:
Cloning into 'C:/work/git/my-repos'...
hostkeys_find_by_key_hostfile: hostkeys_foreach failed for
  /c/Users/User/.ssh/known_hosts: Not a directory
Host key verification failed.
fatal: Could not read from remote repository.
```

Два бага:

### Bug 1: SSH ключ из Settings не используется

После update8 у пользователя SSH ключ лежит в `~/.ssh-py/git-py-ed25519`,
а НЕ в стандартном `~/.ssh/`. `git` CLI по умолчанию ищет ключи в
`~/.ssh/id_ed25519`, `~/.ssh/id_rsa` и т.п. — **не находит** наш ключ.

Решение: передавать путь к ключу через environment variable
`GIT_SSH_COMMAND="ssh -i <path> -o StrictHostKeyChecking=accept-new"`.
Это безопаснее чем `-i` flag (нет риска что ключ попадёт в process list
на Windows).

Путь к ключу берётся из Settings (`ssh_private_key` config field),
который уже автоматически заполняется после `Generate SSH Key...`.

### Bug 2: путь клонирования должен содержать имя репозитория

Пользователь указал `C:/work/git/my-repos` и ожидает что в этой папке
**появится** подпапка `my-repos` (или `git-py`) с самим репозиторием.
Сейчас `git clone URL C:/work/git/my-repos` клонирует **прямо в
`C:/work/git/my-repos`** — это и есть то что делает git CLI по умолчанию.
Но если эта папка уже существует или содержит файлы — git упадёт.

Решение: в `CloneDialog._on_accept` (или в `MainViewModel._execute_clone_sync`)
дополнять путь **именем репозитория**, извлечённым из URL:
- `git@github.com:user/repo.git` → `repo`
- `https://github.com/user/repo.git` → `repo`
- `https://gitlab.com/user/repo` → `repo`

Пользователь может **отменить** автодополнение (если он **специально**
указал полный путь с именем репозитория в конце — например если такая
папка уже пустая). Решение: если путь не существует — используем как
есть; если существует — дополняем именем репозитория. Также: если
последний сегмент пути совпадает с извлечённым именем — не дополняем
(пользователь уже сделал это).

## Задачи

### 1. Извлечение имени репозитория из URL
- Файл: `src/core/operations.py` (рядом с `_url_needs_cli_fallback`).
- Функция: `def _extract_repo_name(url: str) -> str | None`.
- Поддерживает SCP-style (`git@host:path/repo.git`) и URL-style
  (`ssh://git@host/path/repo.git`, `https://host/path/repo.git`).
- Берёт последний сегмент пути, убирает `.git` суффикс.
- Возвращает `None` если не удаётся распарсить.

### 2. Clone использует ключ из Settings
- Файл: `src/core/operations.py` — `_clone_via_cli`.
- Новый параметр `ssh_key_path: str | None = None`.
- Если передан — добавляет в `subprocess.run(env={...})` переменную
  `GIT_SSH_COMMAND=f'ssh -i "{ssh_key_path}" -o StrictHostKeyChecking=accept-new'`.
  Это заставит git CLI использовать именно наш ключ для SSH auth.
- `-o StrictHostKeyChecking=accept-new` — авто-добавляет host key при
  первом подключении (не fail на "Are you sure you want to continue
  connecting?").
- Если ssh_key_path не передан — fallback на default git SSH behaviour.

### 3. `RepositoryManager.clone` принимает ssh_key_path
- Файл: `src/core/repository.py`.
- Новый kwarg `ssh_key_path: str | None = None`.
- Передаёт его в `_clone_via_cli`.
- Только для SSH URLs (HTTPS не нуждается в SSH ключе).

### 4. `MainViewModel.clone_repository` берёт ключ из config
- Файл: `src/viewmodels/main_viewmodel.py` — `clone_repository` метод.
- Читает `ssh_private_key` из config (если есть).
- Передаёт в `manager.clone(..., ssh_key_path=...)`.

### 5. `CloneDialog._on_accept` дополняет путь
- Файл: `src/ui/dialogs/clone_dialog.py`.
- Если путь существует — НЕ трогаем (пользователь знает что делает).
- Если путь НЕ существует — дополняем именем репозитория из URL.
- Если последний сегмент уже совпадает — не дополняем.

### 6. Тесты
- `test_extract_repo_name_from_scp_url`: `git@github.com:foo/bar.git` → `bar`.
- `test_extract_repo_name_from_https_url`: `https://github.com/foo/bar.git` → `bar`.
- `test_extract_repo_name_strips_dot_git`: `https://gitlab.com/foo/bar` → `bar`.
- `test_clone_via_cli_sets_git_ssh_command_when_key_provided`:
  monkeypatch subprocess.run, проверить что `env["GIT_SSH_COMMAND"]` содержит
  путь к ключу.
- `test_clone_via_cli_no_ssh_command_when_no_key`: env не содержит
  GIT_SSH_COMMAND.
- `test_clone_dialog_appends_repo_name_when_path_does_not_exist`:
  путь `C:/work/git` → `C:/work/git/my-repos` (для URL `git@...:user/my-repos.git`).
- `test_clone_dialog_does_not_touch_existing_path`:
  путь `C:/work/git/my-repos` (уже существует) — оставляем как есть.
- `test_clone_dialog_does_not_duplicate_when_path_already_has_name`:
  путь `C:/work/git/my-repos` для URL `git@...:user/my-repos.git` — не
  делаем `C:/work/git/my-repos/my-repos`.

### 7. Регрессия
- Все существующие тесты проходят без изменений.
- Полный pytest suite зелёный.

## Этапы
- **Stage A:** `src/core/operations.py` — `_extract_repo_name` + `_clone_via_cli` ssh_key_path.
- **Stage B:** `src/core/repository.py` — `RepositoryManager.clone` принимает ssh_key_path.
- **Stage C:** `src/viewmodels/main_viewmodel.py` — `clone_repository` читает ключ из config.
- **Stage D:** `src/ui/dialogs/clone_dialog.py` — авто-дополнение пути.
- **Stage E:** Тесты (8 новых).
- **Stage F:** STATUS-stage-1.md, merge feature/update11 → main, push.

## Технические заметки
- `GIT_SSH_COMMAND` env var — стандартный механизм git, работает на
  всех платформах (Linux/macOS/Windows).
- `StrictHostKeyChecking=accept-new` — добавлено в OpenSSH 7.6 (2017),
  достаточно свежо чтобы быть везде.
- `ssh -i "PATH"` — кавычки нужны для путей с пробелами.
- `Path.home() / ".ssh"` — это `C:/Users/User/.ssh` на Windows.
  У пользователя он **файл**, не папка (мы это починили в update7), но
  даже если папка — нам не нужно туда класть ключ, мы передаём
  явный путь через `-i`.
- `hostkeys_find_by_key_hostfile` warning — игнорируется (git CLI
  предупреждает что known_hosts не папка, но ssh продолжает работу).

## Тесты / гейты
- pytest: новые + существующие зелёные.
- ruff: `ruff check src/ tests/`.
