# State: git-py update7 — handle parent-is-a-file conflict + better fallback UX

## Статус

Работаю над планом: docs/updates/update7/PLAN.md
Статус: завершён (merged в main)
Heartbeat: 2026-08-30 15:10 UTC
Текущая задача: — (план завершён)
Текущий stage: 1
Sub-agent dispatched: none (VM sub-agent transport сломан — manual edit)
Токены использовано: ~14k

## Прогресс

- [x] 0. Создан branch `feature/update7` + `docs/updates/update7/{PLAN,state}.md`
- [x] 1. Stage A — `_ensure_parent_dir` refactor: 3 случая, Yes/No/Cancel dialog (commit 801a699)
- [x] 2. Stage B — Тесты: detects_file_with_ssh_name, offers_alternative_path, fallback_message_mentions_pub_file (commit 801a699)
- [x] 3. Stage C — STATUS-stage-1.md, merge feature/update7 → main, push

## Чекпоинт

Сделано: Stage A+B+C выполнены, коммит 801a699 на feature/update7, merged в main, pushed.
Осталось: ждать новых задач.

## Журнал

- 2026-08-30 14:50 UTC — пользователь сообщил: .ssh существует как файл, WinError 183.
- 2026-08-30 14:55 UTC — создан branch feature/update7 + PLAN/state.md.
- 2026-08-30 15:00 UTC — реализация _handle_parent_is_file + улучшенный fallback message (commit 801a699).
- 2026-08-30 15:05 UTC — 3 новых теста зелёные, 569/569 tests_ui passed.
- 2026-08-30 15:10 UTC — STATUS-stage-1.md, merge, push.

## Блокировки

(пусто)

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update7/state.md`. Статус «завершён». Следующая задача: scan на новые updateN+ в git-py, или переход к herzog-zwei / sql-skill.

## Side effects / lessons

- **Не патчить `Path.mkdir` глобально** (из update6 — Qt/ctypes segfault).
- **`monkeypatch.setattr(instance, "method", func)` НЕ передаёт self** (из update6).
- Для тестирования **Qt question dialog с 3 кнопками** (Yes/No/Cancel) — патчить через `QMessageBox.question` staticmethod, не через `QMessageBox.Yes` (это просто enum).
- Sub-agent транспорт всё ещё сломан (см. update5/state.md).
