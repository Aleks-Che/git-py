# git-py

GitKraken-подобный десктопный git-клиент на Python: граф коммитов, ветки, stash, merge/rebase, undo/redo — на PySide6 + pygit2.

## Возможности

- **Интерактивный граф коммитов** — lane-раскладка DAG с цветными ветками (40-цветная детерминированная палитра), WIP-узел незакоммиченных изменений, stash-узлы, branch-чипы с hover-popup.
- **Рабочая директория** — stage/unstage файлов и отдельных строк diff'а, просмотр diff'ов, подсветка, игнорирование файлов.
- **Ветки** — создание, переименование, удаление, checkout (двойной клик), drag-and-drop merge/rebase как на графе, так и в левой панели, подменю «Merge X into...».
- **Merge / Rebase / Cherry-pick / Revert** — включая обнаружение конфликтов и визуальный диалог их разрешения (ours/theirs/base).
- **Undo/Redo** — все мутирующие операции идут через `CommandProcessor` (паттерн Command), панель истории действий.
- **Удалённые репозитории** — push/pull/fetch, управление remote'ами, клонирование с генерацией SSH-ключа, авто-fetch по таймеру.
- **Stash** — push/pop/apply/drop, частичный stash, stash на графе.
- **Прочее** — встроенный терминал, поиск по коммитам, вкладки репозиториев, тёмная/светлая тема, сохранение раскладки окна.

## Стек

- Python 3.10+
- [PySide6](https://doc.qt.io/qtforpython-6/) — UI
- [pygit2](https://www.pygit2.org/) (libgit2) — работа с Git; `git` CLI нужен только для rebase
- Архитектура: MVVM + Command pattern (подробности — `docs/ARCHITECTURE.md`)

## Установка и запуск

```bash
pip install -e ".[dev]"
python -m src.main
```

## Тесты и линт

```bash
python -m pytest            # 1000+ тестов; на headless/CI: QT_QPA_PLATFORM=offscreen
ruff check src/ tests/
```

## Структура проекта

```
src/
├── core/           # чистый Python + pygit2, без Qt: репозиторий, операции, граф, diff-парсер
├── viewmodels/     # состояние UI, сигналы Qt, команды (GitCommand) и CommandProcessor
├── ui/             # пассивные виджеты: main_window, панели, диалоги
└── utils/          # конфиг, тема, async-воркеры, аватары
tests/
├── core/           # unit-тесты core-слоя (временные репозитории через pygit2)
├── viewmodels/     # тесты ViewModel без GUI
└── ui/             # интеграционные тесты на pytest-qt
docs/               # архитектура, правила разработки, план, тест-план (на русском)
```

## Документация

- `docs/ARCHITECTURE.md` — слои, модули, паттерны
- `docs/DEVELOPMENT_RULES.md` — обязательные правила (core без Qt, команды через CommandProcessor, async для сети, доменные исключения)
- `docs/IMPLEMENTATION_PLAN.md` — роадмап по этапам и текущий прогресс
- `docs/FEATURES.md` — детали реализации графа, branch-чипов, drag-and-drop
- `REVIEW.md` — отчёт о глубоком ревью кодовой базы

## Статус

8 из 11 этапов завершено (см. `docs/IMPLEMENTATION_PLAN.md`). Сейчас в работе — этап 9 (настройки, темизация).
