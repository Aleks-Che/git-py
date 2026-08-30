# State: git-py update8 — не разводить мусор в домашнем каталоге на .ssh file conflict

## Статус

Работаю над планом: docs/updates/update8/PLAN.md
Статус: в работе
Heartbeat: 2026-08-30 15:20 UTC
Текущая задача: Stage A — _handle_parent_is_file: использовать ~/.ssh-py вместо sibling
Текущий stage: 1
Sub-agent dispatched: none (VM sub-agent transport сломан)

## Прогресс

- [x] 0. Создан branch `feature/update8` + `docs/updates/update8/{PLAN,state}.md`
- [ ] 1. Stage A — `_handle_parent_is_file`: Yes → `~/.ssh-py/git-py-ed25519` (не sibling)
- [ ] 2. Stage B — Тесты: обновить `test_ssh_dialog_offers_alternative_path_on_file_conflict`, добавить 2 новых
- [ ] 3. Stage C — STATUS-stage-1.md, merge feature/update8 → main, push

## Чекпоинт

Сделано: branch + PLAN/state.
Осталось: реализация + тесты + merge.

## Журнал

- 2026-08-30 15:15 UTC — пользователь указал: "если каталога .ssh нет, то надо его создать, в домашнем каталоге не надо разводить мусор". Текущий Yes-sibling вариант в update7 сохраняет ключ прямо в home — это и есть мусор.

## Блокировки

Sub-agent транспорт сломан (см. update5/state.md). Manual edit.

## Cron-continuation hint

Для cron/новой сессии: прочитай `docs/updates/update8/state.md`. Если статус «в работе» — продолжай.
