# State: git-py update11 — clone использует ключ из Settings + авто-имя папки

## Статус

Работаю над планом: docs/updates/update11/PLAN.md
Статус: в работе
Heartbeat: 2026-08-30 16:45 UTC
Текущая задача: Stage A — _extract_repo_name + _clone_via_cli ssh_key_path
Текущий stage: 1
Sub-agent dispatched: none (VM broken)

## Прогресс

- [x] 0. Создан branch `feature/update11` + `docs/updates/update11/{PLAN,state}.md`
- [ ] 1. Stage A — `src/core/operations.py`: `_extract_repo_name` + `ssh_key_path` параметр
- [ ] 2. Stage B — `src/core/repository.py`: `RepositoryManager.clone` принимает ssh_key_path
- [ ] 3. Stage C — `src/viewmodels/main_viewmodel.py`: `clone_repository` читает ключ из config
- [ ] 4. Stage D — `src/ui/dialogs/clone_dialog.py`: авто-дополнение пути
- [ ] 5. Stage E — Тесты: 8 новых
- [ ] 6. Stage F — STATUS-stage-1.md, merge feature/update11 → main, push

## Чекпоинт

Сделано: branch + PLAN/state.
Осталось: реализация + тесты + merge.

## Журнал

- 2026-08-30 16:35 UTC — пользователь: clone fails, "Host key verification failed", ключ из Settings не используется.
- 2026-08-30 16:40 UTC — пользователь: путь клонирования должен создавать подпапку.

## Блокировки

Sub-agent транспорт сломан. Manual edit.

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update11/state.md`.
