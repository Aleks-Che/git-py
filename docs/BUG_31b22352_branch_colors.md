<!-- Файл: BUG_31b22352_branch_colors.md -->
# Bug: цвет ветки `3mk4yl/fix-dict-unhashable-bug` не соответствует её имени

## Резюме

Ветка `3mk4yl/fix-dict-unhashable-bug` в репозитории
`C:/work/git/other-repos/llm/gpt-researcher` (коммит `31b22352`) рисуется
**не тем цветом, который положен ей по имени**, а цветом, унаследованным от
merge-коммита, через которого она попадает в `history`. Одновременно с этим
**mainline (lane 0) в окрестности merge окрашивается в произвольный fallback-цвет**
(`#7D2559` PINK в данном репозитории), потому что merge-коммит не имеет
`primary_branch` и его `parent[1]` получает первый свободный цвет из
`_pick_fallback`.

Сценарий не уникален для этой ветки — это **системная проблема** алгоритма
`build_graph` в `src/core/graph_v2.py:561-573`: для коммита, который уже
отслеживается на lane (был предварительно зарегистрирован при обработке
его child-merge), `primary_branch` (имя ветки) **никогда не учитывается** —
цвет всегда берётся из `lane_colors[lane]`, заполненного merge-коммитом.

---

## Топология ветки

В `gpt-researcher` ветка имеет ровно **один собственный коммит** перед
merge обратно в main. Полная цепочка:

```
7c321744  «Modify contributor image link in README»
└──  точка ответвления (общая с main)
    ├── ...коммиты main (34723c1, ec126e0, 76912d1, 86e27b3...)...
    └── 987c9e80  «Fix: resolve unhashable dict error…» (Em Kay)
        └── 31b22352  «Merge branch 'main' into fix-dict-unhashable-bug» (3mk4yl)
            └── 92bfc038  «Refine GPT Researcher description in README» (main)
```

`987c9e80` — единственный коммит ветки. `31b22352` — merge коммит, объединяющий
ветку обратно в main. `92bfc038` — следующий коммит main после merge.

Ветка `3mk4yl/fix-dict-unhashable-bug` уже удалена из remote refs, но
`987c9e8` и `31b22352` присутствуют в `history` через:

```
refs/remotes/origin/HEAD
refs/remotes/origin/feature/core-research-improvements
refs/remotes/origin/feature/deepagents-example
refs/remotes/origin/main
refs/remotes/origin/security/content-hardening
refs/tags/v3.5.0
refs/tags/v3.5.1
```

Никакая ветка не указывает **напрямую** на `987c9e8` или `31b22352` —
они являются предками других tip-ов.

---

## Цвет ветки по её имени

Из `src/core/graph_v2.py:81-94`:

```python
def _pick_branch_color(name: str) -> int:
    lower = name.lower()
    override = _BRANCH_COLOR_OVERRIDES.get(lower)
    if override is not None:
        return override
    return crc32(lower.encode("utf-8")) % len(BRANCH_PALETTE)
```

Для `3mk4yl/fix-dict-unhashable-bug`:

```python
>>> from zlib import crc32
>>> crc32(b"3mk4yl/fix-dict-unhashable-bug") % 24
15
>>> BRANCH_PALETTE[15]
'#C4912E'  # GOLD
```

То есть **по имени ветки положен цвет idx=15 (`#C4912E` GOLD)**.

В `_BRANCH_COLOR_OVERRIDES`:
```python
{"main": 1, "master": 1, "develop": 0, "dev": 0}
```

`3mk4yl/fix-dict-unhashable-bug` не в override-таблице, поэтому берётся
через `crc32`. Для пользователя золотисто-коричневый `#C4912E` визуально
может восприниматься как «тёмнокрасный» (похож на `#782B24` RED idx=2
или `#B5453C` RUST idx=14).

---

## Что реально происходит в графе

### Состояние `lanes` и `lane_colors` в ключевых точках

Ниже — трассировка состояния при обработке merge `31b22352` (row 82)
и его child `987c9e8` (row 103). В реальном запуске `used_colors` уже
содержит цвета 0–22 (master tip + remote branches), поэтому fallback
отдаёт первый свободный idx.

#### До обработки `31b22352`

```
lanes       = [None, ..., None]   # lane 0 свободен
lane_colors = {0: 0 (GREEN, от master tip), 1: 1, 2: 2, ...}  # many slots filled
```

#### Обработка `31b22352` (merge коммит)

```python
# graph_v2.py:561-573
commit_branch_names = oid_to_branches.get("31b22352", [])   # []  ← пусто!
primary_branch = commit_branch_names[0] if commit_branch_names else None  # None

# commit_lane_opt is None (это первый раз, когда видим этот SHA):
commit_color_index = color_assigner.assign_color(lane, None)
# lane=23 (новый пустой), primary=None → _pick_fallback(23)
# к этому моменту used_colors = {0..22}, fallback начинает с 23 → 23 занят
# → 0 → занят → 1 → занят → ... → 6 → свободен → return 6
# commit_color_index = 6 (PINK)  ← но потом в коде мы видим 0 (см. ниже)
```

В реальном запуске `build_graph` для этого репозитория
(`tools/debug_31b22352.py`) `31b22352` оказался на **lane=23 с color=0 GREEN**:

```
82 31b2235  lane=23  color=0  #1A5924  | col 0: CROSS  c=6  p=6
```

То есть в этом конкретном запуске к моменту row 82 в `used_colors`
был свободен idx=0 (или `_pick_fallback` нашёл его первым), и merge
получил color=0 GREEN. Точный idx зависит от того, какие remote refs
обрабатывались до row 82 и в каком порядке.

#### Установка parent lanes (graph_v2.py:622-643)

Для `parent[0] = 987c9e8` (first parent, fix-dict commit):

```python
elif parent_idx == 0:
    lanes[lane] = parent_sha                  # lanes[23] = "987c9e8"
    oid_color_index[parent_sha] = commit_color_index   # oid_color["987c9e8"] = 0
    parent_lane = lane
    parent_color = commit_color_index         # = 0 (GREEN)
```

Для `parent[1] = 92bfc03` (second parent, main commit):

```python
else:
    empty = _find_empty_lane(lanes)
    new_lane = empty if empty is not None else (lanes.append(None) or len(lanes) - 1)
    # В реальности: lane 0 был свободен → new_lane = 0
    lanes[new_lane] = parent_sha              # lanes[0] = "92bfc03"
    parent_branch = oid_to_branches.get("92bfc03", [None])[0]  # None (orphan)
    new_color = color_assigner.assign_fork_sibling_color(new_lane, None)
    # = _pick_fallback(0); к этому моменту 0 занят → 1 занят → ... → 6 свободен
    # new_color = 6 (PINK)
    lane_color_index[new_lane] = new_color    # lane_colors[0] = 6
```

#### Обработка `987c9e8` (row 103)

```python
# commit_lane_opt is not None!  lanes[23] = "987c9e8"
commit_color_index = color_assigner.continue_lane(23)
# = lane_colors[23] = 0 (GREEN, унаследованный от 31b22352)
# primary_branch = "3mk4yl/fix-dict-unhashable-bug"  → должно быть 15 (GOLD)
# НО commit_lane_opt is not None ветка ОБХОДИТ assign_color.
```

Итоговый цвет `987c9e8` = **0 GREEN**, не **15 GOLD** по имени ветки.

#### Каскадный эффект на mainline

После того как `92bfc03` получил `lane_colors[0] = 6 PINK`, **все** следующие
merge коммиты mainline, чей `commit_lane_opt = 0`, наследуют тот же
`lane_colors[0] = 6`:

```
84 92bfc03  lane=0  color=6 PINK  ← continue_lane(0) = 6
85 27abde0  lane=0  color=6 PINK  ← continue_lane(0) = 6
86 645f24c  lane=0  color=6 PINK
87 c6488fc  lane=0  color=6 PINK
...
94 61a8763  lane=0  color=6 PINK
```

Каждый из этих merge коммитов рисует `TEE_RIGHT` на col 0:

```
row 84  col 0: TEE_RIGHT  c=1  p=6   ← горизонталь к c=1 (BLUE) ветке, pipe=PINK
row 85  col 0: TEE_RIGHT  c=1  p=6
row 86  col 0: TEE_RIGHT  c=2  p=6   ← горизонталь к c=2 (RED) ветке
row 87  col 0: TEE_RIGHT  c=3  p=6
...
```

`c=N` — цвет горизонтальной половинки (цвет **другой** ветки, с которой
пересекается mainline). `p=6` — цвет вертикали на lane 0 (= PINK).

---

## Три симптома — объяснение через код

### Симптом 1: «В начале от создания у ветки один цвет похож на тёмнокрасный»

**Что видит пользователь:** tip-коммит ветки `987c9e8` окрашен в GOLD
(`#C4912E`, idx=15), что субъективно похоже на тёмный красно-коричневый
(особенно рядом с RED idx=2 `#782B24` или RUST idx=14 `#B5453C`).

**Откуда это:** в самом начале жизненного цикла ветки (когда merge `31b22352`
ещё не существовал в `history`), `987c9e8` обрабатывался как новый коммит:

```python
commit_lane_opt = None
# → assign_color(23, "3mk4yl/fix-dict-unhashable-bug")
# = _pick_branch_color("3mk4yl/fix-dict-unhashable-bug") = 15 (GOLD)
```

Пользователь видит GOLD.

### Симптом 2: «Потом он переходит в зелёный»

**Что видит пользователь:** после merge ветки в main (или после `git fetch`
с новым merge коммитом в `history`) `987c9e8` окрашивается в GREEN
(`#1A5924`, idx=0).

**Откуда это:** теперь `history` содержит merge `31b22352`, который
обрабатывается **раньше** `987c9e8` (newest first). `31b22352` не имеет
`primary_branch` (никто не указывает на него), получает fallback цвет
(`_pick_fallback` → 0 GREEN в этом запуске) и при обработке
`parent[0] = 987c9e8` записывает:

```python
lanes[lane] = "987c9e8"               # lanes[23]
oid_color_index["987c9e8"] = 0        # GREEN
```

Когда `987c9e8` обрабатывается, `commit_lane_opt is not None`, и
`continue_lane(23)` возвращает `lane_colors[23] = 0 GREEN`. Цвет по имени
(`15 GOLD`) **никогда не применяется**.

Пользователь видит GREEN.

### Симптом 3: «При пересечениях с другими ветками половинки возле пересечения окрашиваются в цвет пересекаемой ветки»

**Что видит пользователь:** в окрестности `31b22352` mainline (lane 0)
состоит из merge коммитов, у которых:

- `commit_color_index = 6` (PINK `#7D2559`) — унаследован от `92bfc03`,
  который получил fallback от `_pick_fallback(0)` в момент обработки
  `parent[1]` для `31b22352`.
- На col 0 каждый merge рисует `TEE_RIGHT` с `c=N` (цвет **другой**
  ветки, в которую уходит горизонталь) и `p=6` (PINK pipe-цвет).

**Пользователь видит горизонтальные «половинки» в цветах других веток**,
а вертикаль mainline — в PINK, что **не является ни одним из логичных
цветов mainline** (master=BLUE idx=1 или fallback main=GREEN idx=0).

**Откуда это:** при обработке `parent[1] = 92bfc03` для `31b22352` —
строго в коде `graph_v2.py:629-643`:

```python
else:
    empty = _find_empty_lane(lanes)
    if empty is not None:
        new_lane = empty
    else:
        lanes.append(None)
        new_lane = len(lanes) - 1
    lanes[new_lane] = parent_sha
    parent_branch_names = oid_to_branches.get(parent_sha, [])
    parent_branch = parent_branch_names[0] if parent_branch_names else None
    new_color = color_assigner.assign_fork_sibling_color(new_lane, parent_branch)
    # parent_branch = None  (92bfc03 не имеет branch_name)
    # new_color = _pick_fallback(0) → 6 (PINK) в данном запуске
    oid_color_index[parent_sha] = new_color
    lane_color_index[new_lane] = new_color  # lane_colors[0] = 6
```

Этот idx=6 «отравляет» lane 0 для всех последующих mainline merge коммитов.

---

## Корневая причина

В `src/core/graph_v2.py:561-573`:

```python
commit_branch_names = oid_to_branches.get(commit.sha, [])
primary_branch = commit_branch_names[0] if commit_branch_names else None

commit_color_index: int
if commit_lane_opt is not None:
    commit_color_index = color_assigner.continue_lane(lane)
elif not nodes or all(n.commit is None for n in nodes):
    commit_color_index = color_assigner.assign_main_color(lane, primary_branch)
else:
    commit_color_index = color_assigner.assign_color(lane, primary_branch)
```

**Проблема:** когда коммит уже tracking на lane (т.е. `commit_lane_opt is not None`),
его собственный `primary_branch` **никогда не используется**. Цвет всегда
берётся из `lane_colors[lane]` — кэша, заполненного ранее обработанным
merge коммитом.

Это приводит к **двум симптомам одновременно**:

1. **Tip-коммит ветки = цвет merge коммита**, а не цвет по имени ветки.
2. **Mainline в окрестности merge = fallback от `parent[1]`**,
   потому что у merge коммита `primary_branch = None` и его
   `parent[1]` получает первый свободный fallback-цвет.

Симптом 2 ещё усиливается тем, что `_pick_fallback` зависит от
порядка обработки — для разных репозиториев и разных HEAD результат
будет разным (idx=6 в нашем случае, но могло быть любое 0–23).

---

## Направления исправления

### Вариант A: точечный фикс — использовать `primary_branch` даже при existing lane

В `graph_v2.py:566`:

```python
if commit_lane_opt is not None:
    if primary_branch is not None:
        commit_color_index = color_assigner.assign_color(lane, primary_branch)
    else:
        commit_color_index = color_assigner.continue_lane(lane)
```

**Эффект:** tip коммита ветки получит цвет по её имени (GOLD 15),
а mainline merge коммиты продолжат использовать кэшированный
lane-цвет (т.к. у них `primary_branch` тоже None — не имеем
ветки, указывающей на них напрямую).

Чтобы починить и mainline, нужно что-то из вариантов B/C.

### Вариант B: пересмотр parent-pre-coloring

В `graph_v2.py:622-643` (parent lanes) сейчас:

```python
elif parent_idx == 0:
    lanes[lane] = parent_sha
    oid_color_index[parent_sha] = commit_color_index   # ← слишком ранняя запись
```

`oid_color_index[parent_sha]` записывается **до** того как parent реально
обработан как commit. Это и есть «отравление».

Альтернатива: не записывать `oid_color_index[parent_sha]` здесь,
а дождаться момента когда parent сам обработается (тогда у него
будет шанс через `assign_color(lane, primary_branch)`).

Но это меняет порядок обработки — нужно проверять, что все
уже работающие тесты остаются зелёными.

### Вариант C: для merge коммитов брать `primary_branch` из `origin/main` / `master`

Если merge коммит **содержится** в `origin/main` (типичный случай
«merge ветки в main»), то в `oid_to_branches` есть `origin/main`,
но НЕ bare `main`. Можно расширить `_pick_branch_color` (или
отдельный pre-pass) чтобы при отсутствии bare `main` использовать
`origin/main` или `master` как fallback для mainline.

Но это изменение семантики `_pick_branch_color` — тоже надо тестировать.

### Рекомендуемый подход

Комбинация A + минимальный B:

1. **A** — гарантирует, что коммиты с известной веткой получают свой цвет.
2. **B** — убрать предзапись `oid_color_index[parent_sha]` для **merge**
   коммитов (когда есть второй parent) либо для всех parent lanes, оставив
   только `lanes[lane] = parent_sha` (lane tracking). Это позволит mainline
   коммитам, у которых **тоже** есть `primary_branch` (`master` после PR
   merge), использовать свой цвет.

---

## Скрипты воспроизведения

- `tools/reproduce_31b22352_bug.py` — исходный repro скрипт.
  Печатает ASCII-снимок окрестности TARGET и verdict.

- `tools/debug_31b22352.py` — детальный дамп 50 строк вокруг TARGET
  с branch_names, lane, color_index, color hex, col 0 и col 46 cells.

- `tools/debug_with_fake_branch.py` — добавляет фейковую ветку
  `3mk4yl/fix-dict-unhashable-bug` с target=`987c9e8` (когда ветка
  уже удалена в репозитории), запускает `build_graph` и смотрит
  как граф строится с этой веткой.

- `tools/debug_branches.py` — печатает все ветки репозитория с их
  computed color (`_pick_branch_color`).

- `tools/trace_lanes.py` — пошаговая трассировка состояния `lanes`,
  `lane_colors`, `oid_colors` для WATCH_SHAS
  (`987c9e8`, `31b22352`, `7c321744`, `34723c1`, `92bfc03`,
  `b648bd2`).

---

## Запуск воспроизведения

```powershell
# Из корня проекта:
python tools/reproduce_31b22352_bug.py
python tools/debug_31b22352.py
python tools/debug_with_fake_branch.py
python tools/debug_branches.py
python tools/trace_lanes.py
```

Перед запуском убедиться, что репозиторий
`C:/work/git/other-repos/llm/gpt-researcher` доступен
и в нём выполнен `git checkout master` (HEAD на master, чтобы
`is_head=True` стоял на актуальной ветке).

---

## Затронутые файлы

- `src/core/graph_v2.py:455-573` — основной цикл `build_graph`,
  точка принятия решения `commit_lane_opt is not None`.
- `src/core/graph_v2.py:81-94` — `_pick_branch_color` (через `crc32`).
- `src/core/graph_v2.py:622-643` — parent lanes setup (pre-coloring).
- `src/core/graph_v2.py:319-323` — `ColorAssigner.continue_lane`
  (возвращает кэшированный lane-цвет).
- `src/core/graph_v2.py:359-366` — `ColorAssigner._pick_fallback`
  (sequential fallback).

---

## Дополнительные наблюдения

- **Override-таблица слишком узкая.** `_BRANCH_COLOR_OVERRIDES` содержит
  только `main/master/develop/dev` (idx=1 BLUE и idx=0 GREEN). Bare `main`
  override работает только для локальной ветки с именем `main`. Если
  локальной `main` нет (как в нашем случае — HEAD на `master`, а `main`
  существует только как `origin/main`), то все коммиты mainline идут
  через `_pick_fallback`.

- **`master` тоже не попадает в override.** `_pick_branch_color("master")`
  даёт `crc32("master") % 24 = 14` (`#B5453C` RUST) — не BLUE. Override
  работает через `lower()`, но значение `master=1` в override-таблице
  **перебивает** `crc32`. То есть master ВСЕГДА BLUE idx=1 — корректно.
  Проблема только когда merge коммит не имеет ни одной ветки, указывающей
  на него напрямую.

- **Head (HEAD branch) не используется как primary_branch для orphan
  коммитов.** Это значит, что коммиты в main, которые ещё не докатились
  до local master tip, не получают цвет master даже если HEAD на master.