# State: git-py update9 — show public key + Copy button in Settings

## Статус

Работаю над планом: docs/updates/update9/PLAN.md
Статус: завершён (merged в main)
Heartbeat: 2026-08-30 16:00 UTC
Текущая задача: — (план завершён)
Текущий stage: 1
Sub-agent dispatched: none (VM broken)
Токены использовано: ~18k

## Прогресс

- [x] 0. Создан branch `feature/update9` + `docs/updates/update9/{PLAN,state}.md`
- [x] 1. Stage A — `icons.py`: `_draw_copy` (commit 3eb7861)
- [x] 2. Stage B — `settings_dialog.py`: QPlainTextEdit + QToolButton + debounce (commit 3eb7861)
- [x] 3. Stage C — Тесты: 4 новых (commit 3eb7861)
- [x] 4. Stage D — STATUS-stage-1.md, merge feature/update9 → main, push

## Чекпоинт

Сделано: все стадии завершены, закоммичены, merged в main, pushed.

## Журнал

- 2026-08-30 15:40 UTC — пользователь: показывать текст + copy-кнопку.
- 2026-08-30 15:45 UTC — branch + PLAN/state.md.
- 2026-08-30 15:50 UTC — реализация иконки + UI + debounce.
- 2026-08-30 15:55 UTC — 4 новых теста зелёные, ruff clean, 575/575 tests_ui passed.
- 2026-08-30 16:00 UTC — STATUS, merge, push.

## Блокировки

(пусто)

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update9/state.md`. Статус «завершён». Следующая задача: scan на новые updateN+ в git-py.

## Side effects / lessons

- `QToolTip.showText` нужно патчить в тестах, иначе пытается позиционироваться на скрытом виджете.
- `qtbot.waitUntil` — правильный способ ждать debounced refresh в тестах.
- Sub-agent транспорт всё ещё сломан (см. update5/state.md).
