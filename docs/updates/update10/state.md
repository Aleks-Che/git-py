# State: git-py update10 — clone SSH URL через system git CLI

## Статус

Работаю над планом: docs/updates/update10/PLAN.md
Статус: в работе
Heartbeat: 2026-08-30 16:15 UTC
Текущая задача: Stage A — _clone_via_cli helper
Текущий stage: 1
Sub-agent dispatched: none (VM broken)

## Прогресс

- [x] 0. Создан branch `feature/update10` + `docs/updates/update10/{PLAN,state}.md`
- [ ] 1. Stage A — `src/core/operations.py`: добавить `_clone_via_cli`
- [ ] 2. Stage B — `src/core/repository.py`: обновить `RepositoryManager.clone()`
- [ ] 3. Stage C — Тесты: 4 новых
- [ ] 4. Stage D — STATUS-stage-1.md, merge feature/update10 → main, push

## Чекпоинт

Сделано: branch + PLAN/state.
Осталось: реализация + тесты + merge.

## Журнал

- 2026-08-30 16:10 UTC — пользователь сообщил: clone `git@github.com:...` → "unsupported URL protocol".

## Блокировки

Sub-agent транспорт сломан. Manual edit.

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update10/state.md`.
