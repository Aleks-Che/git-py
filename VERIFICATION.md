# Отчёт о проверке реализации плана update1 — раунд 2 (R3/R4)

> **Раунд 3 (2026-07-21): блокеры устранены.** Внесённые исправления:
> 1. **§3 (краш 0xC0000409):** `QCoreApplication([])` → `QApplication([])` в `tests/core/test_r4_cleanup.py` и 29 файлах tests/ui + tests/viewmodels (с подчисткой импортов). Механизм краша устранён в корне: ни один тест больше не создаёт QCoreApplication.
> 2. **§4 (CRLF):** фикстура `tmp_git_repo` в `tests/conftest.py` выставляет `core.autocrlf=false` (герметичность к глобальному git-конфигу разработчика); 12 записей файлов в `tests/core/test_operations.py` переведены с `write_text` (переводит `\n`→`\r\n` на Windows) на `write_bytes`; `test_development_rules_documents_exemptions` читает doc с `encoding="utf-8"` (падал с `UnicodeDecodeError` на cp1251).
> 3. **§5 (шрифт):** `tests/conftest.py` выставляет `QT_QPA_FONTDIR=<SystemRoot>\Fonts` до создания QApplication — все 4 font-fragile теста зелёные; в `test_right_click_on_branch_chip_routes_to_branch_menu` добавлена страховка `monkeypatch.setattr(QMenu, "exec", ...)` — регрессия hit-test'а теперь даёт FAIL вместо вечного зависания.
>
> **Итог раунда 3: `1140 passed, 0 failed` (exit 0), `ruff check` чисто, приложение запускается.**
> Не закрыто: **H19** (полнодеревный diff на клик) — pygit2 1.19 не экспонирует pathspec для diff API; быстрого фикса нет, варианты — `git diff -- <path>` через CLI или ожидание поддержки в pygit2. Lazy full-document (R3.2) частично снимает остроту.

---

**Дата:** 2026-07-21
**Объект:** коммиты `9f46eb7`..`9bf93f7` (фикс R1.1 + R3.1–R3.4 + R4 + release v0.10.0) поверх предыдущей проверки (`VERIFICATION.md` раунд 1)
**Метод:** прогон тестов, живые воспроизводящие скрипты, замеры производительности, бисекция крашей через git worktree на `4dddc15`.

---

## 1. Сводка

План `docs/updates/update1/PLAN.md` реализован **полностью**: R1, R2 (раунд 1), R3.1–R3.4, R4 (этот раунд). Приложение запускается, `ruff check` чисто.

Но **тестовый набор на HEAD не может завершиться**: два независимых блокера (один — жёсткий краш процесса, один — детерминированный фейл на Windows) плюс кластер environment-зависимых тестов. Ниже — что проверено и работает, затем блокеры.

| Проверка | Результат |
|---|---|
| Приложение стартует (`MainWindow`) | ✅ |
| `ruff check src/ tests/` | ✅ чисто |
| `pytest` (полный набор) | ❌ **не завершается** — краш/фейлы, см. §3–5 |

---

## 2. Что реализовано и проверено живыми прогонами

### R1.1 (доработка) — merge X→Y, вариант (a): mid-state устранён
`src/core/operations.py:493-655`, регрессионные тесты `tests/core/test_r1_1_merge_mid_state.py` (569 строк).
Прогон моего скрипта из раунда 1: после merge source→main при HEAD=feature — HEAD на main, **status чист**, worktree соответствует merge-дереву (`s.txt` на диске), feature не двинут, первый родитель — старый tip main. Undo откатывает корректно. Дефект mid-state из раунда 1 (фантомный `INDEX_DELETED`, потерянные файлы) устранён.

### R3.1 — граф O(n²)→O(n) и лимит истории
- Замер на синтетическом репо: **5000 коммитов — `build_graph` 0.02 с** (было 0.80 с при O(n²)-поисках). Требование «< 1 с» выполнено с 40× запасом.
- Индикатор усечения истории («showing N of M») в шапке графа (`graph_panel.py`, `_truncation_label`); лимит вынесен в конфиг `graph_history_limit` (default 500), поиск ходит по полной истории (`SEARCH_HISTORY_MAX_COUNT=100_000`).

### R3.2 — разгрузка UI-потока
- Приоритет веток считается в VM-кэше (`GraphViewModel.branch_priority_for`), виджет больше не ходит в pygit2 из paint (P4 — основной пункт выполнен; `_is_branch_reachable_from_head` помечен DEPRECATED).
- Full-document diff (2³¹−1 строк контекста) — ленивый, по `request_full_document()` (`commit_panel_viewmodel.py:638-665`).
- Async-диспетчеризация refresh после мутаций, generation-токены (по коду `main_viewmodel.py`).

### R3.3 — Windows-специфика
`subprocess` с `encoding="utf-8"` (`operations.py:794, 1734`); тесты `test_r3_3_terminal_encoding.py`, `test_r3_3_subprocess_encoding.py`, `test_r3_3_pull_no_upstream.py` (pull без upstream → понятная ошибка).

### R3.4 — диалог конфликтов и рендер-полировка
`tests/ui/test_r3_4.py` (347 строк): бинарные конфликты, рёбра у кромок viewport, connector-хит-тест, expanded-состояние LeftPanel, HTML-экранирование.

### R4 — чистка
- Все 20 debug-`print` в `main_viewmodel.py` переведены на `debug_print` (`utils/debug_mode.py`).
- `GraphWidget` помечен DEPRECATED (docstring), аватар консолидирован в `utils/avatar.py::make_avatar_pixmap`.
- `tests/core/test_r4_cleanup.py` — docstring/мёртвый-код контракты.

### Устаревшие тесты из раунда 1 — обновлены
Хардкод-пути `/root/projects/...` убраны (`test_main_viewmodel_r2_3.py`, `test_qt_lifecycle_r2_6.py` — теперь от корня репо); контрактные тесты merge/remote переписаны под новую семантику процессора (+240 строк); popup-тест переписан на `findChildren` — ровно по рекомендациям раунда 1.

---

## 3. Блокер 1 (CRITICAL): полный прогон падает с 0xC0000409 — `QCoreApplication` отравляет сессию

**Симптом:** `python -m pytest` на HEAD не завершается: обрыв вывода на ~18% (после tests/core) без summary, exit code `-1073740791` (0xC0000409). В `tests/viewmodels` — access violation 0xC0000005 в `test_main_viewmodel_clipboard.py` (`QApplication.clipboard().setText`, `main_viewmodel.py:1060`).

**Механизм (подтверждён прогоном):** `tests/core/test_r4_cleanup.py:16` (`_qapp()`: `QCoreApplication.instance() or QCoreApplication([])`) создаёт **QCoreApplication** в tests/core. Сессионный фиксчур `qapp` из pytest-qt (`plugin.py:73` — `QApplication.instance() or QApplication(...)`) потом **переиспользует** его для tests/ui. Первое создание `QWidget` под QCoreApplication без QApplication → фатальный 0xC0000409 (воспроизведено минимальным скриптом). Для clipboard — `QApplication.clipboard()` без QGuiApplication → access violation.

**Почему раньше работало:** в полном наборе tests/ui шёл перед tests/viewmodels и первым создавал настоящий `QApplication` — все `_ensure_app()` ниже no-op'ились. Новый `test_r4_cleanup.py` внедрил QCoreApplication в tests/core, которые идут **раньше** tests/ui. Тот же дефект латентно существует в ~10 файлах tests/viewmodels (их `_ensure_app()` — QCoreApplication): `pytest tests/viewmodels/` падает и на `4dddc15` (проверено worktree) — просто в полном наборе это маскировалось.

**На CI это убьёт прогон** (порядок core→ui детерминирован).

**Фикс (по приоритету):**
1. Во всех `_ensure_app()`/`_qapp()` создавать `QApplication`, а не `QCoreApplication` (QApplication наследует QCoreApplication — VM-тестам хватит): `tests/core/test_r4_cleanup.py:16` и ~10 файлов в tests/viewmodels. Регрессионный страховочный тест: прогон `pytest tests/core tests/ui` в CI.
2. Стратегически — единый session-фиксчур в `tests/conftest.py` (`@pytest.fixture(scope="session", autouse=True)` создающий QApplication до всего), а `_ensure_app()` удалить.

---

## 4. Блокер 2 (deterministic FAIL): CRLF в `test_r1_1_merge_mid_state`

`tests/core/test_r1_1_merge_mid_state.py::test_merge_into_other_branch_does_not_move_head_or_worktree`:
```
assert _worktree_files(repo) == _tree_files(repo, merge_commit.tree)
# workdir: hello.txt b'hello, world\r\n'  vs  tree: b'hello, world\n'
```
На этой машине `git config --global core.autocrlf = true` → checkout (который вариант (a) делает на target) пишет в worktree CRLF, а blob'ы содержат LF → побайтовое сравнение падает. **Это дефект теста, не реализации** — поведение merge проверено вручную и корректно (§2). На Linux CI без autocrlf тест пройдёт, на Windows-разработчиках с `autocrlf=true` — всегда красный.

**Фикс:** в фикстуре теста выставлять `repo.config["core.autocrlf"] = False` (как это делают другие core-тесты, см. conftest) или нормализовать EOL при сравнении.

---

## 5. Кластер environment-зависимых тестов (pre-existing, НЕ регрессия R3/R4)

В venv стоит PySide6 6.11.1 **без bundled fonts** (`PySide6/lib/fonts` отсутствует — Qt ≥6.7 шрифты не поставляет) → offscreen-ран получает широкий fallback-шрифт «Sans Serif 9» (`'old_main'` = 96 px против лимита 84 px в chip-лейауте). Отсюда, при прогоне tests/ui в изоляции:

- `test_widget_renders_local_branch_label`, `test_widget_remote_branch_strips_origin_prefix` — текст чипа элидируется (`'old_m…'`), тесты ждут точную строку;
- `test_left_click_on_branch_chip_does_not_select_commit`, `test_double_click_on_branch_chip_emits_checkout` — rect чипа шире колонки → `_branch_chip_at` возвращает None → клик проваливается в commit-путь;
- `test_right_click_on_branch_chip_routes_to_branch_menu` — **зависает навсегда**: тот же fallthrough ведёт в настоящий `QMenu.exec()` (`graph_panel.py:800`) вместо замоканного.

Проверено worktree на `4dddc15`: воспроизводится идентично — **не регрессия**. Но: (a) это делает tests/ui непроходимым в данном окружении; (b) зависание — дефект дизайна теста (модальный exec достижим при любом сбое hit-test'а).

**Фикс (комплексно):** поставить шрифт в тестовое окружение (`QT_QPA_FONTDIR`/`QFontDatabase.addApplicationFont` в conftest, или dejavu в CI) — тогда весь кластер зазеленеет; плюс в `test_right_click_...` замокать `QMenu.exec` на уровне класса, чтобы сбой hit-test'а давал FAIL, а не вечный hang; плюс ослабить пиксельные assert'ы до относительных.

---

## 6. Частично реализовано

**H19 (полнодеревные diff'ы на клик):** lazy full-document сделан (§2), но основной пункт — нет: `build_diff_text` по-прежнему строит `repo.diff("HEAD", ...)` по всему дереву без `pathspec` и фильтрует один файл (`commit_panel_viewmodel.py:702-713`, плюс второй полный cached-diff в `_without_staged_diff_lines:742`). Коммит R3.2 заявляет «pathspec diff», в коде pathspec для diff не используется. На репо с тысячами грязных файлов клик по файлу по-прежнему читает весь diff.

---

## 7. Итог и рекомендации

**Реализация плана — да, выполнена** (R1–R4 целиком), ключевые фиксы и perf-цели подтверждены прогонами (merge mid-state ✔, граф 0.02 с ✔, лимит истории ✔, чистка ✔). Но «зелёный набор» не достигнут:

1. **Сначала** — фикс §3 (QCoreApplication → QApplication в `_ensure_app`/`_qapp`): одна строка на файл, снимает краш и разблокирует весь прогон, включая CI.
2. **Затем** — фикс §4 (`core.autocrlf=false` в фикстуре): ещё одна строка.
3. После этого прогнать полный набор и добить оставшееся: шрифт в тестовом окружении (§5), pathspec-diff (§6).
4. Отдельно отметить в `docs/IMPLEMENTATION_PLAN.md`: из REVIEW.md открытыми остаются только H19 (частично) и environment-замечания по тестам.
