# Граф коммитов

Визуализация истории репозитория в виде ASCII-графа с полосами (lanes) и цветами веток.

## Основные возможности

- **Cell-based рендеринг** — каждая строка графа — это массив ячеек `CellInfo`, каждая ячейка предписыжет виджету, что именно рисовать. Вся геометрия вычисляется в `core/`.
- **Автоматическое назначение полос** — каждому коммиту выделяется своя полоса, полоса освобождается, когда ветка заканчивается.
- **Форк-пойнты** — коммиты с двумя и более детьми получают merge-коннекторы, объединённые в строку форк-пойнта (TEE_RIGHT → HORIZONTAL → TEE_UP → MERGE_LEFT).
- **Детерминированные цвета веток** — цвет ветки вычисляется хешированием её имени с точными оверрайдами, что даёт стабильные цвета между запусками.
- **Uncommitted changes (WIP)** — вставляется на полосу HEAD (lane 0) со спеццветом. Если на lane 0 выше HEAD есть коннекторы, несовместимые с WIP (горизонтали), WIP смещается на следующую свободную полосу.
- **Стэши** — отображаются как полноценные коммит-узлы. WIP и стэши выше него не должны пересекаться: стэши сдвигаются вправо лесенкой (lane 1, 2, 3, …), а на строке HEAD рисуется форк-коннектор через `_build_fork_connector_cells`.
- **Ребаланс стэшей при WIP** — стэши, чей первый родитель — HEAD, перед вставкой WIP сдвигаются на offset-полосы. Это освобождает lane 0 для WIP:

  ```
    ● WIP          (lane 0)
    │  ○ stash1     (lane 1 — COMMIT без горизонтали)
    │  │  ○ stash2   (lane 2 — PIPE на промежуточных lane)
    ├──┴──┘ HEAD   (форк-коннектор на строке HEAD)
    ● parent
  ```

  В строке стэша нет горизонтальных коннекторов (TEE_LEFT, HORIZONTAL) — только COMMIT и, для несмежных полос, PIPE на промежуточных lane для поддержания вертикальной непрерывности через gap-bridge. Всё соединение отображается на строке HEAD через `_build_fork_connector_cells`. См. `_rebalance_stashes_for_wip` в `src/core/graph_v2.py`.

- **Branch-рефы** — на левой панели рядом с коммитом показываются метки веток (HEAD, `main`, `feature/…`). Одна и та же ветка показывается для каждого коммита, где она останавливается.
- **Производительность** — синтетический репозиторий на 5000 коммитов рендерится менее чем за 1 секунду.

## Branch-чипы (chip column) — интерактивность

В самой левой колонке графа (`Branches` column) рядом с каждым коммитом рисуется набор цветных «чипов» — по одному на каждую ссылку (HEAD, локальная/удалённая ветка), указывающую на этот коммит. Это самое компактное представление «что сейчас стоит на коммите». Реализовано в `src/ui/widgets/graph_panel.py::_draw_branch_chips`.

### Collapse-политика
Каждая строка с несколькими ветками сворачивается в **один priority-чип** плюс индикатор `▼` (стрелка свёрнутой группы). Остальные ветки не теряются — они прячутся в cache и показываются в hover-popup (см. ниже). Правила:

- **1 ветка на коммите** — 1 чип, без свёртывания, без popup.
- **2+ веток** — 1 priority-чип + `▼`. Число `+N` (badge) добавляется, если скрыто больше одной ветки.

### Priority-ключ (`_branch_priority_key`)
Какой из нескольких чипов станет видимым — решает приоритет (меньше = выше):

| Бакет | Источник |
|-------|----------|
| `0` | текущая HEAD-ветка (`is_head=True`) |
| `1` | ветка, достижимая по first-parent от HEAD (source-branch для только что созданных) |
| `2` | ветка, созданная в текущей сессии (`_recently_created_branches`) |
| `3` | всё остальное (remote-only, unreachable, detached) |

При равенстве бакетов порядок лексикографический по имени — детерминировано между запусками. Недавно созданные ветки намеренно уходят в бакет `2`, чтобы только что сделанная ветка не «перебила» исходную — пользователь хочет остаться на той ветке, с которой создавал.

### Визуальная дифференциация local vs remote-only
Один и тот же display-name может быть как у локальной ветки (например, `main`), так и у её remote-трекера (`origin/main`). Чтобы рисовать одно название дважды, но в разных стилях:

- **Filled** (локальные + remote-с-локальным-дублёкатом) — rect залит цветом коммита, текст/иконки белые, монитор-иконка справа от имени.
- **Outlined** (remote-only, без локального дубликата) — обводка цветом коммита без заливки, текст/иконки тоже цвета коммита, без монитор-иконки. Контрастный wire-frame-look, явно отличается «удалённое».

Признак `is_remote_only` (есть remote-чип, но локального на этом коммите нет) считается в `_draw_branch_chips` и используется и для колонки чипов, и для hover-popup.

### Подавление дублей same-name remote и `origin/HEAD`
В репозитории после `fetch` всегда присутствует служебный `refs/remotes/origin/HEAD` (маркер дефолтной ветки удалённого) — он не несёт полезной информации, только мусорит в списке. Плюс `origin/main` дублирует локальный `main`. Helper `_suppress_dup_remotes(branch_refs, local_display_names=None)` (`src/ui/widgets/graph_panel.py`) применяется **в трёх местах**, чтобы пользователь никогда не видел дубли:

1. **`_draw_branch_chips`** — фильтрует `branch_refs` перед отрисовкой колонки.
2. **`_branches_at_row_visible`** — обёртка над `_branches_at_row`, используется hover-popup.
3. **`BranchPanelViewModel._suppress_same_name_remotes`** — фильтрует список в левой панели (`Branches → Remote`), плюс скрывает всю группу `Remote`, если после фильтра она пуста (`left_panel.py` ставит `setHidden(True)`).

Реализация — единая точка `_suppress_dup_remotes`, чтобы чип-колонка и popup никогда не разъезжались.

### Hover-popup `BranchStackPopup`
Когда пользователь наводит курсор на свёрнутый multi-branch чип, через 220 ms debounce (`_HOVER_POPUP_DELAY_MS`) всплывает frameless `Qt.Tool`-окно со списком всех веток на этом коммите. Каждая строка — кликабельный чип того же стиля, что и в основной колонке. Single- и double-click эмитят `branch_selected` (полное ref-имя) → `MainViewModel.checkout_branch` (через `CheckoutCommand` → undo/redo).

**Skip-when-single:** попап не показывается если после фильтрации дублей там осталось меньше двух веток. Сценарий: `main` (локальная) + `origin/main` (remote-дубликат) → чип-колонка рисует только `main` без `▼`-индикатора (так как `hidden_count == 0` после фильтрации). Hover в этом случае не показывает popup — там было бы одно имя, которое и так уже видно на чипе. Тот же guard в двух местах: в `_on_hover_popup_timer` (таймер debounce) и в `_show_branch_popup` (финальная проверка).

**Закрытие** — тройная страховка от «призрачных» popup, остающихся на экране:

1. `leaveEvent`/`mouseMoveEvent` на самом popup — debounced close-таймер 160 ms.
2. Глобальный `QApplication.installEventFilter(popup)` — ловит `MouseMove` за пределами popup-а и исходного чипа (на случай, если `leaveEvent` не сработал при перетаскивании окна или alt-tab).
3. `QEvent.ApplicationDeactivate` — закрытие при потере фокуса.

Дополнительно `eventFilter` на родителе ловит `QEvent.Move` родительского окна и перемещает popup на ту же дельту — чтобы при перетаскивании основного окна popup следовал за чипом, а не застывал на экране, где его открыли.

### Inline-редактор «Create Branch Here»
Правый клик по чипу открывает контекстное меню ветки. Пункт `Create Branch Here` (только для локальных чипов) поднимает inline `QLineEdit` ровно над чипом. Enter → `create_branch_here_requested(sha, name)` → `MainViewModel.create_branch(name, target_sha=sha)`. Escape или потеря фокуса → закрыть без действия. Команда `CreateBranchCommand` через `CommandProcessor` (undo = удаление только что созданной ветки).

### Drag-and-drop и submenu в LeftPanel
`LeftPanel` (древовидный список веток/тегов/stash в левой части окна) получил тот же UX, что и чипы графа: drag-and-drop с выпадающим меню merge/rebase и правый клик с подменю `Merge <name> into...` / `Rebase <name> onto...`. Реализация в `src/ui/widgets/left_panel.py`.

**Drag-and-drop.** Source — локальные и remote ветки (`ItemIsDragEnabled` на обеих группах, обновляется в `_update_drag_state`). Target — только локальные ветки (merge/rebase работают в working-tree). В кастомном MIME `application/x-git-py-branch-kind` payload несёт discriminator (`local_branch` / `remote_branch`), и drop-handler вызывает `_merge_drop` / `_rebase_drop`, которые для remote source сначала делают `fetch_and_checkout_remote_branch` (нормализация `origin/feature` → `feature`). Меню: `Merge <source> into <target>` / `Rebase <source> onto <target>` через `QMenu.exec(event.position().toPoint())` — тот же visual feedback, что и drop в графе.

**Submenu `Merge <name> into...` / `Rebase <name> onto...`.** Контекст-меню локальной ветки теперь содержит (после старого `Merge <name> into current…`):
- `Merge <name> into <T>` для каждого другого локального `<T>` (через `_merge_into_submenu(source, _KIND_LOCAL_BRANCH)`);
- `Rebase <name> onto <T>` для каждого другого локального `<T>` (аналогично с `rebase=True`).

Remote-ветка получает те же подменю, но клик сначала вызывает fetch+checkout (через `_merge_drop(_KIND_REMOTE_BRANCH)` → `fetch_and_checkout_remote_branch`).

Сценарий: пользователь имеет `main`, `feature`, и видит `origin/main` ушёл вперёд — правый клик `origin/main` → `Merge origin/main into...` → `feature` → `feature` синхронизируется с remote tip. Никаких лишних checkout'ов и диалогов, кроме уже существующего "Reset Local to Here?" если local branch уже есть.

**Что не сделано.** `Rebase <name>` (короткая форма — в текущую HEAD) сохранена как отдельный action рядом с submenu — пользователи, привыкшие к старому workflow (rebase в current), не должны его потерять. Удаление submenu считалось нарушением backwards compat.

### Drag-and-drop merge/rebase
Press-and-drag на чипе → `QDrag` с custom MIME `application/x-git-py-branch-chip`. Drop на другой чип → контекстное меню `Merge <source> into <target>` или `Rebase <source> onto <target>` → `MainViewModel.merge_branch(source, target, no_ff=True)` / `rebase_branch(target)` (через `MergeCommand`/`RebaseCommand`). Порог промоции press→drag = `drag_start_threshold_px` (по умолчанию ~5 px), чтобы короткие клики случайно не начинали перетаскивание. Эквивалентное контекстное меню также открывается по правому клику — пользователь, не догадавшийся про drag, может сделать всё то же через `Merge <X> into <Y>` / `Rebase <X> onto <Y>` напрямую. Один источник истины для меню: drop и right-click идут через `_build_branch_menu_actions`.

**Все UI-пути merge передают `no_ff=True`.** Это гарантирует, что merge-коммит появляется в графе независимо от того, насколько target ahead-of-source — обычный fast-forward (когда source — прямой потомок HEAD) молча переносит ref и в графе ничего не появляется. `no_ff=True` форсирует настоящий merge-коммит, который видно в истории. Программные вызовы `MainViewModel.merge_branch(...)` по умолчанию `no_ff=False` — git-совместимое поведение (fast-forward, когда возможен). Drag/drop и контекстное меню на графе и в `LeftPanel` передают `no_ff=True`. Под капотом: `MergeCommand(..., no_ff=False)` → `MergeCommand.execute()` зовёт `core/operations.merge_branch(..., no_ff=no_ff)` → `repository.merge_commits(workdir, [source, target], no_ff=no_ff)` (pygit2 ≥1.14 поддерживает флаг).

### Сброс локальной ветки к удалённой («Reset Local to Here?»)
Двойной клик на remote-tracking ветке в `LeftPanel` проверяет, существует ли локальная ветка с тем же именем. Если нет — выполняется `fetch_and_checkout_remote_branch(name)` (создаёт локальный tracking branch на свежем tip). Если да — `QMessageBox.question` с текстом:
```
Reset local 'main' to match the remote?

This will discard any unpushed commits on 'main'
(including the merge that is not yet on the remote).
Working-tree changes will also be lost.

Continue?
```
Кнопка `No` — default (защита от случайного Enter, который бы уничтожил работу). `Yes` маршрутизирует на `MainViewModel.reset_local_branch_to_remote(name)`:

1. Синхронный `FetchCommand(remote, branch)` — обновляет remote-tracking ref до актуального tip'а (нужно, чтобы не сбрасывать в stale значение).
2. Lookup `origin/<name>` через `repo_manager.branches`. Если remote ref не пришёл — `error_occurred` и выход без изменений.
3. Если локального ref'а нет — `CreateBranchCommand` + checkout (no-op destructive path).
4. Если локальный ref есть — `core_reset(repo_manager, target_sha, mode="hard")` (отбрасывает unpushed commits, индекс и worktree drift), затем checkout через `GIT_CHECKOUT_FORCE` (без force dirty check re-flag'ит файлы, которые reset только что привёл в порядок).

Метод не идёт через `CommandProcessor` — после `reset --hard` lost commits не вернуть через undo (reflog path тоже отрезан), UI gating на диалог компенсирует.

Эта же логика используется для right-click `Checkout <name> as local branch` в `LeftPanel._remote_branch_actions` и для `_on_graph_branch_checkout` в `MainWindow` — все три пути (double-click в дереве, right-click в дереве, double-click на чипе графа) показывают один и тот же диалог и идут в одну и ту же ветку VM. Это закрывает кейс «пользователь видит diverged `main` после merge → хочет откатить merge → кликает checkout на `origin/main` → раньше ничего не происходило».

## Подсказка про fetch в ошибках merge
`core.operations.merge_branch` и `complete_merge` детектят типичные pygit2 ошибки «unknown revision» / «source ... not found» и дописывают к `error_occurred` рекомендацию: `Run 'fetch <remote>' to update remote-tracking branches and retry.` Это чтобы при перетаскивании upstream-ветки, которая ещё не была fetched, пользователь видел сразу подсказку, а не абстрактный «source not found».

## Цвета bridge pipe и fork connector

Раскладка графа использует два разных правила выбора цвета для соединительных линий — это намеренно, и путать их нельзя.

**Fork connector (горизонталь от корневого коммита к форку/стэшу) — цвет merging lane.** Когда форк-точка (например, корневой коммит или merge-коммит) отправляет ветку/стэш в offset-lane, горизонтальный сегмент и TEE_RIGHT на стороне корня рисуются в цвете **lane-приёмника** (стейша или ветки, которая входит в форк-точку). Это даёт визуальную связь «форк-коннектор ↔ стэш-узел»: они одного цвета, как ветка, ответвившаяся от развилки. Вертикаль (`pipe_color_index` на TEE_RIGHT) остаётся в цвете корневого коммита. Реализовано в `_build_fork_connector_cells` (`src/core/graph_v2.py`).

**Bridge pipe (вертикаль между двумя соседними строками) — цвет предыдущей строки.** Линии, соединяющие ячейки в соседних строках на одном lane (например, от стэша вниз к корневому коммиту, или от HEAD вниз к WIP-узлу), наследуют цвет **ячейки предыдущей строки** на том же lane. Это нужно, чтобы цвет «пробегал» вниз по цепочке: WIP-grey → стэш-grey → корень-blue. Иначе линия стэш→корень была бы синей (от TEE_RIGHT корня), а не серой (от PIPE стэша). Реализовано в `_draw_cells` (`src/ui/widgets/graph_panel.py`) — bridge pipe ищет `prev_cells` в `self._rows[row_idx - 1]` и для tee-типов использует `prev_cells[].p`, для остальных — `prev_cells[].c`. Fallback на текущую строку только если предыдущей нет (верх графа) или ячейка пустая.

Эти два правила **не должны конфликтовать**: fork connector — про горизонтальную связь «корень ↔ форк», bridge pipe — про вертикальную связь «строка выше ↔ строка ниже». Если оба правила применить к одному сегменту (например, дать горизонтали в корневой строке цвет предыдущей строки), форк-коннектор «отвяжется» от своего стэша и начнёт читаться как ветка, ответвившаяся от WIP. Это и был исходный баг — первая попытка исправить bridge pipe сломала fork connector, и наоборот. Регрессионные тесты `test_stash_fork_connector_uses_merging_branch_colour` (UI) и `test_fork_connector_*` (core) фиксируют оба инварианта.

## Защита merge/branch-клеток от fork-overlay

Строка merge-коммита строится в два этапа: сначала `_build_row_cells` создаёт связи с родителями (`BRANCH_LEFT` / `BRANCH_RIGHT`, `MERGE_LEFT` / `MERGE_RIGHT`, `CROSS`), затем `build_graph` накладывает `fork_merging_cells`, чтобы показать создание дочерних веток от этого же коммита.

Fork-overlay не должен перетирать уже выставленные смысловые клетки `BRANCH_*`, `MERGE_*` и `CROSS`. Эти клетки несут направление вертикали: `BRANCH_*` ведёт вниз к родителю, `MERGE_*` ведёт вверх к merge-точке, `CROSS` сохраняет обе связи. Если заменить такую клетку на `HORIZONTAL`, линия создания веток визуально стирает merge/source-соединение.

Реальный кейс: `gpt-researcher` merge `6c75117` (`sudabg/fix/reference-error-1673` → `_render_target`). До overlay на lane source-ветки стоял корректный `BRANCH_LEFT c=11`; fork-коннектор от `_render_target` заменял его на `HORIZONTAL c=13`, и вертикаль вниз к source-коммиту пропадала. Теперь при наложении `fork_merging_cells` существующие `BRANCH_RIGHT`, `BRANCH_LEFT`, `MERGE_RIGHT`, `MERGE_LEFT` и `CROSS` имеют приоритет, а fork-коннектор заполняет только остальные клетки.

Визуальная проверка: `python simulate_problem.py` должен показывать на проблемной точке `BRANCH_LEFT c=11` и статус `downward branch is present`.

## CROSS-`direction`: закрытие зазора у fork-merge точки

`CROSS`-ячейка (cross-junction: горизонталь + вертикаль вверх + вертикаль вниз) рисуется в `_build_row_cells` в fork-merge кейсе — когда merge-коммит одновременно fork-точка (имеет 2+ детей), и один из его вторых родителей (`parent[1]`) лежит на lane, совпадающей с lane одного из детей. Это соответствует GitKraken-style рендерингу: один столбец несёт обе связи (merge снизу + child сверху), и `┼` делает их визуально различимыми.

**Проблема.** Раньше `CROSS` рисовал только вертикали, а горизонталь шла из соседней between-lanes ячейки (`HORIZONTAL` / `HORIZONTAL_PIPE`) на col `parent_lane * 2 + 1`. Её `x` = `col_left + lane_w / 2` (центр lane), а не `x` = `col_left` (центр коммита). Между вертикальной трубой `CROSS` (центр коммита) и началом горизонтали (центр lane) оставалось `lane_w / 2 ≈ 11 px` пустоты. На merge-коммитах с дальним вторым родителем (например, `gpt-researcher` `693d3b72 ← b364917f`, lane 14 → lane 0) это выглядело как «обрыв» горизонтали в воздухе.

**Решение.** `CellInfo` получил поле `direction: int = 0` (только для `CROSS`):
- `+1` — провести горизонталь вправо от центра CROSS-ячейки на ширину `lane_w`;
- `-1` — то же влево;
- `0` — без дополнительной горизонтали (default, backwards-compatible).

В `_build_row_cells` направление выбирается автоматически: `direction = -1 if parent_lane > commit_lane else 1` (горизонталь тянется в сторону merge-коммита, чтобы закрыть зазор между commit-вертикалью и between-lanes-горизонталью). В `_draw_cell_row` (`src/ui/widgets/graph_panel.py`) добавлен вызов `_draw_horiz_line(... lane_w * direction ...)` при `cell["d"] != 0`.

Глобальный `_draw_horiz_line` остался без изменений — расширение применимо только к `CROSS`, что не задевает `HORIZONTAL` / `HORIZONTAL_PIPE` в других контекстах (соседние lanes, multi-merge fork connector).

Полное описание: `docs/MERGE_LANE_FIX.md`. Регрессионные тесты: `test_cross_cell_carries_horizontal_direction`, `test_cross_cell_direction_default_is_zero`, `test_cross_cell_to_dict_omits_direction_when_zero` в `tests/core/test_graph_v2.py`.

Визуальная проверка: `python simulate_problem.py` рендерит реальный `gpt-researcher` через те же `QPainter`-примитивы что и `graph_panel.py`. До фикса — красная рамка «empty gap (11 px)» на col 0 строки merge. После — зелёная галочка «bend bridged», горизонталь дотягивается до вертикали.

## Матрица приоритетов цветов на строке merge+fork коммита (update2)

Когда коммит одновременно merge (2+ родителя) и fork-точка (2+ детей), на его строке две системы коннекторов делят одну горизонтальную колею: коннектор родителей (`_build_row_cells`) и fork-коннектор к детям (`_build_fork_connector_cells` + overlay). Правила приоритета (зафиксированы регрессионными тестами `tests/core/test_graph_v2.py`, префикс `test_*cross*`, `test_merge_left_bend_*`, `test_fork_connector_pipe_*`):

1. **Вертикаль под коммитом — собственный цвет коммита** (`final_color_index`). Fork-коннектор строится до вычисления цвета коммита, поэтому его `TEE_RIGHT.pipe_color_index` постфиксируется в overlay; иначе полклетки под узлом окрашивались в цвет дочерней ветки (kilocode `22149292`).
2. **Горизонталь от коммита до `CROSS` — цвет merge (второго родителя).** Overlay не перезаписывает ячейки коннектора родителей между коммитом и `CROSS` (`merge_own_cols`); цвет дочерней ветки виден только в вертикали `CROSS` вверх (`pipe_color_index`) и в сегментах за `CROSS` (kilocode `9c0e4f76`).
3. **Полклетки за изгибом принадлежат следующему сегменту.** Из-за чёт/нечётной геометрии каждая горизонтальная ячейка закрашивает полклетки соседнего спана. Пост-проход в overlay: слева от `CROSS(d=-1)` ячейка перекрашивается в цвет следующего fork-сегмента (sql-skill `8ee78fc`); слева от `MERGE_LEFT` чужая горизонталь удаляется (`HORIZONTAL`→`EMPTY`, `HORIZONTAL_PIPE`→`PIPE`), чтобы линия не уходила в пустоту (sql-skill `460f62c`).
4. **Дыры между `CROSS` и следующим fork-изгибом заполняются.** Fork-коннектор останавливается на ячейку раньше правого изгиба в расчёте на промежуточный `HORIZONTAL_PIPE`; когда предыдущий изгиб — `CROSS`, эту ячейку никто не рисовал (kilocode `9c0e4f76`, col 11). Последняя заполненная ячейка перед изгибом помечается `direction=-1` (**right-trimmed horizontal**): рендер рисует только левую половину спана, иначе горизонталь торчала бы на полклетки за изгиб в пустоту (kilocode `5c7978c2`, col 11).
5. Прочие коллизии — по базовой матрице защиты overlay: `BRANCH_*`, `MERGE_*`, `CROSS` не перезаписываются (см. «Защита merge/branch-клеток от fork-overlay»); вертикали активных lane сохраняются через `HORIZONTAL_PIPE.pipe_color_index`.

Отладочный инструмент: `python tools/dump_graph_cells.py <repo> <sha>...` печатает ячейки строк вокруг заданных коммитов реального репозитория (включая синтетические stash-узлы, как в `GraphViewModel`).

## Контекстное меню на вкладках репозиториев

Правый клик по табу репозитория (`QTabBar.customContextMenuRequested`) открывает меню из пяти пунктов:

```
Show repo folder          → MainVM.show_repo_in_folder(path)
Copy repo path            → MainVM.copy_repo_path(path)        ── separator ──
Close repo tab            → RepoTabViewModel.remove_tab(index)
Close other tabs          → RepoTabViewModel.close_others(index)
Close tabs to the right   → RepoTabViewModel.close_to_right(index)
```

Меню строится через отдельный builder `_build_tab_context_menu_actions(index, path, tab_count)` — симметричный паттерн с `_build_branch_menu_actions` в `graph_panel.py` и `_context_menu_actions` в `LeftPanel`. Это позволяет тестам инспектировать список действий синхронно (без блокирующего `QMenu.exec`).

**Disabled-state правила** (как в остальных меню приложения):

- `Close other tabs` — серый, если `tab_count == 1`. Действие бессмысленно при единственном табе.
- `Close tabs to the right` — серый, если кликнутый таб уже самый правый (`index == tab_count - 1`). Действие ничего не изменит.
- `Close repo tab`, `Show repo folder`, `Copy repo path` — всегда enabled. Они работают на пути кликнутого таба, не на состоянии списка.

**Защитные guards** (от багов, а не от пользователя):

- Right click вне табов (`tabAt == -1`) → no-op. Иначе меню попыталось бы построить actions с мусорным `index`.
- `tabData(index) == ""` → no-op. Race window между `_rebuild_tabs` и `setTabData` не должен приводить к эмиссии пустого пути (который бы silently сбросил clipboard или попытался открыть `explorer ""`).
- Все три "path"-related защиты на пустые payload-ы в `MainWindow._on_show_repo_folder` / `_on_copy_repo_path` — повторяют тот же паттерн, что в `_on_copy_branch_name` / `_on_copy_commit_sha`.

**Где живёт логика:**

- `RepoTabViewModel.close_others(index)` / `close_to_right(index)` — новые verb-методы рядом с `remove_tab`. Оба no-op при out-of-range и при «no tabs to act on»; оба эмитят `tabs_changed` + `active_tab_changed`.
- `MainViewModel.show_repo_in_folder(path)` — открывает Explorer на нормализованном пути. `os.path.normpath` для mixed separators, `os.path.isdir` для защиты от stale tab path, swallow `subprocess.Popen` failures (Windows Explorer crash не actionable). Это repository-уровневая (а не file-уровневая, как существующая `show_in_folder` для staged-файлов) версия.
- `MainViewModel.copy_repo_path(path)` — делегирует в существующий `copy_to_clipboard`; защита от пустого пути остаётся на стороне VM (как у `copy_to_clipboard`).

## Цвет ветки: устойчивость при merge в основной граф

После того как коммит с именем ветки (branch_name) уже «предзарегистрирован» на lane (через parent processing более нового merge-коммита), его собственный цвет должен браться из `_pick_branch_color(branch_name)`, а не из lane-cache, который заполнил merge коммит.

**Проблема.** В `build_graph` цикл обработки коммитов начинается со старшинства по timestamp (`history` отсортирован newest first). Если ветка `3mk4yl/fix-dict-unhashable-bug` уже влита в main через merge `31b22352`, merge обрабатывается **раньше** tip-коммита ветки `987c9e8`. При обработке `31b22352`:
- `lanes[merge.lane] = '987c9e8'` (parent[0] ставится на lane merge коммита)
- `oid_color['987c9e8'] = merge.color` (pre-coloring для ещё не обработанного parent)

Когда `987c9e8` наконец обрабатывается, его SHA уже отслеживается на lane (`commit_lane_opt is not None`), и старая логика брала цвет из `lane_colors[lane]` — то есть цвет merge коммита, fallback idx=0 (GREEN), вместо собственного цвета ветки `_pick_branch_color("3mk4yl/fix-dict-unhashable-bug") = 15` (GOLD).

Воспроизведение: `python tools/reproduce_31b22352_bug.py` (оригинальный repro скрипт). До фикса на `987c9e8` (tip side-branch) рендерился `PIPE c=6` (PINK) на строках выше, и вся линия ветки выглядела в цвете merge коммита.

**Решение (Изменение A в `src/core/graph_v2.py:566-582`).** В `commit_lane_opt is not None` ветке:

```python
if commit_lane_opt is not None:
    if primary_branch is not None:
        # Side-branch tip: use own branch colour, not the
        # pre-coloured lane-cache value the merge commit set.
        commit_color_index = color_assigner.assign_color(
            lane, primary_branch
        )
    else:
        commit_color_index = color_assigner.continue_lane(lane)
```

`primary_branch` — это первый branch name в `oid_to_branches[commit.sha]`. Если коммит помечен какой-то веткой — `assign_color(lane, primary_branch)` использует `_pick_branch_color(primary_branch)` (детерминированный crc32 % len(palette)). Иначе — fallback `continue_lane(lane)` (старое поведение для orphan коммитов).

**Второй источник проблемы — pre-coloring fork siblings.** Старая логика parent lanes setup писала `lane_color_index[new_lane] = new_color` безусловно. Когда `new_color` — это fallback от `_pick_fallback(lane)` (потому что parent не имеет `primary_branch`), это отравляло lane cache. Все последующие коммиты, попадавшие на этот lane через `continue_lane()`, получали fallback цвет.

**Решение (Изменение B в `src/core/graph_v2.py:664-684`).** `lane_color_index[new_lane] = new_color` для fork siblings теперь записывается **только** если у parent есть `primary_branch`. Для orphan parents предыдущее значение `lane_colors[lane]` сохраняется, и mainline коммиты больше не перекрашиваются в случайный fallback-цвет.

```python
if parent_branch is not None:
    lane_color_index[new_lane] = new_color
# else: leave the existing lane_colors[new_lane] in place
```

**Третий источник — `fork_sibling_color` для single-parent коммитов.** Раньше в `if existing_parent_lane is not None and parent_idx == 0 and parent_sha in fork_points:` блоке `commit_color_index` перезаписывался на `main_color` для **любого** коммита, чей parent — fork point. Это давало правильный результат для merge коммитов (merge в mainline), но ломало single-parent коммиты (например, side-branch tip `987c9e8` рисовался в BLUE/master поверх собственного GOLD).

**Решение (Изменение C в `src/core/graph_v2.py:623-654`).** `fork_sibling_color` присваивается **только** для merge коммитов (`len(valid_parents) >= 2`). Single-parent коммиты с parent на fork point сохраняют оригинальную логику (`parent_lane = lane`, `was_existing = False`), но **без** перезаписи `lane_color_index[lane]` на `main_color`.

```python
if (parent_idx == 0
    and parent_sha in fork_points
    and len(valid_parents) >= 2):
    # ... merge commit: set fork_sibling_color
elif parent_idx == 0 and parent_sha in fork_points:
    # Single-parent commit: legacy behaviour but no lane_colour overwrite
    lanes[lane] = parent_sha
    parent_lane = lane
    was_existing = False
    parent_color = commit_color_index
else:
    # Standard existing-parent path
```

**Результат на реальных данных.** До фикса: `987c9e8` рендерился с col 46 = `PIPE c=6` (PINK) на строках 104-109; mainline (lane 0) на строках 84-94 имел `TEE_RIGHT c=N p=6` (PINK pipes, отравленные fallback от merge `parent[1]`). После: `987c9e8` рендерится с col 46 = `PIPE c=15` (GOLD), mainline больше не в PINK.

**Тесты.** `tests/core/test_graph_v2.py`:
- `test_branch_tip_keeps_own_colour_when_merge_processed_first` — tip side-branch получает `_pick_branch_color(name)`, не цвет merge.
- `test_fork_sibling_does_not_overwrite_mainline_lane_colour` — mainline tip сохраняет master BLUE через fork-sibling pre-coloring.

## Расширенная палитра `BRANCH_PALETTE` (40 цветов)

После фикса выше коллизии crc32 между разными ветками стали заметнее: до фикса коммиты одной ветки могли получать **разные** цвета через `lane_colors[lane]` cache, после фикса — стабильный цвет через `_pick_branch_color(primary_branch)`. В репозитории с 60+ веток на 24-цветной палитре это означало в среднем 3.0 ветки на индекс, и заметные визуальные коллизии (например, две разные ветки рисуются одинаковым цветом).

**Решение.** `BRANCH_PALETTE` расширена с 24 до 40 цветов в `src/core/graph_v2.py:39-79`. Новые 16 индексов (24..39) — дополнительные оттенки (sea, coral, bronze, indigo, sky, sand, burgundy, peach, khaki, jade, fuchsia, chestnut, cerulean, wisteria, sandalwood, moss), hex-коды выбраны для контраста на тёмном фоне `DARK_THEME.bg = #1E1E1E`. Все цвета medium-saturated, видимы и различимы на тёмном background.

**`UNCOMMITTED_COLOR_INDEX` перенесён** с 24 на 40. Это специальный idx за пределами палитры — `crc32(name) % 40` не может дать 40, поэтому WIP маркер невозможно спутать с обычным цветом ветки. Защитный тест `test_uncommitted_color_index_is_outside_palette` гарантирует это инвариант при будущих изменениях палитры.

**Код** — никаких дополнительных правок не нужно:
- `_pick_branch_color` использует `crc32(name) % len(BRANCH_PALETTE)` (line 94)
- `_pick_fallback` использует `len(BRANCH_PALETTE)` (lines 361-366)
- `_cell_color` в `graph_panel.py:194-200` уже обрабатывает `UNCOMMITTED_COLOR_INDEX` отдельно от диапазона палитры
- `palette_map` строится через `enumerate(BRANCH_PALETTE)` и добавляет `palette_map[UNCOMMITTED_COLOR_INDEX] = self._cfg.wip_color`

**Результат для `gpt-researcher`** (66 веток):

| Метрика | 24 цвета | 40 цветов |
|---------|----------|-----------|
| Distinct indices used | 22 | 33 |
| Avg branches per index | 3.0 | **2.0** |
| Свободные индексы для будущих веток | 2 | 7 |

В типичных репозиториях с <40 веток коллизий обычно нет вовсе; для крупных монорепозиториев средняя плотность снизилась с 3 до 2 веток на индекс. `ruff check` чисто, 205/205 core тестов проходят.

## Core-ограничения

- Модуль `core/graph_v2.py` не импортирует PySide6.
- Все мутирующие операции над репозиторием (commit, merge, rebase, checkout и т.д.) — наследники `GitCommand`, проходят через `CommandProcessor`.
- Ошибки `pygit2` оборачиваются в доменные исключения (`GitError`, `MergeConflictError`, `AuthError`).
