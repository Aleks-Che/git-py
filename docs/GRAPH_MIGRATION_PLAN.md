# План миграции графа коммитов на клеточную модель (из проекта keifu)

**Дата создания:** 2026-06-08
**Источник:** `graph.rs` (752 строки, Rust + git2-rs) — эталонная реализация из проекта keifu.

---

## 1. Проблемы текущей реализации

Текущий `src/core/graph.py` (592 строки) использует трёхфазный алгоритм раскладки:

1. **Phase 1 (priority walk)** — обход веток по первому родителю, остановка на общих предках.
2. **Phase 2 (orphan walk)** — добивка коммитов без веток по времени.
3. **Phase 3 (lane compaction)** — жадная интервальная раскраска для сжатия колонок.

**Известные баги и ограничения:**
- Неверная отрисовка сложных историй с fork-точками (коммит с 2+ детьми). Ветки, ответвлённые от одного родителя, не соединяются визуально в одной точке.
- Нет клеточной модели (`CellType`) — рендеринг вычисляет геометрию на лету в `graph_panel.py`, что приводит к дублированию логики и ошибкам при рисовании пересечений.
- WIP и stash узлы — синтетические, вставляются во ViewModel, а не в ядре. Это нарушает правило «core/ не знает о рабочем дереве», но цена — дублирование логики вставки и проблемы с позиционированием.
- Слияние lane'ов (когда ветка заканчивается и вливается в другую) реализовано неявно через orphan walk — нет явного детекта «ending lane».
- Нет fork-connector rows — строк-разделителей, которые показывают точку ветвления нескольких веток от одного родителя.
- Нет fork siblings — особой обработки первого родителя merge-коммита, который лежит на fork-точке.

---

## 2. Целевая архитектура (из `graph.rs`)

### 2.1. Клеточная модель (`CellType`)

Каждая строка графа содержит вектор клеток. Клетка — атомарный элемент отрисовки:

```python
class CellType(Enum):
    EMPTY = 0             # пусто
    PIPE = 1              # │ вертикальная линия (активный lane)
    COMMIT = 2            # ● узел коммита
    BRANCH_RIGHT = 3      # ╭ начало ветки вправо
    BRANCH_LEFT = 4       # ╮ начало ветки влево
    MERGE_RIGHT = 5       # ╰ слияние справа (снизу-вверх)
    MERGE_LEFT = 6        # ╯ слияние слева (снизу-вверх)
    HORIZONTAL = 7        # ─ горизонтальная линия
    HORIZONTAL_PIPE = 8   # ─┼─ пересечение горизонтали с вертикалью
    TEE_RIGHT = 9         # ├ T-образный стык вправо
    TEE_LEFT = 10         # ┤ T-образный стык влево
    TEE_UP = 11           # ┴ T-образный стык вверх (fork point, средняя ветка)
```

Каждая клетка `{PIPE, COMMIT, BRANCH_*, MERGE_*, HORIZONTAL, TEE_*}` несёт `color_index: int` — индекс цвета ветки. `HORIZONTAL_PIPE` несёт `(horizontal_color, pipe_color)`.

### 2.2. `GraphNode`

```python
@dataclass
class GraphNode:
    commit: CommitInfo | None      # None для fork-connector row и uncommitted
    lane: int                      # позиция lane
    color_index: int               # индекс цвета (из ColorAssigner)
    branch_names: list[str]        # имена веток на этом коммите
    is_head: bool                  # HEAD ли это
    is_uncommitted: bool           # узел незакоммиченных изменений
    uncommitted_count: int | None  # кол-во незакоммиченных файлов
    cells: list[CellType]          # клетки строки
```

### 2.3. `GraphLayout`

```python
@dataclass
class GraphLayout:
    nodes: list[GraphNode]
    max_lane: int
```

### 2.4. `ColorAssigner`

Управляет выделением и освобождением цветов веток:
- `advance_row()` — переход к новой строке
- `continue_lane(lane)` — продолжить существующую ветку (тот же цвет)
- `assign_main_color(lane)` — зарезервировать главный цвет (первый коммит)
- `assign_color(lane)` — выделить новый цвет (новая ветка)
- `assign_fork_sibling_color(lane)` — цвет для fork sibling
- `begin_fork()` / `end_fork()` — вход/выход из fork-контекста
- `release_lane(lane)` — освободить lane (ветка закончилась)
- `is_main_lane(lane)` — проверка, главный ли это lane

### 2.5. Ключевые отличия от текущего алгоритма

| Аспект | Текущий (`graph.py`) | Целевой (`graph.rs`) |
|--------|---------------------|----------------------|
| Модель строки | `lane` + `display_column` | `cells: list[CellType]` — полная геометрия |
| Цвета | hex-строки из палитры | int-индексы, `ColorAssigner` |
| Fork-точки | Не детектятся | Явный детект, connector row с `TeeUp`/`MergeLeft` |
| Слияние lane'ов | Неявное (orphan walk) | Явное: `ending_lane` → `main_lane` |
| Uncommitted | Синтетический WIP во ViewModel | Вставляется в `build_graph()` на позицию 0 |
| Порядок обхода | По веткам (priority) + по времени (orphan) | По списку коммитов (один проход) |
| Родители | Только первый родитель для lane | Все родители участвуют в геометрии |

---

## 3. План миграции по этапам

### Этап M1: Новый модуль `src/core/graph_v2.py` (ядро, ~600 строк)

**Цель:** Реализовать `build_graph()`, `GraphNode`, `GraphLayout`, `CellType`, `ColorAssigner` — полный аналог `graph.rs::build_graph()` без зависимостей от PySide6.

**Задачи:**
1. Создать `src/core/graph_v2.py` с нуля.
2. Перенести enum `CellType` (11 вариантов).
3. Реализовать `ColorAssigner`:
   - `advance_row()`, `continue_lane(lane)`, `assign_main_color(lane)`, `assign_color(lane)`, `assign_fork_sibling_color(lane)`.
   - `begin_fork()`, `end_fork()`, `release_lane(lane)`.
   - `is_main_lane(lane)`.
   - Цветовая палитра — 12 цветов, как в `BRANCH_PALETTE` (индексы 0..11).
   - Резервирование `UNCOMMITTED_COLOR_INDEX = 12`.
4. Реализовать `GraphNode` dataclass с полями: `commit`, `lane`, `color_index`, `branch_names`, `is_head`, `is_uncommitted`, `uncommitted_count`, `cells`.
5. Реализовать `GraphLayout` dataclass: `nodes`, `max_lane`.
6. Реализовать `build_graph()`:
   - Вход: `commits: list[CommitInfo]`, `branches: list[BranchInfo]`, `uncommitted_count: int | None`, `head_commit_oid: str | None`.
   - Построить `oid -> branch_names` маппинг.
   - Построить `oid -> row` маппинг.
   - Детект fork-точек: коммиты с 2+ детьми → `fork_points: set[str]`.
   - Lane tracking: `lanes: list[str | None]`.
   - Один проход по коммитам:
     - Найти/создать lane для коммита.
     - Если fork (2+ lane'ов с этим OID) → connector row.
     - Назначить цвет через `ColorAssigner`.
     - Обработать родителей (всех, не только первого):
       - Первый родитель → тот же lane.
       - Если родитель уже в другом lane → fork sibling (особая обработка).
       - Остальные родители → новые lane'ы с fork sibling цветами.
     - Детект lane merge: если lane коммита отличается от lane уже отслеживаемого родителя.
     - Построить клетки через `build_row_cells_with_colors()`.
   - Вставить uncommitted узел (если есть) в начало — найти lane для него.
   - Вернуть `GraphLayout`.
7. Реализовать `build_row_cells_with_colors()`:
   - Вертикальные линии для активных lane'ов.
   - Узел коммита на своём lane.
   - Горизонтальные соединения к родителям на других lane'ах.
   - Выбор символа: `Branch*` (новый lane), `Tee*` (lane продолжается вниз), `Merge*` (lane заканчивается).
8. Реализовать `build_fork_connector_cells()`:
   - T-стык на главном lane (`TeeRight`).
   - Горизонтальные линии к сливающимся lane'ам.
   - `MergeLeft` на крайнем правом, `TeeUp` на промежуточных.
9. Реализовать `graph_to_dicts(layout: GraphLayout) -> list[dict]` — сериализация для Qt signals.
10. Сохранить `BranchRef` и `_build_branch_refs_map()` из текущего `graph.py` (они нужны для левой колонки с ветками).

**API модуля:**
```python
__all__ = [
    "CellType",
    "GraphNode",
    "GraphLayout",
    "ColorAssigner",
    "build_graph",
    "graph_to_dicts",
    "BranchRef",
    "UNCOMMITTED_COLOR_INDEX",
    "BRANCH_PALETTE",
]
```

**Тесты (новый файл `tests/core/test_graph_v2.py`, ~40 тестов):**
- Пустая история, нет коммитов.
- Один коммит без веток.
- Линейная история (3 коммита, одна ветка).
- Ветвление: две ветки от общего предка.
- Слияние: merge-коммит с двумя родителями.
- Fork-точка: коммит с 3+ детьми → connector row.
- Fork sibling: merge, где первый родитель — fork-точка.
- Lane merge: ветка заканчивается и вливается.
- Uncommitted changes: узел в начале.
- Uncommitted + несколько коммитов.
- Цвета: `ColorAssigner` сохраняет цвета при fork.
- `graph_to_dicts()` round-trip.
- Производительность: 500 коммитов < 500ms (синтетический репо).

---

### Этап M2: Адаптация ViewModel (`src/viewmodels/graph_viewmodel.py`)

**Цель:** Переключить ViewModel на `graph_v2.build_graph()`.

**Задачи:**
1. Импортировать `build_graph`, `graph_to_dicts`, `UNCOMMITTED_COLOR_INDEX` из `src.core.graph_v2`.
2. Переписать `_compute_graph()`:
   - Убрать синтез WIP и stash узлов (теперь это делает `build_graph`).
   - Вызвать `build_graph(history, branches, uncommitted_count, head_sha)`.
   - Вернуть `graph_to_dicts(layout)`.
3. Обновить `to_dict()` в `GraphNode` (если используется для деталей) — добавить `cells`.
4. Убедиться, что сигнал `graph_updated(list[dict])` по-прежнему возбуждается с корректными данными.
5. **Не трогать** `get_commit_details()`, `search_commits()`, сигналы — они не зависят от графа.

**Тесты (обновить `tests/viewmodels/test_graph_viewmodel.py`):**
- Адаптировать существующие тесты под новую структуру `cells`.
- Убрать тесты, проверяющие синтез WIP/stash (эта логика ушла в core).
- Добавить тест: uncommitted узел появляется при грязном worktree (теперь через `build_graph`).

---

### Этап M3: Переработка виджета (`src/ui/widgets/graph_panel.py`)

**Цель:** Использовать `cells` из `GraphNode` для отрисовки вместо вычисления геометрии на лету.

**Задачи:**
1. Убрать текущую логику рисования рёбер (`_paint_edges`, вычисление L-образных путей).
2. Реализовать `_paint_cells(cells: list[CellType], x_base, y, lane_w)`:
   - `EMPTY` → ничего.
   - `PIPE(color)` → вертикальная линия цвета `BRANCH_PALETTE[color]`.
   - `COMMIT(color)` → круг/эллипс цвета `BRANCH_PALETTE[color]`.
   - `BRANCH_RIGHT(color)` → символ ╭ (дуга вправо-вниз).
   - `BRANCH_LEFT(color)` → символ ╮ (дуга влево-вниз).
   - `MERGE_RIGHT(color)` → символ ╰ (дуга вправо-вверх).
   - `MERGE_LEFT(color)` → символ ╯ (дуга влево-вверх).
   - `HORIZONTAL(color)` → горизонтальная линия ─.
   - `HORIZONTAL_PIPE(h_color, v_color)` → пересечение ─┼─.
   - `TEE_RIGHT(color)` → символ ├.
   - `TEE_LEFT(color)` → символ ┤.
   - `TEE_UP(color)` → символ ┴.
3. Рендерить глифы через `QPainterPath` с дугами (`arcTo`) или через `QPainter.drawLine` (упрощённый вариант — отрезки под 45°).
4. Обрабатывать fork-connector rows (строки без коммита) — отрисовывать только клетки, без сообщения и без аватара.
5. Адаптировать `_lane_x()` под новую модель `lane` (без `display_column` — сжатие теперь через клетки, а не через колонки).
6. Обновить `RenderConfig` — добавить ширину клетки (`cell_width`).
7. Отрисовка uncommitted узла: проверять `is_uncommitted`, использовать `UNCOMMITTED_COLOR_INDEX`.
8. Сохранить отрисовку branch labels, avatars, ref chips, контекстное меню — они не меняются.

**Детали рендеринга глифов (рекомендация):**

Вместо сложных кривых Безье для каждого символа, использовать упрощённую геометрию из отрезков:

- `BRANCH_RIGHT` ╭: вертикальный отрезок от центра клетки вниз + горизонтальный вправо + дуга 90° между ними.
- `BRANCH_LEFT` ╮: вертикальный вниз + горизонтальный влево + дуга.
- `MERGE_RIGHT` ╰: вертикальный вверх + горизонтальный вправо + дуга.
- `MERGE_LEFT` ╯: вертикальный вверх + горизонтальный влево + дуга.
- `TEE_RIGHT` ├: вертикальный отрезок через всю клетку + горизонтальный вправо от центра.
- `TEE_LEFT` ┤: вертикальный через всю клетку + горизонтальный влево от центра.
- `TEE_UP` ┴: горизонтальный через всю клетку + вертикальный вверх от центра.
- `HORIZONTAL_PIPE` ─┼─: горизонтальный через всю клетку + вертикальный через всю клетку.

**Важно — endpoint кривой BRANCH/MERGE должен доходить до `lane_w/2`, не до `cr=8`:**

Иначе виден 7-пиксельный горизонтальный разрыв между curve endpoint'ом (на `x + cr = x + 8`) и началом следующей `HORIZONTAL` (на `x + lane_w/2 = x + 15`). См. подробный разбор и фикс в `docs/BUGFIX_CURVE_GAP.md`.

---

### Этап M4: Замена старого модуля

**Цель:** Удалить `src/core/graph.py` и переименовать `graph_v2.py` → `graph.py`.

**Задачи:**
1. Убедиться, что все тесты `test_graph_v2.py` проходят.
2. Убедиться, что все тесты `test_graph_viewmodel.py` проходят.
3. Убедиться, что UI-тесты `test_graph_widget.py` / `test_graph_panel.py` проходят.
4. Удалить `src/core/graph.py`.
5. Переименовать `src/core/graph_v2.py` → `src/core/graph.py`.
6. Обновить импорты во всех файлах (их немного: `graph_viewmodel.py`, `test_graph.py`).
7. Удалить `test_graph_v2.py` (или переименовать в `test_graph.py`).
8. Прогнать полный набор тестов: `python -m pytest tests/ -x`.
9. Проверить `ruff check src/ tests/`.

---

### Этап M5: Стабилизация и краевые случаи

**Цель:** Обработать краевые случаи, которые могут проявиться на реальных репозиториях.

**Задачи:**
1. Тест на реальном репозитории с merge-коммитами (например, `pygit2` сам).
2. Тест: orphan-коммиты (нет веток, только detached HEAD).
3. Тест: stash на графе (проверить, что `kind="stash"` узлы корректно вставляются в `history` ДО вызова `build_graph`).
4. Тест: несколько stash'ей подряд.
5. Тест: unborn HEAD + uncommitted changes.
6. Тест: detached HEAD + uncommitted changes.
7. Тест: ветки с `/` в имени (не должны ломать парсинг).
8. Тест: remote-ветки (не должны создавать дубликаты с local).
9. Интеграционный тест: полный цикл «открыть репо → граф → клик по коммиту → детали».
10. Стресс-тест: 5000 коммитов, построение графа < 1с.

---

### Этап M6: Удаление мёртвого кода

**Цель:** Убрать код, который стал не нужен после миграции.

1. Удалить старый `RenderConfig` из `graph_panel.py` (если поля больше не используются).
2. Удалить `_compute_edge_geometry()` и подобные методы из `graph_panel.py`, заменённые рендерингом клеток.
3. Удалить `_compact_lanes()`, `_assign_lanes()`, `_assign_colors()` из старого `graph.py` (автоматически при удалении файла).
4. Удалить `display_column` из `GraphNode.to_dict()` — больше не используется.
5. Проверить `graph_widget.py` (старый QGraphicsView виджет): он может остаться как fallback, но его тесты нужно адаптировать или пометить skip.

---

## 4. Оценка трудозатрат

| Этап | Описание | Строк кода | Тестов | Часов |
|------|----------|-----------|--------|-------|
| M1 | Новый модуль `graph_v2.py` | ~600 | ~40 | 6-8 |
| M2 | Адаптация ViewModel | ~50 (изменения) | ~20 (обновить) | 1-2 |
| M3 | Переработка виджета | ~200 (изменения) | ~15 (обновить) | 3-4 |
| M4 | Замена старого модуля | ~20 | — | 0.5 |
| M5 | Стабилизация | ~100 (тесты) | ~15 (новые) | 2-3 |
| M6 | Удаление мёртвого кода | −200 (удалить) | — | 0.5 |
| **Итого** | | **~770** | **~90** | **13-18** |

---

## 5. Риски

1. **Совместимость `cells` с существующим `nodes_to_rows` / `to_dict()`.**  
   Сериализация `CellType` (enum) в JSON for Qt signals потребует преобразования в `int` или `str`.  
   *Митигация:* `to_dict()` возвращает `"cells": [c.value for c in self.cells]`.

2. **Производительность `cells`.**  
   Вектор клеток на каждую строку: `(max_lane + 1) * 2` элементов. Для 12 lane'ов и 5000 коммитов это ~120,000 клеток — приемлемо.  
   *Митигация:* Ленивое вычисление клеток только для видимых строк (виртуализация — Этап 10).

3. **Отрисовка глифов `Branch*` / `Merge*` / `Tee*`.**  
   Требует аккуратного `QPainterPath` с дугами. При неправильных координатах граф будет выглядеть сломанным.  
   *Митигация:* Начать с упрощённой геометрии (отрезки под 45°), добавить дуги во втором проходе.

4. **Обратная совместимость с `graph_widget.py` (QGraphicsView).**  
   Старый виджет использует `lane` и `display_column` для позиционирования. После миграции `display_column` исчезнет, а `cells` появится.  
   *Митигация:* Либо адаптировать `graph_widget.py`, либо удалить его (он уже не используется в `MainWindow`, только в тестах).

5. **WIP/stash синтез в core.**  
   Сейчас WIP и stash создаются во ViewModel. Перенос в core означает, что `build_graph()` должен принимать `CommitInfo` с `kind="wip"` / `kind="stash"` уже в списке.  
   *Митигация:* ViewModel продолжает синтезировать WIP/stash как `CommitInfo`, но вставляет их в `history` перед вызовом `build_graph()`. `build_graph()` обрабатывает `kind` для определения специального рендеринга.

---

## 6. Критерии приёмки

- [ ] `build_graph()` на пустом репо возвращает пустой `GraphLayout`.
- [ ] `build_graph()` на репо с одним коммитом возвращает 1 `GraphNode` с `Commit(0)` в клетках.
- [ ] `build_graph()` на репо с ветвлением рисует `BranchLeft`/`BranchRight`.
- [ ] `build_graph()` на репо со слиянием рисует `MergeLeft`/`MergeRight` и горизонтальные соединения.
- [ ] `build_graph()` на репо с fork-точкой вставляет connector row.
- [ ] `build_graph()` корректно обрабатывает uncommitted changes.
- [ ] Виджет рендерит все `CellType` варианты без визуальных артефактов.
- [ ] Все существующие тесты проходят (с адаптацией под новую модель).
- [ ] `ruff check src/ tests/` — чисто.
- [ ] Граф на реальном репозитории (например, сам `git-py`) выглядит корректно.
