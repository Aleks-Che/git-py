# Зависания при работе с несколькими worktree — 2026-09-19

Причина подтверждена на `C:/work/git/my-repos/math-portal` и worktree
`C:/work/git/my-repos/math-portal.agents-ide-worktrees/6ae6ba5766b74179b9be392bbd45b408`.
Проверены основная рабочая копия и пять зарегистрированных linked worktree.
Работа велась с Python 3.12, pygit2 1.19.3, Windows.

## Что блокировало интерфейс

Таймер каждую секунду запускал проверку текущей папки и всех соседних worktree.
В исходном замере `pygit2.status()` активного linked worktree занимал 7–14 мс,
но проверка основной папки и одного из соседей — примерно по секунде.
Во время этих нативных вызовов блокировался контрольный Python-поток:
QRunnable не устранял удержание GIL, поэтому задерживались и обработчики Qt.

В соседнем worktree `ce1f62b245194708a86ae757a207cdf7` обнаружены junction-каталоги
`.tools`, `backend/.venv`, `frontend/node_modules`. `pygit2` показывал их как
WT_NEW, хотя `git check-ignore -v` подтверждал правила `.tools/`, `.venv/`,
`node_modules/` из `.gitignore`. Git CLI их корректно исключает.
Это объясняет различие счётчиков dirty worktree, но само по себе не доказывает,
что вся длительность libgit2-сканирования приходится именно на junction.

## Изменения

- `core/status.py` выполняет Git status отдельным процессом с таймаутом 30 с.
  Ожидание процесса освобождает GIL. Формат porcelain v1 с NUL-разделителями
  переводится в прежние битовые флаги pygit2, включая обе стороны partial staging
  и все конфликтные комбинации. `--no-renames` сохраняет прежнюю семантику
  добавленного и удалённого пути без вычисления сходства файлов.
- `--no-optional-locks` запрещает необязательную запись индекса; stdin/команды
  пользователя не используются, на Windows дочерний процесс не открывает консоль.
  Это рекомендованный Git режим фонового опроса:
  [git-status, Background refresh](https://git-scm.com/docs/git-status#_background_refresh).
- Git-dir и work-tree передаются явно; переменные окружения, способные выбрать
  чужой репозиторий или индекс, не наследуются дочерним процессом.
- Панель изменений, мониторинг, граф, проверка перед auto-fetch и диагностический
  статус checkout используют новый метод `RepositoryManager.get_raw_status()`.
  Ошибки Git/таймаут не подменяются пустым статусом или медленным fallback.
- Фоновая загрузка и обновление WIP передают графу уже прочитанный статус.
  После завершения мониторинга таймер начинает новый интервал: долгий опрос
  не превращается в непрерывное сканирование без пауз.
- Оценка размера merge считает `diff.deltas`, не создавая патчи всех файлов.

## Итоговые замеры

Три повтора каждого варианта, без параллельного запуска pytest. Время полного
опроса — медиана; пауза Python — максимум интервала контрольного потока,
просыпающегося каждые 10 мс. Каждый опрос заново открывает менеджеры,
читает текущий статус и статусы соседей и закрывает подключения.

| Открытая папка | Полный опрос pygit2 | Полный опрос CLI | Макс. пауза Python до → после |
| --- | ---: | ---: | ---: |
| math-portal | 1394 мс | 619 мс | 1784 → 18 мс |
| …/6ae6ba5766b74179b9be392bbd45b408 | 1526 мс | 621 мс | 1704 → 23 мс |

Граф по уже полученному снимку: 71 и 50 мс соответственно, 76 строк.
Ключевой эффект — исчезновение секундных пауз GUI; суммарное время проверки
диска уменьшилось примерно в 2,3–2,5 раза в этом прогоне.

Дополнительно запущены MainViewModel с async и настоящий GraphTableWidget
в Qt offscreen, размер 1200×800, на проблемном linked worktree. За 6,5 секунды
мониторинга, движения мыши, кликов и перерисовок: 402 события контрольного таймера,
404 отрисовки, максимальный интервал событий 44,1 мс, самая долгая отрисовка
32,5 мс, ошибок ViewModel нет. Это локальный smoke-замер, не гарантия FPS.

Рабочие папки параллельно изменялись внешними процессами: число файлов основной
папки менялось между запусками. `same_status=false` в профиле ожидаем в присутствии
описанных игнорируемых junction; в отдельном сравнении остальные пять папок
дали одинаковые словари статусов. Содержимое, индекс и refs пользовательских
репозиториев диагностикой не изменялись. Merge/Undo проверялись на временных репо.

Повторить из корня git-py:

```powershell
.venv/Scripts/python.exe -m tools.profile_worktree_status `
  C:/work/git/my-repos/math-portal `
  C:/work/git/my-repos/math-portal.agents-ide-worktrees/6ae6ba5766b74179b9be392bbd45b408 `
  --repeat 3
```

## Проверки и границы

Целевой прогон **492 passed, 88,12 с**, `QT_QPA_PLATFORM=offscreen`:

```text
python -m pytest
  tests/core/test_repository.py tests/core/test_operations.py tests/core/test_status.py
  tests/core/test_worktree_status.py tests/core/test_other_worktrees.py
  tests/core/test_repository_handles.py tests/viewmodels/test_status_performance.py
  tests/viewmodels/test_worktree_refresh.py tests/viewmodels/test_other_worktrees.py
  tests/viewmodels/test_graph_viewmodel.py tests/viewmodels/test_commit_panel_viewmodel.py
  tests/viewmodels/test_main_viewmodel_merge.py tests/viewmodels/test_review_followup.py
  tests/ui/test_worktree_refresh.py tests/ui/test_other_worktrees.py
  tests/ui/test_graph_widget.py tests/ui/test_graph_panel.py tests/ui/test_conflict_workflow.py
  -q --tb=short -p no:cacheprovider
```

После добавления двух регрессий junction/merge повторно запущены целиком
`tests/core/test_status.py` и `tests/viewmodels/test_status_performance.py`:
**35 passed, 2,55 с**. Эти 35 тестов частично входят в предыдущий прогон.
Полный pytest не запускался. Для временных Git-репозиториев тесты выполнялись
вне песочницы; конфигурация пользователя не менялась.

Остаются синхронные Git-операции и `_refresh_all_views()` после части действий;
они теперь получают более быстрый статус, но не становятся асинхронными.
Для следующей оптимизации при необходимости стоит измерять именно их:
перенос полного refresh требует сохранения цепочки remote checkout → merge
и busy/Undo-контрактов. Кэширование истории и более редкий опрос соседей
не понадобились для устранения найденных постоянных зависаний и не добавлены.
