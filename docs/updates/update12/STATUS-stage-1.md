# STATUS update12 stage-1 — всегда дополнять имя репозитория к пути

## Что сделано

Пользовательский фидбек (Александр Чесноков) после update11:

> Репозиторий `git@github.com:Aleks-Che/git-py.git` склонирован. но при указании каталога
> `C:\work\git\my-repos` он склонировался ровно туда, а надо было клонировать в каталог `git-py`

Причина: в update11 `_resolve_clone_target` имел правило "если путь
**существует** — оставляем как есть". У пользователя `C:/work/git/my-repos`
уже существовала (от других проектов или заранее созданная), и мой код
решил "раз папка существует — пользователь хочет клонировать именно в неё".

Это была **неправильная интерпретация**. Стандартные Git GUI клиенты
(GitHub Desktop, Sourcetree, GitKraken) трактуют путь как **родительскую
директорию** для нового клона и всегда добавляют имя репозитория.

## Изменения

### `src/ui/dialogs/clone_dialog.py` — `_resolve_clone_target`

- Убрана проверка `if target.exists(): return path`.
- Теперь: если URL парсится и путь не заканчивается на имя репозитория —
  **всегда** дополняем имя. Существование/несуществование пути больше
  не имеет значения.
- Оставлены две проверки:
  1. URL не парсится → оставляем путь как есть.
  2. Последний сегмент пути уже совпадает с именем репозитория →
     не дублируем.

## Тесты

### Изменён

- `tests/ui/test_clone_dialog.py::test_clone_dialog_does_not_touch_existing_path`
  переименован в `test_clone_dialog_appends_repo_name_to_existing_path`
  и обновлён под новое поведение:
  `tmp_path/my-repos` (существующий) + URL → `tmp_path/my-repos/git-py`.

### Без изменений

- `test_clone_dialog_appends_repo_name_when_path_does_not_exist` —
  продолжает работать (поведение то же самое).
- `test_clone_dialog_does_not_duplicate_when_path_already_has_name` —
  продолжает работать (последний сегмент совпадает → не дублируем).

## Гейты

```
$ ruff check src/ tests/
All checks passed!

$ QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
1291 passed, 5 failed in 93s
```

5 фейлов — pre-existing `test_remove_remote_*` (задокументированы
в `update4/STATUS-stage-1.md`), НЕ связаны с update12.

## Commits

```
efc3252 stage-1(update12): always append repo name to clone target (drop exists() check)
```

## Что НЕ сделано (out of scope для update12)

- Не делаю preference dialog "клонировать в эту папку или создать
  подпапку" — overkill для одного параметра.
- Не показываю preview финального пути в UI перед clone — можно
  добавить позже, если пользователь попросит.

## Регрессии

Прогон `tests/` полный: 1291/1296 (5 pre-existing fails unrelated).
