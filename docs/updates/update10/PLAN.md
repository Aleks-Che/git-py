# PLAN update10 — clone SSH URL через system git CLI (pygit2 без libssh2 на Windows)

Дата: 2026-08-30. Источник: пользовательский фидбек.

Контекст: пользователь скопировал SSH ссылку с GitHub
(`git@github.com:Aleks-Che/git-py.git`) и пытается клонировать через
приложение. Получает:

```
Failed to clone git@github.com:Aleks-Che/git-py.git -> C:/work/git/my-repos:
unsupported URL protocol
```

Причина: **prebuilt pygit2 wheels на Windows собраны без libssh2**. pygit2
(libgit2) понимает только URL с протоколом `ssh://...`, но НЕ понимает
SCP-style `git@host:path`. Все остальные SSH-операции проекта
(push/pull/fetch) уже обходят это через `_url_needs_cli_fallback` →
`_push_via_cli`/`_fetch_via_cli` (см. `src/core/operations.py:2307+`).
Но `clone` эту проверку не делал — `RepositoryManager.clone()` напрямую
вызывает `pygit2.clone_repository(url, path)`.

## Задачи

### 1. `_clone_via_cli` helper в `src/core/operations.py`
- Шаблон: такой же как `_fetch_via_cli` / `_push_via_cli` (subprocess.run
  + git CLI).
- Сигнатура: `def _clone_via_cli(url: str, path: str, bare: bool) -> None`.
- Args: `["clone", url, path]` (+ `"--bare"` если bare).
- При failure (non-zero exit) — `_wrap_remote_error(url, ...)` или новый
  `CloneError` чтобы дать доменный error.
- При success — return; caller (`RepositoryManager.clone`) сам подхватит
  через `pygit2.Repository(path)` чтобы загрузить уже склонированный
  репозиторий.

### 2. Обновить `RepositoryManager.clone()` в `src/core/repository.py`
- Перед `pygit2.clone_repository(url, path)`:
  - Если `_url_needs_cli_fallback(url)` → `_clone_via_cli(url, path, bare)`,
    потом `self._open(path)` чтобы подхватить репозиторий.
  - Иначе → pygit2 напрямую (как сейчас).
- ВАЖНО: после CLI clone мы НЕ получаем pygit2.Repository объект напрямую
  — нужно либо (a) `pygit2.Repository(path)` отдельно, либо (b) использовать
  только что созданный on-disk repo.
- Подход (a) проще и единообразнее — `pygit2.Repository(path)` работает
  без SSH wheels, потому что это локальный FS доступ.

### 3. Тесты
- `test_clone_via_cli_runs_git_clone_for_ssh_url`:
  monkeypatch `subprocess.run`, проверить что для `git@github.com:foo/bar.git`
  вызывается `git clone <url> <path>`.
- `test_clone_falls_back_to_cli_when_url_is_ssh`:
  интеграционный: убедиться что `_clone_via_cli` вызывается для SSH URL.
- `test_clone_uses_pygit2_for_https_url`:
  регрессия: для `https://...` НЕ должен вызываться CLI.
- `test_clone_via_cli_propagates_failure`:
  если `git clone` падает (non-zero exit) — должен быть `CloneError` или
  `GitError` с осмысленным сообщением.

### 4. Сохранить обратную совместимость
- Существующие тесты на clone должны проходить без изменений.
- API `RepositoryManager.clone()` остаётся (signature не меняется).

## Этапы
- **Stage A:** `src/core/operations.py` — добавить `_clone_via_cli` helper.
- **Stage B:** `src/core/repository.py` — обновить `RepositoryManager.clone()`.
- **Stage C:** Тесты (4 новых).
- **Stage D:** STATUS-stage-1.md, merge feature/update10 → main, push.

## Технические заметки
- `subprocess.run(["git", "clone", url, path], capture_output=True, ...)` —
  использовать git из PATH (он сам разберётся с SSH через user-config).
- `_url_needs_cli_fallback` уже в operations.py — переиспользуем.
- Если `git` нет в PATH (теоретически) — fallback fail с понятной ошибкой.
- В Windows может быть нужен bash quoting для пути с пробелами — `subprocess.run`
  обрабатывает это нативно через list-args.
- `pygit2.Repository(path)` после CLI clone подхватит on-disk repo. Это
  нужно чтобы `self._repo` и `self._path` были установлены как обычно.

## Тесты / гейты
- pytest: новые + существующие зелёные.
- ruff: `ruff check src/ tests/`.
