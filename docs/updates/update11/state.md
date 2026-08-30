# State: git-py update11 — clone использует ключ из Settings + авто-имя папки

## Статус

Работаю над планом: docs/updates/update11/PLAN.md
Статус: завершён (merged в main)
Heartbeat: 2026-08-30 17:00 UTC
Текущая задача: — (план завершён)
Текущий stage: 1
Sub-agent dispatched: none (VM broken)
Токены использовано: ~22k

## Прогресс

- [x] 0. Создан branch `feature/update11` + `docs/updates/update11/{PLAN,state}.md`
- [x] 1. Stage A — `src/core/operations.py`: `_extract_repo_name` + `ssh_key_path` (commit fec218b)
- [x] 2. Stage B — `src/core/repository.py`: `RepositoryManager.clone()` принимает ssh_key_path (commit fec218b)
- [x] 3. Stage C — `src/viewmodels/main_viewmodel.py`: `clone_repository` читает ключ из config (commit fec218b)
- [x] 4. Stage D — `src/ui/dialogs/clone_dialog.py`: `_resolve_clone_target` (commit fec218b)
- [x] 5. Stage E — Тесты: 8 новых + 1 обновлён (commit fec218b)
- [x] 6. Stage F — STATUS-stage-1.md, merge feature/update11 → main, push

## Чекпоинт

Сделано: все стадии завершены, merged в main, pushed.

## Журнал

- 2026-08-30 16:35 UTC — пользователь: clone fails "Host key verification failed".
- 2026-08-30 16:40 UTC — пользователь: путь клонирования должен создавать подпапку.
- 2026-08-30 16:50 UTC — реализация в operations.py (extract_repo_name + ssh_key_path).
- 2026-08-30 16:55 UTC — реализация в repository.py, main_viewmodel.py, clone_dialog.py.
- 2026-08-30 17:00 UTC — 8 новых тестов + 1 обновлён, 1291/1296 green, merge, push.

## Блокировки

(пусто)

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update11/state.md`. Статус «завершён». Следующая задача: scan на новые updateN+ в git-py.

## Side effects / lessons

- **GIT_SSH_COMMAND env var** — стандартный способ передать ssh параметры git CLI; безопаснее чем `-i` в args (нет риска что ключ попадёт в process list на Windows).
- **StrictHostKeyChecking=accept-new** — OpenSSH 7.6+; избегает интерактивного prompt в non-interactive subprocess.
- Sub-agent транспорт всё ещё сломан.
