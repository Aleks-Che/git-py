# State: git-py update7 — лучше обрабатывать конфликт "файл с именем .ssh"

## Статус

Работаю над планом: docs/updates/update7/PLAN.md
Статус: в работе
Heartbeat: 2026-08-30 14:50 UTC
Текущая задача: Stage A — _ensure_parent_dir: детект file-vs-dir конфликта + Yes/No диалог
Текущий stage: 1
Sub-agent dispatched: none (VM sub-agent transport сломан)
Токены использовано: ~1k

## Прогресс

- [x] 0. Создан branch `feature/update7` + `docs/updates/update7/{PLAN,state}.md`
- [ ] 1. Stage A — `SshKeyDialog._ensure_parent_dir`: детект file conflict + Yes/No диалог с альтернативным путём
- [ ] 2. Stage B — Тесты: `test_ssh_dialog_detects_file_with_ssh_name`, `test_ssh_dialog_offers_alternative_path_on_file_conflict`, `test_ssh_dialog_fallback_message_mentions_pub_file`
- [ ] 3. Stage C — STATUS-stage-1.md, merge feature/update7 → main, push

## Чекпоинт

Сделано: branch + PLAN/state.
Осталось: реализация + тесты + merge.

## Журнал

- 2026-08-30 14:45 UTC — пользователь сообщил: на Windows `.ssh` существует как файл (не папка), WinError 183 от mkdir, fallback в Temp работает но UX непонятный.

## Блокировки

Sub-agent транспорт сломан (см. update5/state.md). Manual edit.

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update7/state.md`. Если статус «в работе» — продолжай.
