# State: git-py update6 — auto-create ~/.ssh directory before ssh-keygen

## Статус

Работаю над планом: docs/updates/update6/PLAN.md
Статус: завершён (merged в main)
Heartbeat: 2026-08-30 14:45 UTC
Текущая задача: — (план завершён)
Текущий stage: 1
Sub-agent dispatched: none (VM sub-agent transport сломан — manual edit)
Токены использовано: ~12k (manual edit + 3 test iterations для отлова Qt crash)
Лимит токенов: ~1M default

## Прогресс

- [x] 0. Создан branch `feature/update6` + `docs/updates/update6/{PLAN,state}.md`
- [x] 1. Stage A — `SshKeyDialog._ensure_parent_dir(path)` + вызов в `_on_generate` (commit cb7b5a6)
- [x] 2. Stage B — Тесты: 3 новых (creates_missing_parent, falls_back_to_tempdir, aborts_when_no_dir) (commit cb7b5a6)
- [x] 3. Stage C — STATUS-stage-1.md, merge `feature/update6` → main, push

## Чекпоинт

Сделано: Stage A+B+C выполнены, коммит cb7b5a6 на feature/update6, merged в main, pushed.
Осталось: ждать новых задач.
Следующий шаг: ждать update7+ или переход к другим репо.

## Журнал

- 2026-08-30 14:15 UTC — пользователь сообщил об ошибке ssh-keygen на Windows.
- 2026-08-30 14:16 UTC — создан branch feature/update6 + PLAN/state.md.
- 2026-08-30 14:20 UTC — реализация _ensure_parent_dir + интеграция в _on_generate (commit cb7b5a6).
- 2026-08-30 14:25 UTC — первый тест (creates_missing_parent) прошёл.
- 2026-08-30 14:30 UTC — второй тест (falls_back_to_tempdir) завис → root cause: monkeypatch.setattr(Path, "mkdir", ...) вызывает Qt/ctypes segfault. Переписал на instance method patch.
- 2026-08-30 14:35 UTC — instance method patch прошёл, но test_3 (aborts_when_no) упал: monkeypatch на instance метод НЕ передаёт self, нужно убрать self из сигнатуры fake.
- 2026-08-30 14:40 UTC — все 3 update6 теста зелёные, full tests_ui 566/566 passed, ruff clean.
- 2026-08-30 14:45 UTC — STATUS-stage-1.md написан, merge feature/update6 → main, push.

## Блокировки

(пусто)

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update6/state.md`. Статус «завершён». Следующая задача: scan на новые updateN+ в git-py, или переход к herzog-zwei / sql-skill.

## Side effects / lessons learned (зафиксировано)

- **НЕ патчить `Path.mkdir` глобально** — Qt/ctypes использует Path внутри; segfault или deadlock.
- **`monkeypatch.setattr(instance, "method", func)` НЕ передаёт self** — функция принимает только аргументы метода.
- Sub-agent транспорт всё ещё сломан (см. update5/state.md). Все изменения сделаны manual edit.
