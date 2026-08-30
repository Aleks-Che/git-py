# State: git-py update10 — clone SSH URL через system git CLI

## Статус

Работаю над планом: docs/updates/update10/PLAN.md
Статус: завершён (merged в main)
Heartbeat: 2026-08-30 16:30 UTC
Текущая задача: — (план завершён)
Текущий stage: 1
Sub-agent dispatched: none (VM broken)
Токены использовано: ~20k

## Прогресс

- [x] 0. Создан branch `feature/update10` + `docs/updates/update10/{PLAN,state}.md`
- [x] 1. Stage A — `src/core/operations.py`: `_clone_via_cli` (commit 0d9b266)
- [x] 2. Stage B — `src/core/repository.py`: `RepositoryManager.clone()` использует CLI fallback (commit 0d9b266)
- [x] 3. Stage C — Тесты: 4 новых (commit 0d9b266)
- [x] 4. Stage D — STATUS-stage-1.md, merge feature/update10 → main, push

## Чекпоинт

Сделано: все стадии завершены, merged в main, pushed.

## Журнал

- 2026-08-30 16:10 UTC — пользователь: clone `git@github.com:...` → "unsupported URL protocol".
- 2026-08-30 16:15 UTC — branch + PLAN/state.md.
- 2026-08-30 16:20 UTC — реализация `_clone_via_cli` + интеграция в `RepositoryManager.clone()`.
- 2026-08-30 16:25 UTC — 4 новых теста зелёные, ruff clean.
- 2026-08-30 16:30 UTC — STATUS, merge, push.

## Блокировки

(пусто)

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update10/state.md`. Статус «завершён». Следующая задача: scan на новые updateN+ в git-py.

## Side effects / lessons

- **`monkeypatch.setattr("module.attr.subattr", ...)` НЕ работает** если `subattr` не импортирован в `module` через `import attr`. Решение: патчить модуль где `subattr` реально живёт (например, `src.core.operations.subprocess.run`).
- Circular import fix: `RepositoryManager.clone` делает **локальный import** `_clone_via_cli` (внутри метода), потому что `src.core.operations` импортирует `RepositoryManager` для `unwrap`.
- Sub-agent транспорт всё ещё сломан.
