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

## Core-ограничения

- Модуль `core/graph_v2.py` не импортирует PySide6.
- Все мутирующие операции над репозиторием (commit, merge, rebase, checkout и т.д.) — наследники `GitCommand`, проходят через `CommandProcessor`.
- Ошибки `pygit2` оборачиваются в доменные исключения (`GitError`, `MergeConflictError`, `AuthError`).
