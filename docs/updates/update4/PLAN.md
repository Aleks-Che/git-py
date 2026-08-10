# PLAN update4 — документационный долг и env-зависимый тест после update1

Дата: 2026-08-10. Источник: cron-верификация update1–update3 (см. «Cron check» в docs/updates/update1/VERIFICATION.md, update2/STATUS.md, update3/STATUS.md). Реализация update1–update3 подтверждена кодом и тестами; остались мелкие хвосты.

## Задачи

### 1. Синхронизировать docs/IMPLEMENTATION_PLAN.md
- Строка ~73 помечает «Этап 10 — завершено (R4, 2026-07-21)», а итоговый чеклист (строка ~142) — «Этап 10 — не начато». Закрыть чеклист Этапа 10.
- Обновить раздел «Текущий статус» (сейчас устарел: Этап 9, дата 2026-07-09).
- Acceptance: чеклист и раздел статуса согласованы; `grep -n "Этап 10" docs/IMPLEMENTATION_PLAN.md` не даёт противоречий.

### 2. Самодостаточный тест test_build_graph_pipe_color_zero_does_not_fall_back_to_oid_color
- Файл: tests/core/test_graph_v2.py. Тест требует ветку `visual-feat` в локальном клоне git-py — на свежем клоне падает (pre-existing/environmental, задокументировано в update1/STATUS-r3.1.md).
- Решение: `pytest.skip` при отсутствии ветки либо построение синтетического репозитория в фикстуре (`tempfile` + `pygit2.init_repository`, как принято в tests/core/).
- Acceptance: полный сьют `QT_QPA_PLATFORM=offscreen python -m pytest -q` зелёный на чистом клоне (0 failed).

### 3. Документировать этап R3.2 (update1)
- Реализация R3.2 в коде есть (pathspec-diff, async-воркеры, throttle ApplicationActive, branch-priority в VM), но STATUS-R3.2.md отсутствует.
- Создать docs/updates/update1/STATUS-R3.2.md (или сводный финальный STATUS update1) с перечнем изменений R3.2 и результатами гейтов.
- Acceptance: файл создан, перечислены изменения и прогнанные тесты.

## Тесты
- Обновить/добавить: tests/core/test_graph_v2.py (задача 2).
- Гейты: полный pytest-сьют зелёный на чистом окружении; `ruff check src/ tests/` чистый.
