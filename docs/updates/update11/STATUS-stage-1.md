# STATUS update11 stage-1 — clone использует ключ из Settings + авто-имя папки

## Что сделано

Пользовательский фидбек (Александр Чесноков) после update10:

> запустил клонирование репозитория и ошибка:
> `git clone git@github.com:Aleks-Che/git-py.git -> C:/work/git/my-repos failed:`
> `hostkeys_find_by_key_hostfile: hostkeys_foreach failed for /c/Users/User/.ssh/known_hosts: Not a directory`
> `Host key verification failed.`
> `fatal: Could not read from remote repository.`
>
> ключ указанный в настройках для копипасты я и вставил в гитхаб.
> То есть проблема в том что при клонировании не используются ключи из настроек.

Плюс второй запрос:

> Также проверь что при указании каталога куда клонировать к примеру `C:\work\git\my-repos`
> он создаст каталог с клонируемым репозиторием

Два бага исправлены.

### Bug 1: SSH ключ из Settings не использовался

После update8 ключ лежит в `~/.ssh-py/`, а не в стандартном `~/.ssh/`.
`git` CLI по умолчанию ищет в `~/.ssh/id_*` и не находил наш ключ → auth fail.

### Bug 2: путь клонирования не дополнялся именем репозитория

Пользователь указал `C:/work/git/my-repos` и ожидал что внутри появится
`my-repos` (или `git-py`). Сейчас `git clone URL C:/work/git/my-repos`
пытается клонировать прямо в эту папку — что либо fail (если папка
существует), либо работает но имя не совпадает с ожиданием.

## Изменения

### `src/core/operations.py`

1. **Новый helper `_extract_repo_name(url: str) -> str | None`**:
   - Поддерживает SCP-style (`git@host:path/repo.git`) и URL-style
     (`ssh://git@host/path/repo.git`, `https://host/path/repo.git`,
     `git://`, `file://`).
   - Берёт последний сегмент пути, убирает `.git` суффикс.
   - Возвращает `None` если не удаётся распарсить.

2. **`_clone_via_cli` принимает `ssh_key_path: str | None = None`**:
   - Если передан — добавляет в `subprocess.run(env={...})` переменную
     `GIT_SSH_COMMAND=f'ssh -i "<path>" -o StrictHostKeyChecking=accept-new'`.
   - Это заставляет git CLI использовать именно наш ключ для SSH auth.
   - `-o StrictHostKeyChecking=accept-new` — авто-добавляет host key при
     первом подключении (не fail на "Are you sure you want to continue
     connecting?" который зависает в non-interactive subprocess).

### `src/core/repository.py`

- `RepositoryManager.clone()` принимает `ssh_key_path: str | None = None`.
- Передаёт его в `_clone_via_cli`.

### `src/viewmodels/main_viewmodel.py`

- `clone_repository` (async path) и `_execute_clone_sync` (sync path)
  читают `ssh_private_key` из config через новый helper
  `_ssh_key_path_for_clone()` и передают в `manager.clone(...)`.
- Если ключ не настроен — передаётся `None`, git CLI fallback на
  `~/.ssh/id_*` defaults.

### `src/ui/dialogs/clone_dialog.py`

- Новый helper `_resolve_clone_target(url, path)`:
  - Если путь не существует → трактуем как parent, дополняем именем
    репозитория из URL.
  - Если путь существует → оставляем как есть (пользователь знает что
    делает — папка уже пустая/готова).
  - Если последний сегмент пути уже совпадает с именем репозитория →
    не дублируем (`/tmp/work/git-py` для URL `git@...:user/git-py.git`
    остаётся как есть).
  - Если не удаётся распарсить имя репозитория из URL → оставляем путь.
- В `_on_accept` вызывается `_resolve_clone_target` перед `accepted.emit`.

## Тесты

### Изменён

- `tests/ui/test_clone_dialog.py::test_accept_with_both_fields_emits` —
  обновлён: путь теперь заканчивается именем репозитория
  (`/tmp/clone-target/repo`), чтобы update11's auto-append не менял его.
  Это правильное поведение: пользователь сам указал полный путь.

### Добавлены в `tests/core/test_repository.py`

- `test_extract_repo_name_from_scp_url`: `git@github.com:foo/bar.git` → `bar`.
- `test_extract_repo_name_from_https_url`: `https://github.com/foo/bar.git` → `bar`.
- `test_extract_repo_name_strips_dot_git`: `https://gitlab.com/user/my-project` → `my-project`.
- `test_clone_via_cli_sets_git_ssh_command_when_key_provided`:
  monkeypatch subprocess.run, проверить что env содержит
  `GIT_SSH_COMMAND` с путем ключа, `-i`, и `StrictHostKeyChecking=accept-new`.
- `test_clone_via_cli_no_ssh_command_when_no_key`:
  env is None когда ssh_key_path не передан.

### Добавлены в `tests/ui/test_clone_dialog.py`

- `test_clone_dialog_appends_repo_name_when_path_does_not_exist`:
  несуществующий `parent` → `parent/repo`.
- `test_clone_dialog_does_not_touch_existing_path`:
  существующий dir → оставляем как есть.
- `test_clone_dialog_does_not_duplicate_when_path_already_has_name`:
  путь уже заканчивается на repo name → не дублируем.

## Гейты

```
$ ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
1291 passed, 5 failed in 98s
```

5 фейлов — pre-existing `test_remove_remote_*` (задокументированы
в `update4/STATUS-stage-1.md`), НЕ связаны с update11. Проверено.

## Commits

```
fec218b stage-1(update11): clone uses Settings SSH key + auto-append repo name
```

## Что НЕ сделано (out of scope для update11)

- Не делаю "Save SSH key passphrase" в Settings — пока что
  пользователь должен сам настроить `~/.ssh/config` с `IdentityFile` и
  использовать ssh-agent.
- Не делаю CLI fallback для путей с пробелами — `subprocess.run` с
  list-args обрабатывает их нативно.
- Не показываю в UI что происходит "git clone with this key" — пользователь
  может сам заглянуть в лог (`self._log("clone", ...)`).

## Регрессии

Прогон `tests/` полный: 1291/1296 (5 pre-existing fails unrelated). Тесты в
`tests/ui/`, `tests/core/`, `tests/viewmodels/` (кроме 5 pre-existing) все зелёные.
