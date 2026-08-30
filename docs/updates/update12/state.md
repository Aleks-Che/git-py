# State: git-py update12 — всегда дополнять имя репозитория к пути

## Статус

Работаю над планом: docs/updates/update12/PLAN.md
Статус: завершён (merged в main)
Heartbeat: 2026-08-30 17:15 UTC
Текущая задача: — (план завершён)
Текущий stage: 1
Sub-agent dispatched: none (VM broken)
Токены использовано: ~24k

## Прогресс

- [x] 0. Создан branch `feature/update12` + `docs/updates/update12/{PLAN,state}.md`
- [x] 1. Stage A — `_resolve_clone_target`: убрать `target.exists()` (commit efc3252)
- [x] 2. Stage B — Обновить тест `test_clone_dialog_appends_repo_name_to_existing_path` (commit efc3252)
- [x] 3. Stage C — STATUS-stage-1.md, merge feature/update12 → main, push

## Чекпоинт

Сделано: все стадии завершены, merged в main, pushed.

## Журнал

- 2026-08-30 17:05 UTC — пользователь: при указании `C:\work\git\my-repos` склонировался ровно туда, не в `git-py`.
- 2026-08-30 17:10 UTC — branch + PLAN/state.md.
- 2026-08-30 17:12 UTC — реализация: убрал exists() check.
- 2026-08-30 17:15 UTC — 1 тест обновлён, 1291/1296 green, merge, push.

## Блокировки

(пусто)

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update12/state.md`. Статус «завершён». Следующая задача: scan на новые updateN+ в git-py.

## Side effects / lessons

- **Логика "if path exists: skip auto-append" — антипаттерн**. Стандартные Git GUI клиенты всегда трактуют путь как parent. Если пользователь хочет конкретную папку — он может указать путь заканчивающийся на имя репозитория.
- Sub-agent транспорт всё ещё сломан.
