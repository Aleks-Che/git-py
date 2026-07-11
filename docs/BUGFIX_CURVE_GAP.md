# Bugfix: визуальный разрыв между curve-endpoint и HORIZONTAL в merge-коннекторах

**Файл:** `src/ui/widgets/graph_panel.py`
**Дата:** 2026-07
**Тестовый артефакт:** `tools/reproduce_gpt_researcher_merge.py` (target commit `748923645850...` в репозитории `gpt-researcher`)

---

## 1. Симптом

На реальном репозитории `gpt-researcher` (и любом другом с merge-коммитами, у которых второй родитель находится на lane **правее** или **левее** коммита на расстоянии ≥ 2 lane'ов) в окне графа коммитов между curve-фигурой родителя (`BRANCH_RIGHT`/`MERGE_RIGHT`) и следующей `HORIZONTAL`-ячейкой виден **горизонтальный разрыв ~7 пикселей** на уровне `y_center` строки.

Пример из `gpt-researcher`, merge PR #1776 (commit `7489236`, row 33, lane 9, второй родитель на lane 6):

```
BRANCH_RIGHT   c=7   ← ci=12, parent_lane=6, кривая уходит вправо-вниз
HORIZONTAL     c=7   ← ci=13, горизонталь стартует на x = parent_x + lane_w/2
HORIZONTAL     c=7   ← ci=14, ...
HORIZONTAL     c=7   ← ci=15, ...
HORIZONTAL     c=7   ← ci=16, ...
TEE_LEFT       c=7   ← ci=18, коммит
```

В скриншоте `tools/out/repro_graph_merge_zoom3x.png` (3× zoom на merge-row) разрыв чётко виден как пустое место между концом кривой и началом горизонтали.

---

## 2. Геометрия бага

Параметры рендеринга (по умолчанию):
- `node_radius = 11`
- `lane_w = node_radius * 2 + 8 = 30`
- `cr = min(node_radius, 8.0) = 8` — радиус «скругления» угла BRANCH/MERGE-фигур

Для `BRANCH_RIGHT` / `MERGE_RIGHT` в `graph_panel.py`:

```python
path.moveTo(x, y_center + radius)
path.lineTo(x, y_center + cr)                       # вертикаль до (x, y+cr)
path.cubicTo(x, y_center, x, y_center, x + cr, y_center)   # кубик-кривая в (x+cr, y)
```

В точке `y = y_center` кривая заканчивается на `x + cr = x + 8` (от родительского центра).

Следующая ячейка — `HORIZONTAL` на gap-столбце родителя (`ci = parent_lane*2 + 1`):

```python
x = col_left + parent_lane * lane_w + lane_w / 2   # x = parent_x + 15
_draw_horiz_line(painter, x, y_center, lane_w, ...) # горизонталь от x до x+lane_w
```

Горизонталь **стартует** на `x + lane_w/2 = x + 15`.

**Разрыв:** `lane_w/2 − cr = 15 − 8 = 7` пикселей между `x + 8` (конец кривой) и `x + 15` (начало горизонтали).

---

## 3. Почему bugfix должен был быть в renderer, а не в `_build_row_cells`

Соблазн — добавить в `graph_v2.py` новый тип ячейки `HORIZONTAL_HALF_RIGHT`, который бы «мостил» gap-столбец `commit_lane*2 − 1` между TEE_LEFT и последним HORIZONTAL перед коммитом. Это лечит **детектор** (клеточный паттерн EMPTY gap), но **не лечит визуальный баг** — потому что настоящий разрыв находится в другой геометрической позиции: между `BRANCH_RIGHT` (центр родителя) и `HORIZONTAL` (gap родителя).

При тестировании такого фикса скрипт `tools/reproduce_gpt_researcher_merge.py` (после доработки другим агентом — Pattern B детектор) продолжит находить 4 строки с `curve-to-horizontal break` в `ci=9, ci=11, ci=13, ci=15`, потому что:

- Pattern A (EMPTY gap) убран → но это была не та дыра.
- Pattern B (HORIZONTAL сразу после BRANCH_RIGHT/MERGE_RIGHT) — нет, потому что HORIZONTAL всё равно стоит на `ci = parent_lane*2 + 1`.

Дополнительный артефакт первого «фикса» — клеточный тип `HORIZONTAL_HALF_RIGHT` плодит лишние сущности в core, не лечит визуальный баг и не проходит обновлённый тест. После отката core к чистому состоянию (`git checkout HEAD -- src/core/graph_v2.py`) и очистки от `HORIZONTAL_HALF_RIGHT` остался только этот фикс в renderer.

---

## 4. Фикс

Продлить endpoint кубической кривой в `BRANCH_RIGHT` / `MERGE_RIGHT` с `x + cr` до `x + lane_w/2` (то же — `x + 15` для `lane_w = 30`). Симметрично — для `BRANCH_LEFT` / `MERGE_LEFT` с `x − cr` до `x − lane_w/2`.

Все 4 функции получают опциональный параметр `lane_w: float = 30.0` (дефолт для обратной совместимости):

```python
def _draw_branch_right(
    painter: QPainter,
    x: float,
    y_center: float,
    radius: float,
    width: float,
    color: QColor,
    lane_w: float = 30.0,
) -> None:
    """Branch starting here, going down and right (╭)."""
    pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap)
    painter.setPen(pen)
    cr = min(radius, 8.0)
    path = QPainterPath()
    path.moveTo(x, y_center + radius)
    path.lineTo(x, y_center + cr)
    # Расширяем endpoint кривой до x + lane_w / 2, чтобы кривая
    # стыковалась со следующей HORIZONTAL-ячейкой (которая стартует
    # на x + lane_w / 2). Без этого остаётся видимый 7-пиксельный
    # разрыв между кривой и горизонталью (для lane_w=30, cr=8).
    path.cubicTo(x, y_center, x, y_center, x + lane_w / 2, y_center)
    painter.drawPath(path)
```

Аналогично для `_draw_branch_left`, `_draw_merge_right`, `_draw_merge_left`. Вызовы в `_draw_cell_row` дополнены передачей `lane_w`:

```python
elif t == _T_BRANCH_RIGHT:
    _draw_branch_right(painter, x, y_center, bot_half_h, edge_width, color, lane_w)
elif t == _T_BRANCH_LEFT:
    _draw_branch_left(painter, x, y_center, bot_half_h, edge_width, color, lane_w)
elif t == _T_MERGE_RIGHT:
    _draw_merge_right(painter, x, y_center, half_h, edge_width, color, lane_w)
elif t == _T_MERGE_LEFT:
    _draw_merge_left(painter, x, y_center, half_h, edge_width, color, lane_w)
```

`cr` остался `8` (для короткой вертикальной части и формы «правый угол» в первой половине кривой), endpoint кубика расширен.

---

## 5. Почему этот фикс работает

После расширения endpoint до `x + lane_w / 2`:

- `BRANCH_RIGHT` curve endpoint на `x + 15` совпадает со стартом `HORIZONTAL` на `x + 15` — стык без зазора.
- `BRANCH_LEFT` curve endpoint на `x − 15` совпадает с концом `HORIZONTAL_PIPE` (центрирован на `x − lane_w`, простирается до `x`). Endpoint уже **внутри** диапазона горизонтали, что и было верно и до фикса (для «going right» баг не наблюдался). Симметричное расширение сохраняет согласованность renderer'а.
- Визуально кривая становится длиннее (`cr=8` → `lane_w/2=15`), но форма («╭╮╰╯») сохраняется — управляющие точки кубика `(x, y_center)` обеспечивают плавный перегиб.

`cr = 8` оставлен без изменений — вертикальный сегмент от `(x, y+r)` до `(x, y+cr)` остаётся коротким, чтобы кривая визуально выглядела как «поворот на 90° у самого угла», а не как «длинная пологая дуга».

---

## 6. Проверка

**Reproduction-скрипт** `tools/reproduce_gpt_researcher_merge.py`:
- Exit code = **1** (BUG CONFIRMED остаётся для **Pattern A** — EMPTY gap cell в `ci=17`, который лежит между TEE_LEFT и последним HORIZONTAL merge-коннектора «going left»). Это **не тот же баг** — Pattern A фиксится либо отдельным заполнением gap-ячейки, либо доработкой алгоритма `_build_row_cells`.
- **Pattern B** (curve-to-horizontal) — **больше не детектируется**. Все GAP-сообщения в `tools/out/repro_layout.txt` теперь имеют вид `EMPTY gap cell between connector shapes (merge-connector loop skipped this column)`, а не `curve-to-horizontal break`.

**Bonus branch test** `tools/reproduce_gpt_researcher_bonus_branch.py`:
- Exit code = **1** (BUG CONFIRMED — dangling rightward connector при `ci=2 → ci=3`). Это **отдельный клеточный баг** в `_build_row_cells` (EMPTY ячейка между HORIZONTAL_PIPE и BRANCH_LEFT), не связан с кривыми.

**Unit-тесты** `tests/core/` + `tests/viewmodels/test_graph_viewmodel.py`:
- **222/222 проходят**. Никаких регрессий.

**Визуальная проверка** `tools/out/repro_graph_merge_zoom3x.png`:
- Разрыв между merge-curve и горизонталью **визуально отсутствует**. Линия непрерывна от commit'а до parent'а.
- Артефактов в других местах графа (fork-коннекторы, branch icons) нет — все 4 затронутые функции (BRANCH_LEFT/RIGHT, MERGE_LEFT/RIGHT) ведут себя консистентно.

---

## 7. Изменённые файлы

```
 src/ui/widgets/graph_panel.py | 32 ++++++++++++++++++++++++--------
 1 file changed, 24 insertions(+), 8 deletions(-)
```

`src/core/graph_v2.py` — **не трогали**. Все 222 unit-теста core проходят без изменений.

---

## 8. Что осталось (для следующих этапов)

Этот фикс убирает **визуальный curve-to-horizontal разрыв** и **Pattern B** детектора. Что осталось:

1. **Pattern A** в `tools/reproduce_gpt_researcher_merge.py` — `EMPTY gap cell at ci=17` для merge-коннектора «going left» между последним HORIZONTAL и TEE_LEFT. Лечится либо заполнением gap-ячейки в `_build_row_cells` новым клеточным типом (или HORIZONTAL), либо модификацией алгоритма `_build_row_cells` чтобы заполнить gap-ячейку `commit_lane*2 − 1`. **Не делал** в этом фиксе — артефакт предыдущей попытки (HORIZONTAL_HALF_RIGHT) был откачен вместе с core, чтобы не плодить лишние сущности в чистом виде.

2. **Bonus branch test** `tools/reproduce_gpt_researcher_bonus_branch.py` — пустая ячейка `ci=3` между HORIZONTAL_PIPE и BRANCH_LEFT в merge-коннекторе «going right». Тот же класс багов — `EMPTY` ячейка в gap-позиции. Лечится аналогично в `_build_row_cells`.

3. **Расширение детектора** — сейчас `tools/reproduce_gpt_researcher_merge.py` возвращает `1` при любом найденном gap (Pattern A или Pattern B). Pattern B убран этим фиксом. Pattern A остаётся, но это **отдельный баг** алгоритма раскладки, не renderer'а.