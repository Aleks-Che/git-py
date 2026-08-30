# State: git-py update8 — use ~/.ssh-py/ instead of home sibling

## Статус

Работаю над планом: docs/updates/update8/PLAN.md
Статус: завершён (merged в main)
Heartbeat: 2026-08-30 15:35 UTC
Текущая задача: — (план завершён)
Текущий stage: 1
Sub-agent dispatched: none (VM sub-agent transport сломан — manual edit)
Токены использовано: ~16k

## Прогресс

- [x] 0. Создан branch `feature/update8` + `docs/updates/update8/{PLAN,state}.md`
- [x] 1. Stage A — `_handle_parent_is_file`: Yes → `~/.ssh-py/`, без рекурсии (commit a5fa5c1)
- [x] 2. Stage B — 1 старый тест обновлён + 2 новых (commit a5fa5c1)
- [x] 3. Stage C — STATUS-stage-1.md, merge feature/update8 → main, push

## Чекпоинт

Сделано: Stage A+B+C выполнены, коммит a5fa5c1 на feature/update8, merged в main, pushed.
Осталось: ждать новых задач.

## Журнал

- 2026-08-30 15:20 UTC — пользователь указал: "в домашнем каталоге не надо разводить мусор". Создал branch update8.
- 2026-08-30 15:25 UTC — реализация: `~/.ssh-py/` вместо sibling.
- 2026-08-30 15:28 UTC — поймал RecursionError в тесте when ~/.ssh-py also is a file. Переписал без рекурсии.
- 2026-08-30 15:32 UTC — 3 теста зелёные (1 обновлён + 2 новых).
- 2026-08-30 15:35 UTC — STATUS, merge, push.

## Блокировки

(пусто)

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update8/state.md`. Статус «завершён». Следующая задача: scan на новые updateN+ в git-py, или переход к herzog-zwei / sql-skill.

## Side effects / lessons

- **Recursion в `_ensure_parent_dir` опасна**: если новый путь тоже конфликтный — бесконечный цикл.
- Решение: в `_handle_parent_is_file` создавать новую директорию напрямую через mkdir,
  без рекурсивного вызова `_ensure_parent_dir`.
- Sub-agent транспорт всё ещё сломан (см. update5/state.md).
