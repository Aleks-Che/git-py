# STATUS update10 stage-1 — clone SSH URL через system git CLI

## Что сделано

Пользовательский фидбек (Александр Чесноков):

> при клонировании вставляю стандартную ssh ссылку для клонирования которую скопировал с гитхаба `git@github.com:Aleks-Che/git-py.git`, и не хочет клонировать.
> Failed to clone `git@github.com:Aleks-Che/git-py.git` -> C:/work/git/my-repos: unsupported URL protocol

Причина: prebuilt `pygit2` wheel на Windows собран **без libssh2**, поэтому libgit2
не понимает SCP-style `git@host:path`. Push/pull/fetch проекта уже обходили
это через `_url_needs_cli_fallback` + `_push/_fetch_via_cli` — но `clone` нет.

## Изменения

### `src/core/operations.py` — новый helper `_clone_via_cli`

- Шаблон: такой же как `_fetch_via_cli` / `_push_via_cli` (subprocess.run +
  git CLI).
- Сигнатура: `def _clone_via_cli(url: str, path: str, bare: bool = False) -> None`.
- Args: `["clone", url, path]` (+ `"--bare"` если bare).
- На success — `path` теперь содержит склонированный on-disk repo;
  caller открывает его через `pygit2.Repository(path)` (только локальный FS,
  SSH transport не нужен).
- На failure — парсит stderr по тем же эвристикам что `_fetch_via_cli`:
  - "Permission denied" / "publickey" / "authentication" → `AuthError`
  - "Could not resolve hostname" / "Connection refused" / "timed out" /
    "network" / "no route" → `NetworkError`
  - Остальное → `GitError` с полным stderr в сообщении
- `subprocess.TimeoutExpired` → `GitError("timed out")` (timeout=300s)
- `OSError` (git not in PATH) → `GitError("failed to start")`
- `shutil.which("git")` отсутствует → `GitNotInstalledError`

### `src/core/repository.py` — `RepositoryManager.clone()`

- Импортирует локально `_clone_via_cli` и `_url_needs_cli_fallback` из
  `src.core.operations` (локальный import чтобы избежать circular).
- Перед `pygit2.clone_repository(url, path)`:
  - Если `_url_needs_cli_fallback(url)` → `_clone_via_cli(url, path, bare)`,
    затем `self._repo = pygit2.Repository(path)` чтобы подхватить on-disk
    репозиторий (локальный FS доступ — libssh2 не нужен).
  - Если fallback `_clone_via_cli` упал — `_repo` и `_path` сбрасываются
    в None, пробрасывается доменная ошибка.
  - Иначе (HTTPS / git:// / file://) → pygit2 напрямую как раньше.
- API `clone()` не изменился — обратная совместимость сохранена.

## Тесты

### Изменён

- (нет)

### Добавлены в `tests/core/test_repository.py`

- `test_clone_routes_ssh_url_through_git_cli` — `git@github.com:...` URL →
  проверка что вызван subprocess.run с `["...git", "clone", url, path]`,
  pygit2 НЕ вызван.
- `test_clone_uses_pygit2_for_https_url` — регрессия: `https://github.com/...`
  → `pygit2.clone_repository` вызван, subprocess НЕ вызван.
- `test_clone_via_cli_propagates_auth_error` — stderr содержит
  "Permission denied (publickey)" → `AuthError` с URL в сообщении.
- `test_clone_via_cli_propagates_network_error` — stderr содержит
  "Could not resolve hostname" → `NetworkError` с URL в сообщении.

## Гейты

```
$ ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
1283 passed, 5 failed in 104s
```

5 фейлов — pre-existing `test_remove_remote_*` баги (задокументированы
в `update4/STATUS-stage-1.md`), НЕ связаны с update10. Проверено через
`git stash` что они падают и без моих изменений.

## Commits

```
0d9b266 stage-1(update10): route SSH clone URLs through system git CLI
```

## Что НЕ сделано (out of scope для update10)

- Не делаю CLI fallback для `git://` URL — pygit2 их поддерживает.
- Не использую `--no-tags`, `--depth`, `--branch` опции — не было требования.
- Не делаю progress reporting для long clones (это уже есть в
  `MainViewModel._execute_clone_sync` через отдельный канал).
- Не добавляю config опцию "use CLI for SSH" — это hardcoded поведение
  потому что pygit2 wheels на Windows просто не поддерживают SSH.

## Регрессии

Прогон `tests/` полный: 1283/1286 (5 pre-existing fails unrelated). Тесты в
`tests/ui/` и `tests/core/` (кроме 5 pre-existing) все зелёные.
