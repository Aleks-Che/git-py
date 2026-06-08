# План миграции на swimlane-модель графа

## Источник
Модель основана на `scmHistory.ts` из VS Code (файл в корне репо: `scmHistory.ts`).
Ключевая функция: `renderSCMHistoryItemGraph` и `toISCMHistoryItemViewModelArray`.

## Мотивация

Текущие проблемы:
1. Remote-ветки на том же SHA что и локальные — не имеют собственной линии на графе
2. Remote-ветки на том же первом родителе (shared first-parent chain) — делят лейн с локальной веткой
3. Алгоритм раскладки трёхфазный (priority walk → orphan walk → compaction), сложно поддерживать
4. Цвета рёбер используют цвет коммита, а не цвет ветки — при shared-ancestor цвет «перехватывается» первой веткой

Swimlane-модель решает:
- Каждая ветка получает свой лейн автоматически через parent-relationships
- Цвет лейна = цвет ветки, а не цвет коммита
- Алгоритм однофазный: сверху вниз, строим input/output swimlanes для каждого ряда
- Естественно обрабатывает merge-коммиты, несколько родителей, общих предков

---

## Шаг 1: Новый тип данных в `core/graph.py`

### 1.1 `_SwimlaneEntry`
```python
@dataclass(frozen=True)
class _SwimlaneEntry:
    """Одна swimlane-колонка: SHA коммита + цвет лейна."""
    sha: str
    color: str  # hex-цвет ветки, которой принадлежит этот лейн
```

### 1.2 Новые поля в `GraphNode`
```python
@dataclass
class GraphNode:
    # ... существующие поля ...
    input_lanes: list[dict] = field(default_factory=list)   # list[{"sha": ..., "color": ...}]
    output_lanes: list[dict] = field(default_factory=list)  # list[{"sha": ..., "color": ...}]
```

Эти поля нужны для `_draw_edges`, чтобы знать какие swimlanes входят в ряд и выходят из него.

---

## Шаг 2: Новая функция `_build_swimlanes` в `core/graph.py`

### Сигнатура
```python
def _build_swimlanes(
    history: list[CommitInfo],           # коммиты, новейший первый
    branches: list[BranchInfo],          # все ветки (local + remote)
    head_target_sha: str | None,         # SHA на который указывает HEAD
    head_shorthand: str | None,          # имя текущей ветки (или "(detached)")
    branch_colors: dict[str, str],       # имя_ветки → hex-цвет (из _assign_branch_colors)
) -> list[dict]:
    """Возвращает список dict'ов, по одному на каждый ряд/коммит:
    {
        'sha': str,
        'lane': int,           # позиция коммита во input_lanes
        'color': str,          # цвет кружка коммита
        'input_lanes': list[dict],   # [{"sha": ..., "color": ...}, ...]
        'output_lanes': list[dict],  # [{"sha": ..., "color": ...}, ...]
    }
    """
```

### Алгоритм

```
output_lanes = сеем из branch-tips в порядке приоритета

Для tip в _priority_tips(branches, head_target_sha):
    color = branch_colors[tip.name]
    output_lanes.append(_SwimlaneEntry(sha=tip.target_sha, color=color))

Для каждого commit в history (сверху вниз, новейший первый):
    sha = commit.sha

    # ---- входные лейны: копия выходных лейнов предыдущего ряда ----
    input_lanes = copy(output_lanes)

    # ---- найти позицию коммита во входных лейнах ----
    idx = найти индекс, где input_lanes[i].sha == sha
    если не найден:
        # Новый branch-tip (не было в seed'е — orphan коммит)
        color = sha_to_branch_color.get(sha, BRANCH_PALETTE[0])
        idx = len(input_lanes)
        input_lanes.append((sha, color))

    commit_color = sha_to_branch_color.get(sha, input_lanes[idx].color)

    # ---- собрать все позиции этого коммита (если несколько лейнов ведут в него) ----
    commit_positions = {i for i, (s, _) in enumerate(input_lanes) if s == sha}

    # ---- построить выходные лейны ----
    output_lanes = []
    first_parent_placed = False
    primary_idx = min(commit_positions)  # первый лейн — первичный

    for i, (s, c) in enumerate(input_lanes):
        if i in commit_positions:
            # это лейн коммита
            if i == primary_idx and commit.parents and not first_parent_placed:
                # первый родитель занимает место коммита
                parent_color = sha_to_branch_color.get(commit.parents[0], c)
                output_lanes.append((commit.parents[0], parent_color))
                first_parent_placed = True
            # иначе лейн завершается здесь (коммит = лист)
        else:
            # чужой лейн — пропускаем насквозь
            output_lanes.append((s, c))

    # ---- дополнительные родители (merge) создают новые лейны справа ----
    start = 1 if first_parent_placed else 0
    for p in commit.parents[start:]:
        p_color = sha_to_branch_color.get(p,
            BRANCH_PALETTE[len(output_lanes) % len(BRANCH_PALETTE)])
        output_lanes.append((p, p_color))

    # ---- дедупликация SHA в выходных лейнах ----
    # Если один и тот же SHA встречается в нескольких лейнах,
    # оставляем первый (с его цветом), удаляем дубликаты.
    seen = set()
    deduped = []
    for s, c in output_lanes:
        if s not in seen:
            seen.add(s)
            deduped.append((s, c))
    output_lanes = deduped

    rows.append({...})
```

### Важно: seed swimlanes из branch-tips
До обхода коммитов нужно «засеять» выходные лейны branch-tip'ами в порядке приоритета (`_priority_tips`). Это гарантирует что:
- HEAD-ветка всегда в лейне 0
- Локальные ветки перед remote
- Remote-ветки в правых лейнах

Без seed'а первый обработанный коммит (новейший по времени) захватит лейн 0, даже если это не HEAD-ветка.

### Построение `sha_to_branch_color`
```python
sha_to_branch_color: dict[str, str] = {}
branch_colors = _assign_branch_colors(branches, head_target_sha)
for b in branches:
    if b.target_sha:
        sha_to_branch_color[b.target_sha] = branch_colors[b.name]
```
Для коммита-не-tip (нет прямого указания ветки) цвет берётся из input_lanes[idx].color.

---

## Шаг 3: Изменения в `compute_layout`

Заменить вызовы `_assign_lanes` + `_assign_colors` + `_compact_lanes` + stash-логику на:

```python
def compute_layout(history, branches, tags, head_target_sha, head_shorthand, *, max_columns=12):
    if not history:
        return []

    refs_by_sha = _build_refs_map(branches, tags, head_target_sha, head_shorthand)
    branch_colors = _assign_branch_colors(branches, head_target_sha)

    swimlane_rows = _build_swimlanes(history, branches, head_target_sha, head_shorthand, branch_colors)

    branch_lanes_by_name = _compute_branch_lanes(branches, swimlane_rows)
    branch_refs_by_sha = _build_branch_refs_map(branches, branch_lanes_by_name, branch_colors)

    nodes = []
    for row, commit in enumerate(history):
        sw = swimlane_rows[row]
        nodes.append(GraphNode(
            sha=commit.sha,
            ...
            lane=sw['lane'],
            display_column=sw['lane'],     # без compaction
            color=sw['color'],
            input_lanes=sw['input_lanes'],  # новое поле
            output_lanes=sw['output_lanes'],# новое поле
            branch_refs=branch_refs_by_sha.get(commit.sha, []),
            ...
        ))
    return nodes
```

Удаляемые функции:
- `_assign_lanes` — заменяется swimlane-моделью
- `_assign_colors` — заменяется swimlane-моделью
- `_compact_lanes` — не нужен, swimlane index = display column
- `_first_free_lane` — не нужен
- stash-логика в compute_layout — перенести в `_build_swimlanes` или оставить отдельно

---

## Шаг 4: Изменения в `_draw_edges` (`graph_panel.py`)

Текущий код рисует parent-child рёбра. Нужно добавить отрисовку вертикальных swimlane-линий.

### 4.1 Вертикальные линии swimlanes

Для каждого ряда (кроме последнего):
- Пройти по `input_lanes` ряда
- Для каждого лейна нарисовать вертикальную линию от центра ряда до центра следующего ряда
- Цвет линии = `input_lanes[i].color` (цвет ветки)
- Вертикальная линия идёт от `y_center + r` текущего ряда до `y_center - r` следующего ряда
- Если в `output_lanes` нет лейна на той же позиции — линия обрывается

Псевдокод:
```python
def _draw_swimlane_verticals(painter, row, next_row, col_cx, lane_w, dh, r):
    """Рисует вертикальные линии swimlane-колонок между двумя рядами."""
    for i, entry in enumerate(row.get('output_lanes', [])):
        sha = entry['sha']
        color = QColor(entry['color'])
        cx = _lane_x(i, col_cx, lane_w)
        
        # Найти этот SHA в input_lanes следующего ряда
        next_idx = None
        for j, inp in enumerate(next_row.get('input_lanes', [])):
            if inp['sha'] == sha:
                next_idx = j
                break
        
        if next_idx is None:
            continue  # лейн завершается здесь
        
        next_cx = _lane_x(next_idx, col_cx, lane_w)
        y1 = _row_y(row['row']) + dh / 2 + r
        y2 = _row_y(next_row['row']) + dh / 2 - r
        
        if abs(cx - next_cx) < 0.5:
            # прямая вертикаль
            painter.setPen(QPen(color, edge_width, SolidLine, RoundCap))
            painter.drawLine(int(cx), int(y1), int(next_cx), int(y2))
        else:
            # изогнутая линия (переход в другой лейн)
            # логика как в текущем _draw_edges: /, -, \
            ...
```

### 4.2 Лейн → лейн соединения

Для merge-коммитов (несколько родителей) нужно соединять лейны:
- Родительские лейны (output_lanes без входных соответствий) соединяются с первичным лейном коммита
- Использовать текущую логику L-образных кривых из `_draw_edges`

### 4.3 Упрощение

После swimlane-модели текущий `_draw_edges` можно заменить на:
1. Отрисовка вертикальных swimlane-линий (4.1)
2. Отрисовка соединений для merge-родителей (4.2)
3. Отрисовка кружков коммитов (уже есть `_draw_graph_node`)

---

## Шаг 5: Обработка stash и WIP

В swimlane-модели stash и WIP — это синтетические коммиты в `history`.
Они обрабатываются как обычные коммиты, но с особыми правилами:

### Stash
- Stash имеет `parents=[parent_sha]` (один родитель)
- При обработке: занимает позицию родителя в input_lanes, создаёт новый output_lane для родителя
- Визуально: золотой пунктирный кружок (уже реализовано)

### WIP
- WIP имеет `parents=[head_target_sha]` (один родитель)
- Обрабатывается аналогично stash
- Визуально: серый пунктирный кружок (уже реализовано)

### Seed swimlanes для stash/WIP
Stash и WIP НЕ ветки — они не должны быть в seed'е `output_lanes`.
Они обрабатываются как обычные коммиты при обходе `history`.

Но WIP всегда наверху (row 0). При отсутствии seed'а для WIP, он создаст новый лейн в конце input_lanes.
Это правильное поведение — WIP всегда справа.

---

## Шаг 6: Тесты

### 6.1 Обновить существующие тесты
Тесты в `tests/core/test_graph.py` проверяют текущий алгоритм. Нужно:
- Оставить тесты на `_assign_branch_colors` (не меняется)
- Обновить тесты на `compute_layout` — проверить что `lane`/`display_column`/`color` корректны
- Обновить тесты на `_assign_lanes` — удалить/заменить на тесты `_build_swimlanes`
- Обновить тесты на `_compact_lanes` — удалить

### 6.2 Новые тесты
- `test_swimlane_head_branch_lane_zero` — HEAD-ветка всегда в лейне 0
- `test_swimlane_local_before_remote` — локальные ветки левее remote
- `test_swimlane_remote_branch_gets_own_lane` — remote-ветка на своём SHA получает отдельный лейн
- `test_swimlane_shared_ancestor_merge` — две ветки сливаются в общего предка
- `test_swimlane_merge_commit_creates_new_lanes` — merge создаёт дополнительные лейны
- `test_swimlane_output_dedup` — один SHA не появляется дважды в output_lanes
- `test_swimlane_stash_gets_own_lane` — stash не ломает основные лейны
- `test_swimlane_wip_above_head` — WIP над HEAD в отдельном лейне

---

## Шаг 7: План миграции

### Неделя 1: Core
1. Добавить `_SwimlaneEntry`, поля `input_lanes`/`output_lanes` в `GraphNode.to_dict()`
2. Реализовать `_build_swimlanes` с seed'ом из `_priority_tips`
3. Обновить `compute_layout` — использовать `_build_swimlanes`
4. Адаптировать все core-тесты (около 30 тестов)
5. Убедиться: 32+ core-тестов проходят, ruff clean

### Неделя 2: Rendering
1. Реализовать `_draw_swimlane_verticals` в `graph_panel.py`
2. Обновить `_draw_edges` — использовать swimlane-данные из `input_lanes`/`output_lanes`
3. Удалить stub-логику (уже удалена)
4. UI-тесты: проверить что линии рисуются правильными цветами
5. Ручное тестирование на реальных репозиториях с remote-ветками

### Неделя 3: Стабилизация
1. Удалить неиспользуемые функции (`_assign_lanes`, `_assign_colors`, `_compact_lanes`, `_first_free_lane`)
2. Прогнать полный набор тестов (666+)
3. Исправить регрессии
4. Проверить производительность на 500+ коммитах (должна быть лучше — однофазный алгоритм)

---

## Ключевые отличия от текущей реализации

| Аспект | Текущая | Swimlane |
|--------|---------|----------|
| Фазы раскладки | 3 (priority, orphan, compact) | 1 (top-down swimlane) |
| Назначение лейнов | Первый зашедший забирает SHA | Seed из branch-tips по приоритету |
| Цвет ребра | Цвет коммита (`row_data["color"]`) | Цвет ветки из `input_lanes[i].color` |
| Remote-лейны | Только если SHA уникален | Всегда свой лейн (через seed) |
| Shared ancestor | Первая ветка «захватывает» цвет | Каждый лейн сохраняет цвет ветки |
| Merge-коммиты | Orphan walk для доп. родителей | Новые лейны справа для доп. родителей |
| Compaction | Отдельный проход `_compact_lanes` | Не нужен (swimlane index = display column) |
