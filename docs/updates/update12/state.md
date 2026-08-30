# State: git-py update12 — всегда дополнять имя репозитория к пути

## Статус

Работаю над планом: docs/updates/update12/PLAN.md
Статус: в работе
Heartbeat: 2026-08-30 17:10 UTC
Текущая задача: Stage A — упростить _resolve_clone_target
Текущий stage: 1
Sub-agent dispatched: none (VM broken)

## Прогресс

- [x] 0. Создан branch `feature/update12` + `docs/updates/update12/{PLAN,state}.md`
- [ ] 1. Stage A — `_resolve_clone_target`: убрать `target.exists()` проверку
- [ ] 2. Stage B — Обновить тест `test_clone_dialog_does_not_touch_existing_path`
- [ ] 3. Stage C — STATUS-stage-1.md, merge feature/update12 → main, push

## Чекпоинт

Сделано: branch + PLAN/state.
Осталось: реализация + тест + merge.

## Журнал

- 2026-08-30 17:05 UTC — пользователь: при клонировании в `C:\work\git\my-repos` склонировался ровно туда, а не в `git-py`.

## Блокировки

Sub-agent транспорт сломан. Manual edit.

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update12/state.md`.
