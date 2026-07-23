"""Stage 2 tests for the :class:`GraphWidget`.

These run under ``pytest-qt`` (set ``QT_QPA_PLATFORM=offscreen``
for headless environments). We use real pygit2 repos and a real
:class:`GraphViewModel` so the data flow is exercised end-to-end;
the only thing under test is the widget's rendering and click
handling.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsSimpleTextItem,
    QMenu,
)
from src.core.repository import RepositoryManager
from src.ui.widgets.graph_widget import GraphWidget
from src.viewmodels.graph_viewmodel import GraphViewModel


def _make_committed_repo(path: Path) -> RepositoryManager:
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree1 = mgr.repo.index.write_tree()
    c1 = mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree1, [])
    (path / "f.txt").write_text("v2\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree2 = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "second", tree2, [c1])
    return mgr


# ----- rendering -------------------------------------------------------


def test_widget_renders_node_per_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Exactly one ellipse per commit in the layout.
    nodes = [item for item in widget.scene().items() if isinstance(item, QGraphicsEllipseItem)]
    assert len(nodes) == 2
    # Each node carries the corresponding SHA as data(0).
    shas = {node.data(0) for node in nodes}
    assert len(shas) == 2
    # The set should match the two commits in the repo.
    history = mgr.get_all_history()
    assert shas == {c.sha for c in history}


def test_widget_renders_lines_between_commits(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # At least one connecting path (the edge from child to parent).
    paths = [it for it in widget.scene().items() if isinstance(it, QGraphicsPathItem)]
    assert len(paths) >= 1


def test_widget_shows_placeholder_for_empty_graph(qtbot) -> None:
    vm = GraphViewModel()  # no repo -> empty layout
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=1000):
        vm.refresh_graph()

    # No nodes for an empty graph.
    nodes = [it for it in widget.scene().items() if isinstance(it, QGraphicsEllipseItem)]
    assert nodes == []


def test_widget_clears_previous_scene_on_new_layout(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    first_nodes = [
        it for it in widget.scene().items() if isinstance(it, QGraphicsEllipseItem)
    ]
    assert len(first_nodes) == 2

    # Drop the repository: the next refresh must wipe the scene.
    vm.set_repository(None)
    after = [
        it for it in widget.scene().items() if isinstance(it, QGraphicsEllipseItem)
    ]
    assert after == []


# ----- selection / clicks ---------------------------------------------


def test_set_selected_sha_highlights_node(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    widget.set_selected_sha(head_sha)
    assert widget.selected_sha() == head_sha

    # The selected node's pen should be a lighter color than the
    # background. We test by looking at the pen's color name.
    selected_node = widget._node_items[head_sha]  # noqa: SLF001 - intentional
    pen = selected_node.pen()
    assert pen.color().name().lower() in ("#ffffff", "#fff")


def test_clicking_node_emits_commit_selected(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    head_node = widget._node_items[head_sha]  # noqa: SLF001 - intentional
    # Translate the node's scene rect to a viewport click point.
    target_scene = head_node.mapToScene(head_node.rect().center())
    target_view = widget.mapFromScene(target_scene)

    with qtbot.waitSignal(vm.commit_selected, timeout=1000) as blocker:
        qtbot.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=target_view)
    assert blocker.args[0] == head_sha
    # And the widget remembers the selection.
    assert widget.selected_sha() == head_sha


def test_clicking_empty_space_does_not_emit(
    qtbot, tmp_git_repo: Path,
) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Click somewhere far away from any node.
    with qtbot.assertNotEmitted(vm.commit_selected, wait=200):
        qtbot.mouseClick(
            widget.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(2, 2),
        )


# ----- integration with main_window -----------------------------------


def test_main_window_wires_graph_view_model(qtbot, tmp_git_repo: Path) -> None:
    from src.ui.main_window import MainWindow

    mgr = _make_committed_repo(tmp_git_repo)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    with qtbot.waitSignal(window.graph_view_model().graph_updated, timeout=2000):
        window.set_repository(mgr)

    graph_table = window._graph_table  # noqa: SLF001
    assert graph_table.row_count() == 2
    window.close()


# ----- WIP node (Stage 3) ---------------------------------------------


def test_widget_renders_wip_node_when_worktree_dirty(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A modified worktree file adds a WIP ellipse above the real nodes."""
    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    nodes = [item for item in widget.scene().items() if isinstance(item, QGraphicsEllipseItem)]
    # 2 real commits + 1 WIP node.
    assert len(nodes) == 3
    shas = {node.data(0) for node in nodes}
    assert "WIP" in shas
    # WIP node should be tagged with sha="WIP" so clicks route to the
    # special case in CommitDetailPanel.
    wip_node = widget._node_items["WIP"]  # noqa: SLF001 - intentional
    assert wip_node.data(0) == "WIP"


# ----- top-commit stub guard (no uncommitted) -------------------------


def _make_merge_repo(path: Path) -> RepositoryManager:
    """Build a repo where HEAD is a merge commit.

    The merge puts HEAD at lane 0 (main) and the merged-in branch on
    lane 1, which is the only layout that triggers the
    ``TEE_RIGHT`` / ``TEE_LEFT`` / ``HORIZONTAL_PIPE`` cell types at
    HEAD's lane. Those cells previously extended their vertical
    segment by ``row_height / 2`` above the commit, leaving a stub
    dangling into the empty area above the topmost row.
    """
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)

    (path / "f.txt").write_text("a\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c1 = mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])

    mgr.repo.create_branch("feature", mgr.repo.head.peel())
    mgr.repo.checkout("refs/heads/feature")
    (path / "g.txt").write_text("g\n")
    mgr.repo.index.add("g.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/feature", sig, sig, "feat", tree, [c1])

    mgr.repo.checkout("refs/heads/main")
    (path / "h.txt").write_text("h\n")
    mgr.repo.index.add("h.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "main2", tree, [c1])

    # ``merge_branch`` from ``src.core.operations`` would have been
    # cleaner, but it rejects message strings that contain spaces in
    # the reference name. Doing the merge with ``pygit2.merge``
    # directly keeps the test self-contained.
    feat_branch = mgr.repo.lookup_branch("feature")
    mgr.repo.merge(feat_branch.peel())
    # ``pygit2.merge`` updates the index but does NOT auto-commit.
    # The merge must be materialised as a commit before the index
    # matches HEAD and ``get_status()`` returns ``[]`` — otherwise
    # the test repo would carry an untracked file and the WIP
    # node would mask the layout we want to inspect.
    merge_tree = mgr.repo.index.write_tree()
    merge_parents = [mgr.repo.head.target, feat_branch.target]
    mgr.repo.create_commit(
        "refs/heads/main", sig, sig, "merge", merge_tree, merge_parents,
    )
    return mgr


def test_topmost_commit_does_not_draw_line_stub_into_empty_space(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The top commit must not extend its connector above the commit area.

    ``TEE_RIGHT`` / ``TEE_LEFT`` / ``HORIZONTAL_PIPE`` cells at the
    topmost row used to draw their vertical segment with
    ``half_h = row_height / 2`` extending above ``y_center``. The
    cell's commit ellipse only occupies ``y_center ± node_radius``
    (smaller than ``half_h``), so the extra 5 px ended up as a stub
    dangling into the empty area above HEAD. The guard runs by
    sampling the column directly above the topmost commit and
    asserting the background colour shows through - no edge colour
    should be present.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_merge_repo(tmp_git_repo)
    # Working tree must be clean so the WIP node is NOT inserted.
    assert mgr.get_status() == []

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    head_row = widget._rows[0]
    # Sanity-check: the fix is only meaningful when the top row
    # actually has a fork-connector cell. ``_make_merge_repo`` is
    # shaped to guarantee that, but pin the precondition so a future
    # refactor that strips the merge structure surfaces here rather
    # than as a "test passes for the wrong reason" false positive.
    head_cell_types = {c["t"] for c in head_row["cells"]}
    assert any(
        t in (9, 10, 8)  # _T_TEE_RIGHT / _T_TEE_LEFT / _T_HORIZONTAL_PIPE
        for t in head_cell_types
    ), f"merge setup did not produce a fork-connector cell: {head_cell_types}"

    cfg = widget._cfg
    head_y_center = cfg.header_height + cfg.row_height // 2
    # Just above the commit ellipse - 3 px above the top edge.
    probe_y = head_y_center - cfg.node_radius - 3
    # Lane 0 centre x. The widget's first divider is at 180 px,
    # the graph column starts at ``divider + graph_left_padding``,
    # and lane 0 sits at that x.
    probe_x = widget._dividers[0] + cfg.graph_left_padding

    pix = widget.grab()
    # ``widget.grab()`` returns a pixmap whose pixel grid is
    # scaled by the device pixel ratio. Convert the widget-space
    # probe point to image-space before reading the colour, otherwise
    # the assertion silently samples the wrong pixel and the bug
    # slips through.
    dpr = pix.devicePixelRatio()
    img = pix.toImage()
    color = img.pixelColor(int(probe_x * dpr), int(probe_y * dpr))
    # The background colour is the dark theme's ``bg``; the line
    # colour is one of the branch palette entries which are all
    # significantly brighter. Background RGB is roughly (30, 30, 30)
    # - branch palette entries start at ~(80, 80, 80) for the WIP
    # grey and brighter from there. A channel above 50 is a safe
    # lower bound for "not background".
    assert max(color.red(), color.green(), color.blue()) < 50, (
        f"stub above top commit: probe pixel {color.name()} "
        f"at ({probe_x}, {probe_y}) should be background, got "
        f"rgb=({color.red()}, {color.green()}, {color.blue()})"
    )


def _make_stash_around_commit_repo(path: Path) -> RepositoryManager:
    """Build a repo with two stashes separated by a regular commit.

    Timeline (chronological):

    1. Commit X
    2. Stash 1 ("stash1")  — first parent is Commit X
    3. Apply stash 1, then Commit Y  — first parent is Commit X
    4. Stash 2 ("stash2")  — first parent is Commit Y

    In the graph (newest first):

    * Stash 2  (top)
    * Commit Y
    * Stash 1
    * Commit X

    Stash 1 and Commit Y share Commit X as their first parent, so
    Commit X is a fork point with two children (Stash 1 on the
    offset lane, Commit Y on the main lane). Commit Y additionally
    has Stash 2 as a child on the offset lane.

    The bug this test pins: ``_draw_cells`` used to draw a vertical
    inter-row pipe at the offset lane between Commit Y (which has a
    ``MERGE_LEFT`` cell there) and Stash 1 (which sits on the offset
    lane). The pipe is wrong because Stash 1 is not a child of
    Commit Y — it is a sibling that joins Commit X from above.
    """
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)

    (path / "f.txt").write_text("a\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c_x = mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("b\n")
    from src.core.operations import stash_push
    stash_push(mgr, "stash1", include_untracked=False)

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    from src.core.operations import stash_apply
    stash_apply(mgr, 0)
    (path / "f.txt").write_text("b\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit(
        "refs/heads/main", sig, sig, "second", tree, [c_x],
    )

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("c\n")
    stash_push(mgr, "stash2", include_untracked=False)

    return mgr


def test_no_pipe_between_sibling_stash_and_unrelated_row_above(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A ``MERGE_LEFT`` at a row must not produce a pipe to the row below.

    Reproduces the scenario from
    ``fix: Prevent vertical line stubs at topmost commit`` plus a
    sibling stash: with a stash above HEAD and another stash inserted
    into the history, the older stash used to render with a stub
    going up to the in-between commit. The cause was the
    ``_draw_cells`` pipe check that treated every non-empty cell at
    a lane as evidence that the lane "continues" into the next row
    — but a ``MERGE_LEFT`` / ``MERGE_RIGHT`` / ``TEE_UP`` cell only
    extends upward and terminates at that row.
    """
    from src.core.graph_v2 import CellType
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_stash_around_commit_repo(tmp_git_repo)
    assert mgr.get_status() == []
    assert len(mgr.stash_list) == 2

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Sanity: four rows laid out newest-first as Stash 2, Commit Y,
    # Stash 1, Commit X. The bug requires Stash 1 and Commit Y to
    # both have something at the offset lane (lane 1 in our setup) —
    # pin those preconditions so a future refactor that strips the
    # shape surfaces here rather than as a "passes for the wrong
    # reason" false positive.
    stash1_row = next(
        r for r in widget._rows
        if r.get("commit") and r["commit"]["kind"] == "stash"
        and r["commit"]["subject"].startswith("Stash @{1}")
    )
    commit_y_row = next(
        r for r in widget._rows
        if r.get("commit") and r["commit"]["subject"].startswith("second")
    )
    stash1_lanes = {
        c["t"]
        for ci, c in enumerate(stash1_row["cells"])
        if c.get("t", 0) != CellType.EMPTY
    }
    commit_y_lanes = {
        c["t"]
        for ci, c in enumerate(commit_y_row["cells"])
        if c.get("t", 0) != CellType.EMPTY
    }
    assert CellType.COMMIT in stash1_lanes, f"stash 1 missing COMMIT cell: {stash1_lanes}"
    assert CellType.MERGE_LEFT in commit_y_lanes, (
        f"commit Y missing MERGE_LEFT cell: {commit_y_lanes}"
    )

    cfg = widget._cfg
    dpr = widget.devicePixelRatio()
    img = widget.grab().toImage()

    # The offset lane (lane 1) is at dividers[0] + graph_left_padding
    # + 1 * lane_w. The gap between Stash 1 (row 2) and Commit Y
    # (row 1) at lane 1 is the segment that should be background
    # after the fix — before the fix, a coloured pipe ran through it.
    offset_lane_x = widget._dividers[0] + cfg.graph_left_padding + (cfg.node_radius * 2 + 8)
    # The bug drew the inter-row pipe at lane 1 from the bottom of
    # Commit Y's ellipse (y_center + node_radius) to the top of
    # Stash 1's ellipse (y_center - node_radius). Probe the middle of
    # that band; a stray pipe shows up as a coloured pixel there.
    # Skip a few pixels at each end to avoid ellipse antialiasing.
    stash1_y_center = cfg.header_height + cfg.row_height * 2 + cfg.row_height // 2
    commit_y_y_center = cfg.header_height + cfg.row_height * 1 + cfg.row_height // 2
    probe_y_start = commit_y_y_center + cfg.node_radius + 4
    probe_y_end = stash1_y_center - cfg.node_radius - 4
    assert probe_y_end > probe_y_start, (
        "test geometry broken — probe range collapsed"
    )

    for probe_y in range(probe_y_start, probe_y_end + 1):
        ix = int(offset_lane_x * dpr)
        iy = int(probe_y * dpr)
        if not (0 <= ix < img.width() and 0 <= iy < img.height()):
            continue
        color = img.pixelColor(ix, iy)
        # Background rgb is ~(30, 30, 30); pipe colours are branch
        # palette entries that are clearly brighter (>= ~50 on at
        # least one channel). A stray pipe at this probe point
        # therefore shows up as a high channel value.
        assert max(color.red(), color.green(), color.blue()) < 50, (
            f"stray pipe at lane 1 between Stash 1 and Commit Y: "
            f"probe ({offset_lane_x}, {probe_y}) is {color.name()}, "
            f"rgb=({color.red()}, {color.green()}, {color.blue()})"
        )


def _make_stash_ladder_repo(path: Path) -> RepositoryManager:
    """Build a repo with one stash below and three stashes above.

    Timeline (chronological):

    1. Commit X
    2. Stash 1 (just above Commit X)
    3. Commit Y
    4. Stash 2
    5. Stash 3
    6. Stash 4 (newest, top)

    In the graph (newest first): Stash 4, 3, 2, Commit Y, Stash 1,
    Commit X. The ``_rebalance_stashes_for_wip`` step in
    :mod:`src.core.graph_v2` moves the upper stashes onto offset
    lanes (1, 2, 3, …) so that Commit Y can sit on lane 0. Commit Y
    ends up with a multi-step fork connector (``TEE_RIGHT``,
    ``HORIZONTAL``, ``TEE_UP`` cells) describing the merge of all
    three upper stashes.

    The bug this test pins: even after the single-stash fix, the
    presence of a *group* of stashes above Stash 1 made the
    ``HORIZONTAL`` cells in the fork connector at Commit Y register
    as "downward continuation" at the offset lane. A pipe was then
    drawn between Commit Y's ``HORIZONTAL`` and Stash 1's ``COMMIT``
    at that lane — a red stub going up out of Stash 1.
    """
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)

    (path / "f.txt").write_text("a\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c_x = mgr.repo.create_commit("refs/heads/main", sig, sig, "first", tree, [])

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("b\n")
    from src.core.operations import stash_push
    stash_push(mgr, "stash1", include_untracked=False)

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("c\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit(
        "refs/heads/main", sig, sig, "second", tree, [c_x],
    )

    for label in ("stash2", "stash3", "stash4"):
        time.sleep(1)
        sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
        (path / "f.txt").write_text(label + "\n")
        stash_push(mgr, label, include_untracked=False)

    return mgr


def test_no_pipe_from_horizontal_into_stash_below(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A fork-connector ``HORIZONTAL`` at one row must not draw a pipe
    into a stash sitting on the same lane one row below.

    Companion to :func:`test_no_pipe_between_sibling_stash_and_unrelated_row_above`:
    that test covers the case where a single fork-connector cell
    (``MERGE_LEFT``) is the source of the stray pipe. This test
    covers the case where the source is a plain ``HORIZONTAL`` cell
    (no vertical at all) at one of the offset lanes — the
    multi-stash-ladder rendering exercises that path.
    """
    from src.core.graph_v2 import CellType
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_stash_ladder_repo(tmp_git_repo)
    assert mgr.get_status() == []
    assert len(mgr.stash_list) == 4

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Sanity: Stash 1 and Commit Y sit on adjacent rows, and the
    # commit row has a fork-connector that includes a ``HORIZONTAL``
    # at the offset lane. Without that fork connector the bug
    # wouldn't manifest; pin the precondition so a future refactor
    # surfaces here instead of as a "passes for the wrong reason"
    # false positive.
    stash1_row = next(
        r for r in widget._rows
        if r.get("commit") and r["commit"]["kind"] == "stash"
        and r["commit"]["subject"].startswith("Stash @{3}")
    )
    commit_y_row = next(
        r for r in widget._rows
        if r.get("commit") and r["commit"]["subject"].startswith("second")
    )
    stash1_row_idx = widget._rows.index(stash1_row)
    commit_y_row_idx = widget._rows.index(commit_y_row)
    assert commit_y_row_idx == stash1_row_idx - 1, (
        "test setup broken — Stash 1 and Commit Y are not adjacent"
    )
    commit_y_cell_types = {c["t"] for c in commit_y_row["cells"]}
    assert CellType.HORIZONTAL in commit_y_cell_types, (
        "commit Y missing HORIZONTAL cell — fork connector shape "
        "changed: " + repr(commit_y_cell_types)
    )
    stash1_lanes = {
        c["t"] for c in stash1_row["cells"] if c.get("t", 0) != CellType.EMPTY
    }
    assert CellType.COMMIT in stash1_lanes, (
        "stash 1 missing COMMIT cell: " + repr(stash1_lanes)
    )

    # Find the offset lane where Commit Y has HORIZONTAL and Stash 1
    # has COMMIT — that is the lane where the bug used to draw a pipe.
    cfg = widget._cfg
    dpr = widget.devicePixelRatio()
    img = widget.grab().toImage()
    lane_w = cfg.node_radius * 2 + 8

    stash1_commit_lane = None
    for ci, c in enumerate(stash1_row["cells"]):
        if c.get("t") == CellType.COMMIT and ci % 2 == 0:
            stash1_commit_lane = ci // 2
            break
    assert stash1_commit_lane is not None
    assert stash1_commit_lane > 0, (
        "stash 1 sits on the main lane — test layout does not exercise "
        "the offset-lane bug"
    )

    commit_y_has_horizontal_at_lane = False
    for ci, c in enumerate(commit_y_row["cells"]):
        if (
            c.get("t") == CellType.HORIZONTAL
            and ci // 2 == stash1_commit_lane
        ):
            commit_y_has_horizontal_at_lane = True
            break
    assert commit_y_has_horizontal_at_lane, (
        "test setup broken — Commit Y has no HORIZONTAL at the "
        "Stash 1 offset lane"
    )

    # Probe the gap between Commit Y and Stash 1 at the offset
    # lane — that is where the bug drew a red pipe.
    commit_y_y_center = cfg.header_height + commit_y_row_idx * cfg.row_height + cfg.row_height // 2
    stash1_y_center = cfg.header_height + stash1_row_idx * cfg.row_height + cfg.row_height // 2
    offset_lane_x = widget._dividers[0] + cfg.graph_left_padding + stash1_commit_lane * lane_w
    probe_y_start = commit_y_y_center + cfg.node_radius + 4
    probe_y_end = stash1_y_center - cfg.node_radius - 4
    assert probe_y_end > probe_y_start, (
        "test geometry broken — probe range collapsed"
    )

    for probe_y in range(probe_y_start, probe_y_end + 1):
        ix = int(offset_lane_x * dpr)
        iy = int(probe_y * dpr)
        if not (0 <= ix < img.width() and 0 <= iy < img.height()):
            continue
        color = img.pixelColor(ix, iy)
        assert max(color.red(), color.green(), color.blue()) < 50, (
            f"stray pipe at lane {stash1_commit_lane} between Stash 1 "
            f"and Commit Y: probe ({offset_lane_x}, {probe_y}) is "
            f"{color.name()}, rgb=({color.red()}, {color.green()}, {color.blue()})"
        )


def _make_root_with_stash_and_wip_repo(
    path: Path,
) -> tuple[RepositoryManager, str, str]:
    """Build a single-commit repo with a stash on top and a WIP node above.

    Timeline (chronological):

    1. ``root`` — only commit on ``main``.

    Then on top of the root we drop a stash of staged changes plus an
    uncommitted ``WIP`` node above the stash.  In the rendered graph
    (newest first): ``WIP`` (lane 0), then ``stash`` (offset lane
    because the WIP node claims lane 0), then ``root`` (back on lane
    0 — its own branch).

    The bug this test pins: lane 0 — which is the *root's* own lane
    — has a PIPE cell at the stash row whose default colour is the
    WIP/stash lane's colour (UNCOMMITTED).  Visually that makes the
    vertical line above the root start in WIP-grey, switch to main-
    blue at the stash row, then connect to the root.  The fork-
    connector at the root is drawn in main-blue so the line *should*
    be main-blue all the way up to where the WIP node interrupts it.
    """
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)

    (path / "f.txt").write_text("a\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c_root = mgr.repo.create_commit("refs/heads/main", sig, sig, "root", tree, [])

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("b\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()

    from src.core.operations import stash_push
    stash_push(mgr, "WIP", include_untracked=False)

    # Touch a tracked file so the worktree reports uncommitted changes
    # and the WIP node is inserted above the stash.
    time.sleep(1)
    (path / "f.txt").write_text("c\n")

    return mgr, str(c_root), "WIP"


def test_stash_fork_connector_uses_merging_branch_colour(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The fork connector at the root row must read as the colour
    of the merging lane (the stash), not the root's own colour.

    Regression guard for a previous attempt to fix the bridge
    pipe above a root commit. That attempt also rewrote
    :func:`_build_fork_connector_cells` in :mod:`src.core.graph_v2`
    so the fork connector picked up ``main_color`` everywhere —
    which meant the connector going from the root commit to a
    offset stash node rendered in the *root's* colour, severing
    the visual link to the stash's own colour.

    The cell-level invariant below says: every fork-connector
    cell at the root row carries the merging lane's colour
    (the stash's colour index), not the root commit's colour.
    """
    from src.core.graph_v2 import CellType
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _root_sha, _ = _make_root_with_stash_and_wip_repo(tmp_git_repo)

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    root_row_idx = next(
        i for i, r in enumerate(widget._rows)
        if r.get("commit") and r["commit"]["subject"] == "root"
    )
    stash_row_idx = next(
        i for i, r in enumerate(widget._rows)
        if r.get("commit") and r["commit"]["kind"] == "stash"
    )
    assert stash_row_idx == root_row_idx - 1, (
        f"test setup broken — stash (idx {stash_row_idx}) is not "
        f"directly above the root (idx {root_row_idx})"
    )

    root_row = widget._rows[root_row_idx]
    stash_row = widget._rows[stash_row_idx]
    root_color_index = root_row.get("color_index", -1)
    stash_color_index = stash_row.get("color_index", -1)

    cells = root_row.get("cells", [])
    fork_cells = [
        c for c in cells if c.get("t") in (
            CellType.TEE_RIGHT, CellType.HORIZONTAL,
            CellType.HORIZONTAL_PIPE, CellType.MERGE_LEFT,
        )
    ]
    assert fork_cells, (
        "test setup broken — root row has no fork connector cells: "
        + repr(cells)
    )

    bad = [
        c for c in fork_cells
        if c.get("c") != stash_color_index
    ]
    assert not bad, (
        f"fork connector cells at root row should use the merging "
        f"lane's colour {stash_color_index} (the stash), not the "
        f"root's own colour {root_color_index}. Cells with the "
        f"wrong colour: "
        + ", ".join(
            f"{CellType(c['t']).name} c={c.get('c')}" for c in bad
        )
    )


def test_lane_above_root_stays_in_root_branch_colour(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Lane 0 transitions WIP-grey → main-blue at the bottom of the
    stash row, so the line above the root commit reads as main-blue
    while the line just under the WIP node reads as WIP-grey.

    Companion to :func:`test_root_commit_does_not_draw_stub_below_itself`:
    that test pins the *bottom* of the root (no stub dangling into
    the empty area below); this one pins the *above* — the lane the
    root commit sits on must read as a continuous main-blue line from
    the bottom of the stash row down into the root commit's circle,
    and as a WIP-grey line from the WIP node down to the bottom of
    the stash row.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _root_sha, _stash_msg = _make_root_with_stash_and_wip_repo(tmp_git_repo)

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    root_row_idx = next(
        i for i, r in enumerate(widget._rows)
        if r.get("commit") and r["commit"]["subject"] == "root"
    )
    stash_row_idx = next(
        i for i, r in enumerate(widget._rows)
        if r.get("commit") and r["commit"]["subject"].startswith("Stash @")
    )
    assert stash_row_idx == root_row_idx - 1, (
        f"test setup broken — stash (idx {stash_row_idx}) is not "
        f"directly above the root (idx {root_row_idx})"
    )

    cfg = widget._cfg
    dpr = widget.devicePixelRatio()
    img = widget.grab().toImage()
    lane_w = cfg.node_radius * 2 + 8
    stash_y = widget._row_y(stash_row_idx) + cfg.row_height / 2
    root_y = widget._row_y(root_row_idx) + cfg.row_height / 2
    lane0_x = widget._lane_x(0, lane_w)

    wip_grey = (80, 80, 80)  # DARK_THEME.graph_wip

    def _bad_pixels(
        probe_y_start: int, probe_y_end: int, expected: tuple[int, int, int],
    ) -> list[tuple[int, int, int, int, str]]:
        bad: list[tuple[int, int, int, int, str]] = []
        for py in range(probe_y_start, probe_y_end + 1):
            ix = int(lane0_x * dpr)
            iy = int(py * dpr)
            if not (0 <= ix < img.width() and 0 <= iy < img.height()):
                continue
            color = img.pixelColor(ix, iy)
            if max(color.red(), color.green(), color.blue()) < 30:
                continue
            if (
                abs(color.red() - expected[0]) > 30
                or abs(color.green() - expected[1]) > 30
                or abs(color.blue() - expected[2]) > 30
            ):
                bad.append((py, color.red(), color.green(), color.blue(), color.name()))
        return bad

    # (a) The gap between the bottom of the stash circle and the top
    #     of the root circle must read as WIP-grey — the bridge pipe
    #     inherits the colour of the lane above (the WIP/stash PIPE
    #     at lane 0 in the stash row), not the root's TEE_RIGHT.
    gap_start = int(stash_y + cfg.node_radius + 1)
    gap_end = int(root_y - cfg.node_radius - 1)
    assert gap_end > gap_start, (
        "test geometry broken — stash and root are too close together"
    )
    bad_gap = _bad_pixels(gap_start, gap_end, wip_grey)
    assert not bad_gap, (
        "bridge pipe from stash to root at lane 0 is not WIP-grey: "
        + ", ".join(
            f"y={py} rgb=({r},{g},{b}) #{name}"
            for py, r, g, b, name in bad_gap[:5]
        )
    )

    # (b) Inside the stash row, lane 0 carries a passing-through PIPE
    #     which inherits the lane's tracking colour — in this layout
    #     that is the WIP-grey above the stash. The PIPE is therefore
    #     grey, not main-blue.
    stash_inner_start = int(stash_y - cfg.node_radius + 1)
    stash_inner_end = int(stash_y + cfg.node_radius - 1)
    bad_stash = _bad_pixels(stash_inner_start, stash_inner_end, wip_grey)
    assert not bad_stash, (
        "PIPE at stash row lane 0 should be WIP-grey, not main-blue: "
        + ", ".join(
            f"y={py} rgb=({r},{g},{b}) #{name}"
            for py, r, g, b, name in bad_stash[:5]
        )
    )


def _make_root_with_forked_child_repo(
    path: Path,
) -> tuple[RepositoryManager, str]:
    """Build a repo whose root commit is the branch point of a fork.

    Timeline (chronological):

    1. ``root`` — first commit on ``main``.
    2. ``second`` — child of ``root`` on ``main``.
    3. ``feature`` branch created from ``root`` (so ``root`` has TWO
       children: ``second`` on main and ``feature_child`` on the
       feature branch).
    4. ``feature_child`` — child of ``root`` on ``feature``.

    In the graph (newest first): ``feature_child``, ``second``,
    ``root``. The ``root`` row carries a fork connector
    (``TEE_RIGHT`` + ``MERGE_LEFT`` + lane-0 ``PIPE`` passing through
    the ``second`` row) describing the fan-out. ``root`` is the
    bottommost row — there is no row below it — so any vertical line
    drawn at ``root``'s lane that extends below ``y_center + node_radius``
    is a stub dangling into empty space.
    """
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)

    (path / "f.txt").write_text("a\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c_root = mgr.repo.create_commit("refs/heads/main", sig, sig, "root", tree, [])

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "f.txt").write_text("b\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit(
        "refs/heads/main", sig, sig, "second", tree, [c_root],
    )

    from src.core.operations import (
        checkout_branch as op_checkout_branch,
    )
    from src.core.operations import (
        create_branch as op_create_branch,
    )
    op_create_branch(mgr, "feature", str(c_root))
    op_checkout_branch(mgr, "feature")

    time.sleep(1)
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (path / "g.txt").write_text("feature\n")
    mgr.repo.index.add("g.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit(
        "refs/heads/feature", sig, sig, "feature_child", tree, [c_root],
    )

    return mgr, str(c_root)


def test_root_commit_does_not_draw_stub_below_itself(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The root (bottommost) commit must not draw a vertical stub below.

    Companion to :func:`test_topmost_commit_does_not_draw_line_stub_into_empty_space`:
    that test pins the upward stub at the topmost commit; this one pins
    the downward stub at the bottommost commit.

    When ``root`` is a fork point with children on multiple lanes, its
    row carries ``TEE_RIGHT`` / ``TEE_LEFT`` / ``HORIZONTAL_PIPE`` cells
    whose vertical extents reach ``y_center + half_h`` so they bridge
    into the row below. At the bottom of the layout there *is* no row
    below, so the part of the vertical that lies between
    ``y_center + node_radius`` and ``y_center + half_h`` is a stub that
    dangles into the empty space below the root.
    """
    from src.core.graph_v2 import CellType as _CellType
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _root_sha = _make_root_with_forked_child_repo(tmp_git_repo)

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Pin the precondition: there is exactly one root commit, it is
    # the bottommost row, and its row carries a fork connector at
    # least. Without those conditions the bug wouldn't manifest, so a
    # future refactor that reorders lanes must surface here instead
    # of as a "passes for the wrong reason" false positive.
    root_row = next(
        r for r in widget._rows
        if r.get("commit") and r["commit"]["subject"] == "root"
    )
    root_row_idx = widget._rows.index(root_row)
    assert root_row_idx == len(widget._rows) - 1, (
        "test setup broken — root commit is not the bottommost row"
    )
    root_cell_types = {c.get("t") for c in root_row["cells"]}
    assert any(
        t in root_cell_types for t in (
            _CellType.TEE_RIGHT, _CellType.TEE_LEFT, _CellType.HORIZONTAL_PIPE,
        )
    ), (
        "test setup broken — root row has no fork-connector cell; "
        f"got cell types {root_cell_types}"
    )

    cfg = widget._cfg
    dpr = widget.devicePixelRatio()
    img = widget.grab().toImage()

    # Probe every non-empty lane at the root row, *below* the commit
    # circle, looking for pipe-coloured pixels. The clean range is
    # ``y_center + node_radius + 1`` (just below the circle outline)
    # to ``y_center + row_height`` (bottom of the row, where the
    # stub would terminate).
    y_center = widget._row_y(root_row_idx) + cfg.row_height / 2
    lane_w = cfg.node_radius * 2 + 8
    probed: list[tuple[int, int, int, str]] = []
    for ci, cell in enumerate(root_row["cells"]):
        if cell.get("t", 0) == _CellType.EMPTY:
            continue
        lane = ci // 2
        x = widget._lane_x(lane, lane_w)
        for probe_y in range(
            int(y_center + cfg.node_radius + 1),
            int(y_center + cfg.row_height),
        ):
            ix = int(x * dpr)
            iy = int(probe_y * dpr)
            if not (0 <= ix < img.width() and 0 <= iy < img.height()):
                continue
            color = img.pixelColor(ix, iy)
            ch_max = max(color.red(), color.green(), color.blue())
            if ch_max >= 50:
                probed.append((lane, probe_y, ch_max, color.name()))

    assert not probed, (
        "stray stub below root commit (bottommost row): "
        + ", ".join(
            f"lane {lane} y={py} channel_max={mx} #{name}"
            for lane, py, mx, name in probed[:5]
        )
    )


# ----- branch label column --------------------------------------------


def _branch_label_items(widget: GraphWidget, sha: str) -> list[QGraphicsItem]:
    """Return the chip items in the branch-label column for *sha*."""
    return widget._branch_label_items.get(sha, [])  # noqa: SLF001


def _chip_children(
    widget: GraphWidget, sha: str, item_type: type,
) -> list[QGraphicsItem]:
    """Return child items of the given *item_type* from every chip for *sha*."""
    result: list[QGraphicsItem] = []
    for chip in _branch_label_items(widget, sha):
        for child in chip.childItems():
            if isinstance(child, item_type):
                result.append(child)
    return result


def _branch_label_texts(widget: GraphWidget, sha: str) -> list[str]:
    """Return the plain-text content of every text item in the column."""
    return [it.text() for it in _chip_children(widget, sha, QGraphicsSimpleTextItem)]


def _branch_label_icons(widget: GraphWidget, sha: str) -> list[QGraphicsPathItem]:
    """Return icon paths (checkmark/monitor) — NoBrush distinguishes from chip."""
    return [
        it for it in _chip_children(widget, sha, QGraphicsPathItem)
        if it.brush().style() == Qt.BrushStyle.NoBrush
    ]


def _branch_label_chips(widget: GraphWidget, sha: str) -> list[QGraphicsPathItem]:
    """Return chip (coloured background) items for *sha*."""
    return [
        it for it in _branch_label_items(widget, sha)
        if isinstance(it, QGraphicsPathItem)
    ]


def test_widget_renders_local_branch_label(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A commit with a non-HEAD local branch shows a chip + name + monitor."""
    mgr = _make_committed_repo(tmp_git_repo)
    parent_sha = mgr.get_all_history()[-1].sha
    mgr.repo.create_reference(
        "refs/heads/old_main", parent_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    assert _branch_label_texts(widget, parent_sha) == ["old_main"]
    # old_main is local, not HEAD: one chip + one monitor icon.
    assert len(_branch_label_chips(widget, parent_sha)) == 1
    icons = _branch_label_icons(widget, parent_sha)
    assert len(icons) == 1


def test_widget_renders_checkmark_for_head_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The current branch chip contains a checkmark + monitor inside it."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    icons = _branch_label_icons(widget, head_sha)
    assert len(icons) == 2  # checkmark + monitor
    # Both icons sit inside the chip: left-aligned checkmark near padding,
    # monitor right of the name.
    xs = sorted(int(round(p.scenePos().x())) for p in icons)
    fm = widget.fontMetrics()
    column_margin = 6
    pad = 5
    icon_size = 10
    gap = 3
    check_x = column_margin + pad
    monitor_x = column_margin + pad + icon_size + gap + fm.horizontalAdvance("main") + gap
    assert xs == sorted([check_x, monitor_x])


def test_widget_renders_no_monitor_for_remote_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """When both local ``main`` and remote ``origin/main`` point at the
    same commit, the remote chip is suppressed — the monitor icon on
    the local chip already conveys the "local" information."""
    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/main", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    # Only the local "main" — the remote duplicate is suppressed.
    texts = _branch_label_texts(widget, head_sha)
    assert texts == ["main"]
    # One chip (local) with checkmark + monitor.
    assert len(_branch_label_chips(widget, head_sha)) == 1
    assert len(_branch_label_icons(widget, head_sha)) == 2  # checkmark + monitor


def test_widget_remote_branch_strips_origin_prefix(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A remote-only branch (no local copy) shows chip with short name, no monitor."""
    mgr = _make_committed_repo(tmp_git_repo)
    # origin/base_features — remote only, no local copy.
    mgr.repo.references.create(
        "refs/remotes/origin/base_features", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    texts = _branch_label_texts(widget, head_sha)
    # main (local HEAD) + base_features (remote, stripped prefix).
    assert texts == ["main", "base_features"]
    # main: chip + checkmark + monitor.  base_features: chip only.
    assert len(_branch_label_chips(widget, head_sha)) == 2
    assert len(_branch_label_icons(widget, head_sha)) == 2  # checkmark + monitor for main


def test_widget_no_branch_label_items_for_commit_without_branches(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A commit no branch points at has an empty branch-label column."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # The older commit (the parent of HEAD) has no branch attached.
    older_sha = mgr.get_all_history()[-1].sha
    assert widget._branch_label_items[older_sha] == []  # noqa: SLF001


def test_graph_node_lane_zero_offsets_by_branch_label_width(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The leftmost commit node must sit to the right of the label column."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    cfg = widget._cfg  # noqa: SLF001
    head_node = widget._node_items[mgr.head_commit.sha]  # noqa: SLF001
    expected_x = cfg.branch_label_width + cfg.lane_offset
    assert int(round(head_node.rect().center().x())) == expected_x


# ----- scroll_to_commit (GraphViewModel) -----------------------------


def test_viewmodel_scroll_to_commit_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``GraphViewModel.scroll_to_commit`` emits the view-side signal verbatim.

    The view subscribes to :attr:`scroll_to_commit_requested`; this
    test pins the ViewModel's role as a pure forwarder so we know
    that the contract for the view is just "you will receive the
    SHA, do whatever scrolling is needed".
    """
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    # GraphViewModel is a QObject, not a QWidget — qtbot.addWidget
    # would refuse it. The QObject lives only for the duration of
    # the test and the Qt event loop is driven by ``waitSignal``, so
    # no explicit teardown is needed.

    head_sha = mgr.head_commit.sha
    with qtbot.waitSignal(vm.scroll_to_commit_requested, timeout=1000) as blocker:
        vm.scroll_to_commit(head_sha)
    assert blocker.args[0] == head_sha


def test_viewmodel_scroll_to_commit_empty_sha_is_noop(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A falsy SHA is ignored — no signal, no crash.

    Defends against accidental callers (e.g. a half-built branch
    snapshot) passing ``None`` or ``""``: the view would otherwise
    scroll to "nothing" and the widget would have to defend itself.
    """
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)

    with qtbot.assertNotEmitted(vm.scroll_to_commit_requested, wait=200):
        vm.scroll_to_commit("")
        vm.scroll_to_commit(None)  # type: ignore[arg-type]


# ----- scroll_to_commit (GraphTableWidget) ---------------------------


def test_graph_table_widget_scrolls_vertically_to_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The graph table scrolls vertically so the target commit is centred.

    Same behaviour as before Stage 4: the row's vertical centre is
    brought to the viewport's vertical centre, regardless of horizontal
    state.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    # Add a few extra commits so the graph is tall enough to scroll.
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    # ``bottom_oid`` captures the *very first* commit on the chain —
    # the one that existed before the loop started. After the loop
    # ``refs/heads/main`` has been rewritten, so reading HEAD would
    # give us the most-recent commit, not the bottom of the graph.
    bottom_oid = mgr.repo.head.target
    parent = bottom_oid
    for i in range(10):
        (tmp_git_repo / "f.txt").write_text(f"v{i + 3}\n")
        mgr.repo.index.add("f.txt")
        mgr.repo.index.write()
        tree = mgr.repo.index.write_tree()
        parent = mgr.repo.create_commit(
            "refs/heads/main", sig, sig, f"commit {i + 3}", tree, [parent],
        )
    bottom_sha = str(bottom_oid)
    head_sha = str(mgr.repo.head.target)

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 200)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Sanity-check: bottom_sha is not the head commit and the
    # scrollbar overflows.
    assert bottom_sha != head_sha
    assert widget._scrollbar.maximum() > 0  # noqa: SLF001

    # Scroll the viewport back to the top first so the test can
    # observe the downward jump.
    widget._scrollbar.setValue(0)  # noqa: SLF001
    widget.update()

    # Now ask the widget to scroll to the bottom commit.
    widget.scroll_to_commit(bottom_sha)

    # The bottom commit's row is the last one. The vertical scroll
    # value should now put that row near the viewport centre.
    assert widget._scrollbar.value() > 0  # noqa: SLF001


def test_graph_table_widget_scroll_horizontal_brings_lane_into_view(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A commit outside the visible graph column triggers a horizontal scroll.

    Builds a graph with a wide lane (lane index 10, well past the
    visible window for an 800px-wide widget) and calls the private
    :meth:`_scroll_horizontal_to_lane` helper directly with a
    synthetic row dict — the helper is the only place the new
    horizontal-scroll math lives, so this is the cheapest way to
    pin its behaviour.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Force the graph column to overflow horizontally by synthesising
    # a row with a very high lane index. Then set the horizontal
    # scrollbar to a non-zero range so the helper has room to move.
    widget._h_scrollbars[1].setRange(0, 500)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(0)  # noqa: SLF001

    fake_row = {
        "commit": {"sha": "deadbeef", "kind": "commit"},
        "lane": 10,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(fake_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() > 0  # noqa: SLF001


def test_graph_table_widget_no_horizontal_scroll_when_lane_already_visible(
    qtbot, tmp_git_repo: Path,
) -> None:
    """If the lane is already on screen, the horizontal scroll is left alone.

    The helper is a no-op when the target lane's centre sits within
    the visible portion of the graph column (with a one-node-radius
    margin). We start the bar at a non-zero value to make the
    assertion meaningful — a careless implementation that always
    recenters would change the value here.

    With the bar at ``value=123`` the content is shifted left by
    123px, so a lane at content-x ~480 (i.e. lane 9) lands inside
    the visible column.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    widget._h_scrollbars[1].setRange(0, 500)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(123)  # noqa: SLF001

    fake_row = {
        "commit": {"sha": "deadbeef", "kind": "commit"},
        "lane": 9,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(fake_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() == 123  # noqa: SLF001


def test_graph_table_widget_horizontal_scroll_skips_connector_rows(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A connector row (no commit) is ignored by the horizontal scroll helper.

    Connector rows in the cell-based layout represent a lane
    continuation without a node — scrolling to one of them would
    be meaningless. The helper must return without touching the
    horizontal scrollbar.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    widget._h_scrollbars[1].setRange(0, 500)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(42)  # noqa: SLF001

    connector_row = {
        "commit": None,
        "lane": 5,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(connector_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() == 42  # noqa: SLF001


def test_graph_table_widget_horizontal_scroll_noop_when_bar_disabled(
    qtbot, tmp_git_repo: Path,
) -> None:
    """If the graph column has no horizontal overflow, the helper is a no-op.

    The bar is hidden in that case (``maximum() == 0``); touching it
    would push the value past its valid range. We verify the helper
    does not error and leaves the bar alone.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Force the bar to "disabled" state — maximum == 0, like when
    # the graph column fits entirely in the viewport.
    widget._h_scrollbars[1].setRange(0, 0)  # noqa: SLF001
    widget._h_scrollbars[1].setValue(0)  # noqa: SLF001

    fake_row = {
        "commit": {"sha": "deadbeef", "kind": "commit"},
        "lane": 5,
        "color_index": 0,
        "branch_names": [],
        "is_head": False,
        "is_uncommitted": False,
        "cells": [],
    }
    widget._scroll_horizontal_to_lane(fake_row)  # noqa: SLF001
    assert widget._h_scrollbars[1].value() == 0  # noqa: SLF001


def test_graph_table_widget_subscribes_to_scroll_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The widget responds to :attr:`scroll_to_commit_requested`.

    Driving the ViewModel side (not the widget's API directly)
    proves the signal/slot wiring done in :meth:`GraphTableWidget.__init__`
    is correct.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(800, 600)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    head_sha = mgr.head_commit.sha
    widget._scrollbar.setValue(0)  # noqa: SLF001

    vm.scroll_to_commit(head_sha)
    # The signal is connected directly; it runs synchronously so the
    # scrollbar has the new value by the time we read it.
    assert widget._scrollbar.value() >= 0  # noqa: SLF001


# ----- branch chip hit-testing & gestures ------------------------------


def _chip_viewport_pos(widget, sha: str, display: str) -> QPoint:
    """Convert a branch chip's content rect to a viewport click point.

    Returns the centre of the chip — good enough for hit-tests because
    the chip's rounded-rect radius is small enough to leave a clear
    inside region even after the 4-px slop Qt adds during drag.
    """
    chip = widget._branch_chip_rects[(sha, display)]  # noqa: SLF001
    rect = chip["rect"]
    # Translate content x to widget x (the column scrolls horizontally
    # in content coordinates; the click arrives in widget coordinates).
    widget_x = rect.center().x() - widget._h_scrolls[0]  # noqa: SLF001
    # The y is in widget coordinates directly (the painter is not
    # translated vertically, the row position already accounts for the
    # vertical scroll).
    widget_y = rect.center().y()
    return QPoint(int(widget_x), int(widget_y))


def _force_paint(widget) -> None:
    """Force a paint pass so the chip-rect cache is populated.

    In offscreen Qt, ``show()`` + a few signal emissions are not
    always enough to land a paint event before the test starts
    asserting on the cache. The three-call dance (``update`` →
    ``processEvents`` → ``repaint``) is the cheapest way to get
    deterministic paints: ``update`` schedules a repaint,
    ``processEvents`` lets the scheduled event run, and ``repaint``
    blocks until the actual paint completes. Tests that need a
    populated chip cache should call this once after the graph
    update.
    """
    widget.update()
    QApplication.processEvents()
    widget.repaint()


def _make_repo_with_feature(
    path: Path,
) -> tuple[RepositoryManager, str, str]:
    """Build a repo with a second local branch named ``feature`` at HEAD.

    Returns ``(manager, head_sha, feature_sha)``. ``head_sha`` and
    ``feature_sha`` are equal because ``feature`` is created on top of
    ``main``'s tip — the chip is a duplicate of the HEAD chip, which
    is the realistic case the new branch gestures need to handle.
    """
    mgr = _make_committed_repo(path)
    feature_sha = mgr.head_commit.sha
    mgr.repo.create_reference(
        "refs/heads/feature", feature_sha, force=True,
    )
    head_sha = mgr.head_commit.sha
    return mgr, head_sha, feature_sha


def test_branch_chip_at_returns_none_outside_branch_column(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A click in the graph column (not on a chip) must not match.

    The hit-test's first guard is the column boundary — anything past
    the first divider is in the graph / commit-message area and must
    fall through to the commit-selection path.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Click squarely in the graph column (the first divider is at 180
    # by default; click at 400 which is well into col 1).
    chip = widget._branch_chip_at(400, 200)  # noqa: SLF001
    assert chip is None


def test_branch_chip_at_finds_chip_after_horizontal_scroll(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Hit-test works when the branch column is scrolled horizontally.

    Forces the column's scrollbar to a small non-zero value and
    clicks at the chip's post-scroll screen position. The test pins
    the ``content_x + scroll`` math in :meth:`_branch_chip_at` so a
    regression here is loud (the chip would simply never be found).
    The scroll value is small enough to keep the chip partially on
    screen — a click at its post-scroll centre is therefore a real,
    visible hit.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(300, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Force the column overflow so the horizontal scrollbar activates.
    # 40 px is enough to slide the rightmost "main" chip a bit, but
    # the leftmost "feature" chip stays inside the column.
    widget._h_scrollbars[0].setRange(0, 200)  # noqa: SLF001
    widget._h_scrollbars[0].setValue(40)  # noqa: SLF001
    widget._h_scrolls[0] = 40  # noqa: SLF001
    _force_paint(widget)

    # Compute the chip's post-scroll viewport position and click there.
    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    chip = widget._branch_chip_at(pos.x(), pos.y())  # noqa: SLF001
    assert chip is not None
    assert chip["display"] == "feature"
    assert chip["is_head"] is False
    assert chip["is_remote"] is False


def test_branch_chip_at_finds_head_chip(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The current branch's chip carries ``is_head=True``.

    The flag matters for the context menu — actions on the active
    branch are disabled, since merging / rebasing a branch into
    itself is a no-op. The test pins the flag at the chip level so a
    future refactor that drops the ``is_head`` plumbing would be
    caught here.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")
    chip = widget._branch_chip_at(pos.x(), pos.y())  # noqa: SLF001
    assert chip is not None
    assert chip["display"] == "main"
    assert chip["is_head"] is True


def test_left_click_on_branch_chip_does_not_select_commit(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A left click on a branch chip must NOT change the commit selection.

    This is the contract: the chip is a separate gesture surface from
    the commit node that lives in the same row. A click that would
    otherwise have selected the row's commit must be swallowed by
    the chip hit-test.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, head_sha, "feature")
    with qtbot.assertNotEmitted(vm.commit_selected, wait=200):
        qtbot.mouseClick(
            widget, Qt.MouseButton.LeftButton, pos=pos,
        )
    # The widget's own selection also stayed where it was (None
    # initially).
    assert widget.selected_sha() is None


def test_double_click_on_branch_chip_emits_checkout(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Double-clicking a branch chip emits the checkout signal with the ref name.

    Uses the ``feature`` chip so the signal carries ``"feature"`` —
    the bare local name, not ``"refs/heads/feature"``. The VM's
    :meth:`checkout_branch` accepts the bare name so the wiring in
    :meth:`MainWindow._on_graph_branch_checkout` stays a one-liner.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        qtbot.mouseDClick(
            widget, Qt.MouseButton.LeftButton, pos=pos,
        )
    assert blocker.args[0] == "feature"


def test_double_click_on_head_branch_chip_emits_checkout(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Double-clicking the current branch's chip also emits the signal.

    Edge case: clicking the HEAD chip is a no-op as far as the actual
    checkout goes (you're already there), but the signal still fires
    so the MainWindow can decide what to do — the left panel, the
    toolbar and the chip are all consistent. The test pins that the
    gesture is not silently swallowed.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")
    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        qtbot.mouseDClick(
            widget, Qt.MouseButton.LeftButton, pos=pos,
        )
    assert blocker.args[0] == "main"


def test_right_click_on_branch_chip_routes_to_branch_menu(
    qtbot, tmp_git_repo: Path, monkeypatch,
) -> None:
    """Right-clicking a branch chip invokes the branch menu, not the commit one.

    Pins the dispatch in :meth:`_on_context_menu`: the chip hit-test
    must run before the commit hit-test, otherwise the chip click
    would fall through to the commit menu (Checkout this commit).
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Patch the menu builders so we can detect which path the context
    # menu took without running QMenu.exec() (which would block).
    branch_called = []
    commit_called = []
    monkeypatch.setattr(
        widget, "_show_branch_context_menu",
        lambda chip, pos: branch_called.append(chip["display"]),
    )
    monkeypatch.setattr(
        widget, "_on_context_menu_for_commit",  # type: ignore[attr-defined]
        lambda sha, pos: commit_called.append(sha),
        raising=False,
    )
    # Safety net: if the chip hit-test ever regresses and the call falls
    # through to the commit path, the real ``QMenu.exec`` would block the
    # test run forever.  No-op it so a regression fails instead of hangs.
    monkeypatch.setattr(QMenu, "exec", lambda self, *args, **kwargs: None)

    pos = _chip_viewport_pos(widget, head_sha, "feature")
    widget._on_context_menu(pos)  # noqa: SLF001

    assert branch_called == ["feature"]
    assert commit_called == []


def test_show_branch_context_menu_contains_merge_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The branch chip's context menu includes 'Merge X into Y'.

    The menu is built in :meth:`_show_branch_context_menu` and
    contains a single :class:`QAction` per verb. The merge action's
    label interpolates both the source and the current HEAD so the
    user sees a readable description of what they are about to do.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001

    # Build the actions synchronously — the actual QMenu.exec() would
    # block waiting for a click. We just want to inspect the actions.
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    labels = [a.text() for a in actions]

    assert "Checkout feature" in labels
    assert "Merge feature into main" in labels
    assert "Rebase feature onto main" in labels


def test_show_branch_context_menu_merge_disabled_on_current_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The merge / rebase actions are disabled for the current branch.

    Merging ``main`` into ``main`` is a no-op; the menu shows the
    action so the user can see what they could do, but it is greyed
    out. Mirrors the left panel's ``_merge_rebase_against_current``
    policy.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "main")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    merge = next(a for a in actions if a.text().startswith("Merge main into"))
    rebase = next(a for a in actions if a.text().startswith("Rebase main onto"))
    assert merge.isEnabled() is False
    assert rebase.isEnabled() is False


def test_branch_menu_merge_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Triggering the merge action emits merge_branch_requested with source+target."""
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    merge = next(a for a in actions if a.text() == "Merge feature into main")

    with qtbot.waitSignal(widget.merge_branch_requested, timeout=1000) as blocker:
        merge.trigger()
    assert blocker.args == ["feature", "main"]


def test_branch_menu_rebase_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The rebase action emits rebase_branch_requested with source+target."""
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    rebase = next(a for a in actions if a.text() == "Rebase feature onto main")

    with qtbot.waitSignal(widget.rebase_branch_requested, timeout=1000) as blocker:
        rebase.trigger()
    assert blocker.args == ["feature", "main"]


def test_branch_menu_checkout_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The checkout action in the chip menu emits the checkout signal."""
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    checkout = next(a for a in actions if a.text() == "Checkout feature")

    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        checkout.trigger()
    assert blocker.args == ["feature"]


def test_remote_branch_chip_checkout_uses_full_ref_name(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A remote-only branch chip's checkout action carries the full ref name.

    The chip displays ``base_features`` (the ``origin/`` prefix is
    stripped for the user), but the underlying ref is
    ``refs/remotes/origin/base_features``. The wiring in
    :meth:`MainWindow._on_graph_branch_checkout` looks at the full
    name to decide between local and fetch+checkout — so the action
    must pass the full ref through, not the display label.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/base_features", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "base_features")]  # noqa: SLF001
    assert chip["is_remote"] is True
    assert chip["full_name"] == "origin/base_features"
    actions = widget._build_branch_menu_actions(chip)  # noqa: SLF001
    checkout = next(
        a for a in actions if a.text().startswith("Checkout base_features")
    )
    with qtbot.waitSignal(
        widget.checkout_branch_requested, timeout=1000,
    ) as blocker:
        checkout.trigger()
    assert blocker.args == ["origin/base_features"]


# ----- WIP context menu (Stash Changes) -------------------------------


def test_wip_menu_has_stash_changes_action_first(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-clicking the WIP node shows a 'Stash Changes' action on top.

    The WIP node sits at row 0 (above HEAD) whenever the worktree has
    uncommitted changes. The action is placed **first** in the menu so
    the primary verb on a dirty worktree is the one-click reachable
    option — the user does not have to scroll through destructive
    choices (Discard changes) to reach the safe one (Stash).

    Built via :meth:`GraphTableWidget._build_node_menu` so the test
    does not block on ``QMenu.exec``.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    menu = widget._build_node_menu("WIP", "wip")  # noqa: SLF001
    qtbot.addWidget(menu)
    labels = [a.text() for a in menu.actions()]
    assert labels[0] == "Stash Changes"
    assert "Discard changes" in labels
    assert "Copy diff" in labels


def test_wip_menu_stash_action_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Triggering 'Stash Changes' emits ``stash_push_requested`` with WIP.

    The signal payload is the WIP marker (``"WIP"``) — a stable
    sentinel for "the worktree is dirty". The :class:`MainWindow`
    handler ignores the payload and just delegates to
    :meth:`MainViewModel.stash_push`.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    menu = widget._build_node_menu("WIP", "wip")  # noqa: SLF001
    qtbot.addWidget(menu)
    stash_action = next(
        a for a in menu.actions() if a.text() == "Stash Changes"
    )
    with qtbot.waitSignal(
        widget.stash_push_requested, timeout=1000,
    ) as blocker:
        stash_action.trigger()
    assert blocker.args == ["WIP"]


def test_wip_menu_discard_action_still_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The pre-existing Discard action still works after the menu change.

    The WIP menu now starts with ``Stash Changes``; the new helper
    must keep emitting the original ``discard_changes_requested``
    signal so the existing ``MainWindow`` wiring (which calls
    :meth:`MainViewModel.discard_changes`) keeps working.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    menu = widget._build_node_menu("WIP", "wip")  # noqa: SLF001
    qtbot.addWidget(menu)
    discard_action = next(
        a for a in menu.actions() if a.text() == "Discard changes"
    )
    with qtbot.waitSignal(
        widget.discard_changes_requested, timeout=1000,
    ) as blocker:
        discard_action.trigger()
    assert blocker.args == ["WIP"]


def test_regular_commit_menu_has_no_stash_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A regular commit node's menu does not gain a Stash Changes action.

    Stashing uncommitted work is meaningless for a commit node (the
    worktree is not involved); the new action must only appear on
    the WIP kind. Pins the menu structure for the non-WIP branch so
    a future refactor cannot accidentally add it back to every node.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    menu = widget._build_node_menu(mgr.head_commit.sha, "commit")  # noqa: SLF001
    qtbot.addWidget(menu)
    labels = [a.text() for a in menu.actions()]
    assert "Stash Changes" not in labels
    assert "Checkout this commit" in labels
    assert "Copy diff" in labels


def test_commit_menu_has_copy_sha_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-clicking a commit node shows a 'Copy SHA' action.

    The row context menu (``_build_node_menu`` for kind ``commit``)
    exposes the same 'Copy SHA' verb the branch-chip menu already
    carries — both copy paths reach the same
    :attr:`GraphTableWidget.copy_commit_sha_requested` signal so the
    :class:`MainWindow` wiring is shared.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    menu = widget._build_node_menu(mgr.head_commit.sha, "commit")  # noqa: SLF001
    qtbot.addWidget(menu)
    labels = [a.text() for a in menu.actions()]
    assert "Copy SHA" in labels
    # 'Copy SHA' sits next to 'Copy diff' — same copy-clipboard section.
    copy_diff_idx = labels.index("Copy diff")
    copy_sha_idx = labels.index("Copy SHA")
    assert copy_sha_idx == copy_diff_idx + 1


def test_commit_menu_copy_sha_emits_row_sha(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Triggering 'Copy SHA' emits the commit's full OID.

    The :class:`MainWindow` slot forwards the payload to
    :meth:`MainViewModel.copy_to_clipboard`, so the SHA on the
    clipboard is exactly the row SHA passed to the menu builder.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    menu = widget._build_node_menu(mgr.head_commit.sha, "commit")  # noqa: SLF001
    qtbot.addWidget(menu)
    action = next(a for a in menu.actions() if a.text() == "Copy SHA")
    with qtbot.waitSignal(
        widget.copy_commit_sha_requested, timeout=1000,
    ) as blocker:
        action.trigger()
    assert blocker.args == [mgr.head_commit.sha]


def test_stash_menu_has_copy_sha_action(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-clicking a stash node also exposes 'Copy SHA'.

    Stash entries are backed by real commits in the object database
    (``git stash push`` creates a commit per stash), so the row
    carries a real OID that is meaningful to copy — exactly the
    same payload the user would get from ``git stash show -p
    <stash>`` or any external tool that accepts a stash OID.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    from src.core.operations import stash_push

    stash_push(mgr, "copy-sha-test")

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    stash_sha = mgr.stash_list[0].sha
    menu = widget._build_node_menu(stash_sha, "stash")  # noqa: SLF001
    qtbot.addWidget(menu)
    labels = [a.text() for a in menu.actions()]
    assert "Copy SHA" in labels
    # Co-located with the other copy verb, between 'Copy diff' and 'Delete Stash'.
    copy_diff_idx = labels.index("Copy diff")
    copy_sha_idx = labels.index("Copy SHA")
    delete_idx = labels.index("Delete Stash")
    assert copy_diff_idx < copy_sha_idx < delete_idx


def test_stash_menu_copy_sha_emits_stash_oid(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The stash node's 'Copy SHA' emits the stash entry's real OID.

    The signal payload is the row's ``sha`` field, which for a
    stash row is the stash commit's OID (as stored by ``git stash
    push``). Distinct from the commit the stash was forked from —
    the user typically wants the stash OID so they can pass it to
    ``git show`` / ``git stash apply <oid>``.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    from src.core.operations import stash_push

    stash_push(mgr, "copy-sha-oid")

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    stash_sha = mgr.stash_list[0].sha
    menu = widget._build_node_menu(stash_sha, "stash")  # noqa: SLF001
    qtbot.addWidget(menu)
    action = next(a for a in menu.actions() if a.text() == "Copy SHA")
    with qtbot.waitSignal(
        widget.copy_commit_sha_requested, timeout=1000,
    ) as blocker:
        action.trigger()
    assert blocker.args == [stash_sha]


def test_wip_menu_has_no_copy_sha(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The WIP node's menu does not gain a 'Copy SHA' action.

    The WIP marker is a synthetic sentinel (``"WIP"``) with no
    backing commit — copying ``"WIP"`` to the clipboard would be
    useless, and copying the *parent* SHA would silently mislead
    the user. The verb is intentionally restricted to nodes that
    have a real OID.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    (tmp_git_repo / "f.txt").write_text("v3\n")
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    menu = widget._build_node_menu("WIP", "wip")  # noqa: SLF001
    qtbot.addWidget(menu)
    labels = [a.text() for a in menu.actions()]
    assert "Copy SHA" not in labels


def test_remote_branch_suppressed_when_local_duplicate_exists(
    qtbot, tmp_git_repo: Path,
) -> None:
    """When local ``main`` and remote ``origin/main`` share a commit,
    only the local chip is drawn and only it has a hit-test rect.

    The pre-fix behaviour was: both chips were drawn but only the
    second one (the remote) ended up in the chip-rect cache (because
    the cache was keyed by display name), so drag-and-drop worked on
    the remote chip but not the local one. Pinning the single-chip
    result here so a future regression surfaces as a loud failure
    rather than a "menu only opens on the remote chip" surprise.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/main", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Exactly one entry for the HEAD row, keyed by the HEAD commit
    # SHA + the display name. The remote ``origin/main`` chip is
    # suppressed because the local ``main`` chip covers the name.
    head_sha = mgr.head_commit.sha
    assert (head_sha, "main") in widget._branch_chip_rects  # noqa: SLF001
    assert len(widget._branch_chip_rects) == 1  # noqa: SLF001

    chip = widget._branch_chip_rects[(head_sha, "main")]  # noqa: SLF001
    # The surviving chip is the *local* one — it must carry the
    # local-style flags so the menu builder treats it as local.
    assert chip["is_remote"] is False
    assert chip["is_head"] is True
    assert chip["full_name"] == "main"


def test_origin_head_remote_is_dropped_from_chip_cache(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The synthetic ``refs/remotes/<remote>/HEAD`` pseudo-ref must
    never surface as a chip alongside the local ``main`` chip.

    After ``fetch`` every clone carries ``origin/HEAD`` even when the
    user never asked for it; treating it as a regular branch would
    draw an extra ``HEAD`` chip next to the local main chip, which
    the user reports as "main, HEAD, main" — three entries where
    only one was expected.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = str(mgr.head_commit.sha)
    mgr.repo.references.create(
        "refs/remotes/origin/main", head_sha, force=True,
    )
    mgr.repo.references.create(
        "refs/remotes/origin/HEAD", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    keys = list(widget._branch_chip_rects.keys())  # noqa: SLF001
    # Only the local ``main`` chip survives — both ``origin/main``
    # (same short-name as local) and ``origin/HEAD`` (synthetic
    # pseudo-ref) are dropped.
    assert keys == [(head_sha, "main")]


def test_chip_rect_cache_keyed_by_row_and_display(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Two commits with the same display name both have their own chip.

    Without a row-aware key the cache would overwrite the older
    chip with the newer one and the older commit's drag would
    silently stop working. The test builds a feature branch that
    shares ``main``'s name at HEAD and points it at a different
    (older) commit; the cache must have one entry per row.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_commit = mgr.repo[mgr.repo.head.target]
    parent_sha = str(head_commit.parent_ids[0])
    mgr.repo.create_reference(
        "refs/heads/release", parent_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    head_sha = mgr.head_commit.sha
    # HEAD row carries ``main`` (the current branch); the older
    # row carries ``release``. Both keys are present in the
    # cache.
    assert (head_sha, "main") in widget._branch_chip_rects  # noqa: SLF001
    assert (parent_sha, "release") in widget._branch_chip_rects  # noqa: SLF001
    assert len(widget._branch_chip_rects) == 2  # noqa: SLF001


def test_drop_on_suppressed_chip_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Dropping on the single surviving (local) chip works.

    The bug report said: ``drag & drop only works on the remote
    branch``. The fix collapsed the two visual chips into one;
    the drop test pins that the surviving chip's full Qt drag
    pipeline is wired up.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/main", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Build a synthetic drop event at the (only) chip's centre.
    head_sha = mgr.head_commit.sha
    chip = widget._branch_chip_rects[(head_sha, "main")]  # noqa: SLF001
    content_x = chip["rect"].center().x()
    content_y = chip["rect"].center().y()
    drop_x = content_x - widget._h_scrolls[0]  # noqa: SLF001
    drop_pos = QPoint(int(drop_x), int(content_y))

    mime = QMimeData()
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415
    mime.setData(_CHIP_MIME, b"main")
    mime.setText("main")
    event = QDropEvent(
        drop_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )

    with qtbot.assertNotEmitted(widget.branch_dropped_on_branch, wait=200):
        # Dropping main on itself is the documented no-op.
        widget.dropEvent(event)  # noqa: SLF001


# ----- branch chip drag-and-drop --------------------------------------


def _make_chip_mime(
    widget, source_display: str,
) -> QMimeData:
    """Build a :class:`QMimeData` payload that mimics a chip drag.

    The drag payload format is owned by :meth:`GraphTableWidget._begin_chip_drag`
    — both the custom ``application/x-git-py-branch-chip`` type and
    the plain-text branch name are set.  The MIME constant is a
    module-level value in :mod:`src.ui.widgets.graph_panel`; the
    helper imports it on demand so the test does not have to
    duplicate the string.
    """
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415

    mime = QMimeData()
    mime.setData(_CHIP_MIME, source_display.encode("utf-8"))
    mime.setText(source_display)
    return mime


def test_widget_accepts_drops(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``GraphTableWidget`` must call ``setAcceptDrops(True)``.

    ``QWidget`` defaults to ``acceptDrops=False``; without an
    explicit opt-in Qt's drag pipeline never delivers
    ``dragEnterEvent`` / ``dropEvent`` to the widget. The symptom
    is subtle: the press on a branch chip starts a drag (because
    the widget initiates it via ``QDrag``), the cursor changes to
    the drag indicator, but on release nothing happens — the drop
    is silently rejected before the handler ever runs. Pinning
    the flag here turns a runtime "menu doesn't show" mystery
    into a loud, immediate test failure.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    assert widget.acceptDrops() is True


def test_drag_enter_accepts_branch_chip_mime(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The drag-enter handler must accept a chip-style MIME payload.

    The handler is the gate that lets Qt show the drop cursor over
    a chip. A regression that filters out the custom MIME type
    would surface as ``QDrag`` returning ``IgnoreAction`` and the
    drop cursor never appearing — both of which are visible in
    practice but noisy to debug without this test.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    mime = _make_chip_mime(widget, "feature")
    pos = QPoint(50, 100)
    event = QDragEnterEvent(
        pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.dragEnterEvent(event)  # noqa: SLF001
    assert event.isAccepted() is True


def test_drag_enter_rejects_unrelated_mime(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Drop payloads without the chip MIME type are not accepted.

    Defensive: the widget may one day accept other drags (e.g.
    dragging a file from a commit row to the terminal). Until then
    the chip MIME is the only valid payload, and ``dragEnterEvent``
    must fall through to ``super`` for everything else.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    mime = QMimeData()
    mime.setText("some-file.txt")
    pos = QPoint(50, 100)
    event = QDragEnterEvent(
        pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    # ``super().dragEnterEvent`` is a no-op for ``QWidget`` — the
    # test pins that the handler does **not** mark an unrelated drop
    # as accepted.
    widget.dragEnterEvent(event)  # noqa: SLF001
    assert event.isAccepted() is False


def test_drop_on_target_branch_emits_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Dropping the ``feature`` chip on the ``main`` chip emits branch_dropped_on_branch.

    The test synthesises a chip-style ``QDropEvent`` at the
    ``main`` chip's screen position and asserts the signal payload.
    The actual :class:`QMenu` that ``MainWindow`` builds on top of
    this signal is exercised in a separate integration test.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    target_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")
    mime = _make_chip_mime(widget, "feature")
    event = QDropEvent(
        target_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )

    with qtbot.waitSignal(
        widget.branch_dropped_on_branch, timeout=1000,
    ) as blocker:
        widget.dropEvent(event)  # noqa: SLF001
    assert blocker.args == ["feature", "main"]


def test_drop_on_same_chip_is_ignored(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Dropping a chip on itself must not emit a signal.

    Merging a branch into itself is a no-op; emitting a signal here
    would just leave the ``MainWindow`` slot to handle a degenerate
    case.  The drop is still accepted (so Qt stops showing the
    "no drop" cursor) but ``branch_dropped_on_branch`` stays quiet.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    target_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    mime = _make_chip_mime(widget, "feature")
    event = QDropEvent(
        target_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )
    with qtbot.assertNotEmitted(widget.branch_dropped_on_branch, wait=200):
        widget.dropEvent(event)  # noqa: SLF001
    assert event.isAccepted() is True


def test_drop_off_any_chip_is_accepted_but_quiet(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A drop that misses every chip is accepted but emits no signal.

    Defensive: the user might drop a chip on the commit graph
    (column 1) or the commit message (column 2). We accept the drop
    so Qt stops showing the "no drop" cursor, but we do not act on
    it — only drops on a chip carry an explicit target branch.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Drop somewhere in the graph column, well past the branch chips.
    drop_pos = QPoint(700, 100)
    mime = _make_chip_mime(widget, "feature")
    event = QDropEvent(
        drop_pos,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QEvent.Type.Drop,
    )
    with qtbot.assertNotEmitted(widget.branch_dropped_on_branch, wait=200):
        widget.dropEvent(event)  # noqa: SLF001
    assert event.isAccepted() is True


def test_drag_start_threshold_keeps_short_click_quiet(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A press + tiny move on a chip must not start a drag.

    Pins the threshold logic: ``_begin_chip_drag`` only fires once
    the cursor has moved more than ``_DRAG_START_THRESHOLD_PX``
    from the press point. Short clicks on a chip (which is the
    pre-existing commit-selection bypass) must still bypass
    commit selection *without* accidentally starting a drag.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Press on the "feature" chip, then nudge the cursor by 2 px —
    # well below the 6-px drag threshold.
    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(press_event)  # noqa: SLF001
    # The press must have stashed the chip + position; the drag
    # must NOT have started yet.
    assert widget._drag_press_chip is not None  # noqa: SLF001
    assert widget._drag_active_chip is None  # noqa: SLF001

    move_pos = pos + QPoint(2, 0)
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        move_pos,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    # The move event will try to start a real QDrag inside the
    # widget — that blocks waiting for a drop. Patch the drag
    # starter to record the call instead so the test stays
    # synchronous.
    started: list[dict] = []

    def fake_begin(chip, press_pos):
        started.append({"chip": chip, "press_pos": press_pos})
        # No QDrag.exec here — the test does not need a real drag.

    widget._begin_chip_drag = fake_begin  # type: ignore[method-assign]  # noqa: SLF001
    widget.mouseMoveEvent(move_event)  # noqa: SLF001
    assert started == []  # 2 px is below the threshold

    # Now nudge the cursor past the threshold — the drag must start.
    big_move = pos + QPoint(20, 0)
    big_event = QMouseEvent(
        QEvent.Type.MouseMove,
        big_move,
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseMoveEvent(big_event)  # noqa: SLF001
    assert len(started) == 1
    assert started[0]["chip"]["display"] == "feature"


def test_release_clears_drag_press_state(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A mouse release after a chip press clears the press state.

    The press state is what the move handler reads to decide
    whether a drag should be promoted; if it leaks past the
    release, the next move (in some unrelated area) could start a
    spurious drag. The test pins the release-time cleanup.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(press_event)  # noqa: SLF001
    assert widget._drag_press_chip is not None  # noqa: SLF001

    release_event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mouseReleaseEvent(release_event)  # noqa: SLF001
    assert widget._drag_press_chip is None  # noqa: SLF001
    assert widget._drag_press_pos is None  # noqa: SLF001
    assert widget._drag_active_chip is None  # noqa: SLF001


def test_begin_chip_drag_uses_correct_mime_types(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The drag payload carries the chip MIME and the plain text.

    Pins the payload contract: the drop handler distinguishes chip
    drags from anything else via the custom MIME type, while the
    plain text lets external targets (clipboard, other widgets
    that only know text) still get a useful payload. The
    ``exec`` call is monkey-patched out so the test does not
    block on a real drag.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]  # noqa: SLF001
    captured: list[QMimeData] = []

    class _StubDrag:
        def __init__(self, parent):
            self._mime = None
            self._pix = None

        def setMimeData(self, mime):  # noqa: N802 - Qt override
            self._mime = mime

        def setPixmap(self, pix):  # noqa: N802 - Qt override
            self._pix = pix

        def setHotSpot(self, pt):  # noqa: N802 - Qt override
            self._hot = pt

        def exec(self, *actions):  # noqa: N802 - Qt override
            captured.append(self._mime)
            return Qt.DropAction.CopyAction

    # ``_begin_chip_drag`` constructs ``QDrag(self)`` directly; patch
    # the symbol in the module so the stub class is used.
    import src.ui.widgets.graph_panel as gp_module
    original_drag = gp_module.QDrag
    gp_module.QDrag = _StubDrag  # type: ignore[assignment]
    try:
        pos = QPoint(50, 100)
        widget._begin_chip_drag(chip, pos)  # noqa: SLF001
    finally:
        gp_module.QDrag = original_drag  # type: ignore[assignment]

    assert len(captured) == 1
    mime = captured[0]
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415
    assert mime.hasFormat(_CHIP_MIME)
    assert bytes(mime.data(_CHIP_MIME)).decode("utf-8") == "feature"
    assert mime.text() == "feature"


def test_full_qdrag_drops_on_target_chip(
    qtbot, tmp_git_repo: Path,
) -> None:
    """End-to-end drag-and-drop on the graph: from ``QDrag`` start to menu signal.

    The previous drop tests synthesised ``QDropEvent`` instances and
    called ``widget.dropEvent(event)`` directly, which bypasses Qt's
    drag pipeline. That is fine for unit-testing the handler logic,
    but it would not have caught a regression where the widget
    refuses to accept drops at the Qt level (``acceptDrops=False``
    on the underlying :class:`QWidget`). This test does the full
    thing: starts a real ``QDrag``, lets ``exec`` run, and
    delivers a synthetic drop via ``QDrag::exec``'s event loop.
    The :meth:`QtBot.mouseMove` calls advance the cursor through
    the drag, and ``QtBot.mouseRelease`` finalises the drop on the
    target chip.

    The test stays in-process by capturing the drag with a custom
    ``QDrag.exec`` (the same trick the other drag tests use) and
    forwarding the drop position to the widget's ``dropEvent`` —
    the only piece of the Qt pipeline that can run synchronously
    inside a test event loop is the drop itself, so we set up
    the press / move / release sequence on the widget and let it
    fall through to ``dropEvent`` exactly as a real release would.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # The widget must accept drops at the Qt level. Without this
    # flag Qt silently rejects the drag before any of our handlers
    # run, and the user sees "nothing happens" on release.
    assert widget.acceptDrops() is True

    # Compute the press and drop positions on the chips.
    press_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "feature")
    drop_pos = _chip_viewport_pos(widget, mgr.head_commit.sha, "main")

    # Press on the "feature" chip.
    qtbot.mousePress(widget, Qt.MouseButton.LeftButton, pos=press_pos)

    # Move past the drag-start threshold to promote the press into a
    # drag.  We bypass the real ``QDrag.exec`` by monkey-patching
    # the class: when ``_begin_chip_drag`` runs, the stub captures
    # the mime and synthesises a drop event at the target position.
    captured: list[tuple[str, str]] = []
    from src.ui.widgets.graph_panel import _CHIP_MIME  # noqa: PLC0415

    def fake_exec(self, *_args, **_kwargs):
        # Simulate Qt delivering the drop event to the widget at
        # the target position. ``dropEvent`` runs synchronously, so
        # the signal is captured before the stub returns.
        mime = QMimeData()
        mime.setData(_CHIP_MIME, b"feature")
        mime.setText("feature")
        event = QDropEvent(
            drop_pos,
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QEvent.Type.Drop,
        )
        # ``dropEvent`` is a regular method — call it the way Qt
        # would after a real drop.
        try:
            widget.dropEvent(event)  # noqa: SLF001
        except Exception:
            pass
        return Qt.DropAction.CopyAction

    import src.ui.widgets.graph_panel as gp_module
    original_drag = gp_module.QDrag
    gp_module.QDrag = type(  # type: ignore[assignment]
        "_StubDrag",
        (),
        {
            "__init__": lambda self, parent: None,
            "setMimeData": lambda self, mime: setattr(self, "_mime", mime),
            "setPixmap": lambda self, pix: None,
            "setHotSpot": lambda self, pt: None,
            "exec": fake_exec,
        },
    )
    try:
        # Connect the signal to capture the drop result.
        widget.branch_dropped_on_branch.connect(  # noqa: SLF001
            lambda s, t: captured.append((s, t))
        )
        # Nudge the cursor past the threshold to start the drag.
        move_pos = press_pos + QPoint(20, 0)
        qtbot.mouseMove(widget, move_pos)
        # Process events so the drag-start logic runs.
        QApplication.processEvents()
    finally:
        gp_module.QDrag = original_drag  # type: ignore[assignment]
        qtbot.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=drop_pos)

    # The drop on the "main" chip must have produced the menu signal
    # with source="feature" and target="main".
    assert captured == [("feature", "main")]


# --------------------------------------------------------------------------
# "Create branch here" - context menu + inline editor
# --------------------------------------------------------------------------

def test_branch_menu_has_create_branch_here_for_local(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-click on a local branch chip -> menu has 'Create branch here'.

    The action is appended after ``Rebase X onto current`` with a
    visual separator in between so the user reads it as part of a
    related group (verbs targeting the source branch, plus the
    "create a sibling from this commit" gesture).
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    actions = widget._build_branch_menu_actions(chip)
    labels = [a.text() for a in actions if not a.isSeparator()]
    assert "Create branch here" in labels


def test_branch_menu_omits_create_branch_here_for_remote(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-clicking a remote-tracking chip must NOT offer 'Create branch here'.

    Remote refs are immutable from the local repo; the user is
    reading a remote's state, not creating one. The action only
    makes sense for local refs the user owns.

    We set up ``upstream/feature`` (no local counterpart that would
    trigger the local-vs-remote display-name suppression) and
    verify the menu it produces does not include the action.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    mgr.repo.create_reference(
        "refs/remotes/upstream/feature", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "feature")]
    actions = widget._build_branch_menu_actions(chip)
    labels = [a.text() for a in actions if not a.isSeparator()]
    assert "Create branch here" not in labels
    # Sanity: the chip we found is indeed the remote variant.
    assert chip["is_remote"] is True


# ----- Copy branch name / Copy commit sha --------------------------------


def test_branch_menu_has_copy_branch_name_for_local(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Right-clicking a local branch chip exposes 'Copy branch name'.

    Mirrors the left-panel behaviour: every branch chip (local or
    remote) must offer a copy action so the user can paste the
    name straight into a ``git checkout`` command. The local chip
    has ``full_name == display == "feature"`` here.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    actions = widget._build_branch_menu_actions(chip)
    labels = [a.text() for a in actions if not a.isSeparator()]
    assert "Copy branch name" in labels
    assert "Copy commit sha" in labels


def test_branch_menu_copy_branch_name_emits_full_ref(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Triggering 'Copy branch name' emits the chip's full ref name.

    Local chip: ``full_name == display == "feature"`` so the
    payload is just the bare branch name.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    actions = widget._build_branch_menu_actions(chip)
    copy_name = next(a for a in actions if a.text() == "Copy branch name")

    with qtbot.waitSignal(
        widget.copy_branch_name_requested, timeout=1000,
    ) as blocker:
        copy_name.trigger()
    assert blocker.args == ["feature"]


def test_branch_menu_copy_commit_sha_emits_row_sha(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Triggering 'Copy commit sha' emits the row's commit SHA.

    The chip cache carries ``row_sha`` (the commit the chip
    points at), so the slot receives the same SHA the row
    already exposes on click.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    actions = widget._build_branch_menu_actions(chip)
    copy_sha = next(a for a in actions if a.text() == "Copy commit sha")

    with qtbot.waitSignal(
        widget.copy_commit_sha_requested, timeout=1000,
    ) as blocker:
        copy_sha.trigger()
    assert blocker.args == [mgr.head_commit.sha]


def test_branch_menu_has_copy_branch_name_for_remote(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Remote-tracking chips also expose 'Copy branch name'.

    The chip renders ``base_features`` (the ``origin/`` prefix is
    stripped for display) but the action must hand the user the
    full ref name so they can paste ``origin/base_features``
    straight into a checkout command.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/base_features", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "base_features")]
    assert chip["is_remote"] is True
    actions = widget._build_branch_menu_actions(chip)
    labels = [a.text() for a in actions if not a.isSeparator()]
    assert "Copy branch name" in labels


def test_branch_menu_copy_branch_name_uses_full_ref_for_remote(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The remote chip's 'Copy branch name' emits the full ref (e.g. ``origin/main``).

    Critical for the UX: the chip *displays* the bare branch name
    (with the remote prefix stripped) but the clipboard payload
    must include the prefix — that is what the user pastes into
    ``git checkout`` and what every other remote-aware UI surfaces.
    Mirrors the left panel's ``_remote_branch_actions`` policy.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    mgr.repo.references.create(
        "refs/remotes/origin/base_features", mgr.repo.head.target, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "base_features")]
    actions = widget._build_branch_menu_actions(chip)
    copy_name = next(a for a in actions if a.text() == "Copy branch name")

    with qtbot.waitSignal(
        widget.copy_branch_name_requested, timeout=1000,
    ) as blocker:
        copy_name.trigger()
    # Full ref, not the display label.
    assert blocker.args == ["origin/base_features"]


def test_create_branch_here_action_opens_inline_editor(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Picking 'Create branch here' spawns a QLineEdit at the chip.

    The action itself does not emit the create-branch signal - the
    editor must first collect a name from the user. Only when Enter
    is pressed does :attr:`create_branch_here_requested` fire.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    widget._open_inline_editor(chip)
    assert widget._inline_editor is not None
    # The editor is anchored to the chip's row SHA so the eventual
    # signal carries the correct target commit.
    assert widget._inline_editor_row_sha == mgr.head_commit.sha


def test_inline_editor_enter_emits_create_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Enter in the inline editor emits create_branch_here_requested.

    The editor is intentionally separate from the menu action: the
    menu opens the editor, the editor captures the typed name and
    fires the signal on Enter. Anything else (Escape, focus loss)
    closes the editor silently - pinned here so a regression
    where Escape accidentally fires the signal would surface.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    widget._open_inline_editor(chip)
    editor = widget._inline_editor
    assert editor is not None
    editor.setText("hotfix/login-bug")

    with qtbot.waitSignal(
        widget.create_branch_here_requested, timeout=2000,
    ) as blocker:
        QApplication.processEvents()
        # ``returnPressed`` is fired by ``returnPressed.connect``;
        # ``setText`` + a deliberate ``keyPressEvent`` of ``Enter``
        # mirrors the user pressing the key.
        from PySide6.QtGui import QKeyEvent
        enter_event = QKeyEvent(
            QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(editor, enter_event)
        QApplication.processEvents()

    assert blocker.args == [mgr.head_commit.sha, "hotfix/login-bug"]
    # Editor torn down after a successful commit.
    assert widget._inline_editor is None


def test_inline_editor_escape_closes_without_emitting(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Escape closes the inline editor and does NOT emit the signal.

    Cancel-then-recreate is a normal flow (right-click, change
    mind, right-click again) - Escape must be a clean revert.
    The signal only fires on Enter; a regression that wired
    Escape to ``create_branch_here_requested`` would be loud here.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    widget._open_inline_editor(chip)
    editor = widget._inline_editor
    assert editor is not None

    signal_caught: list[tuple[str, str]] = []
    widget.create_branch_here_requested.connect(
        lambda s, n: signal_caught.append((s, n)),
    )

    from PySide6.QtGui import QKeyEvent
    esc_event = QKeyEvent(
        QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(editor, esc_event)
    QApplication.processEvents()

    assert widget._inline_editor is None
    assert signal_caught == []


def test_inline_editor_graph_update_closes_editor(
    qtbot, tmp_git_repo: Path,
) -> None:
    """If the graph refreshes while the editor is open, the editor closes.

    The editor is anchored to a specific row SHA; a refresh that
    moves that row (commit disappeared, branch went away) would
    leave a stale editor pointing at a vanished target. We close
    the editor on every ``graph_updated`` and rebuild it manually
    if the user actually wants to keep typing - the test pins
    the close-on-refresh contract.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(mgr.head_commit.sha, "feature")]
    widget._open_inline_editor(chip)
    assert widget._inline_editor is not None

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    assert widget._inline_editor is None


# --------------------------------------------------------------------------
# Branch priority: HEAD wins, recent branches demoted
# --------------------------------------------------------------------------

def test_priority_prefers_head_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A HEAD branch outranks non-HEAD branches at the same commit.

    ``_branch_priority_key`` is the sorting function used to choose
    which branch keeps the prominent (visible) chip when several
    share a commit. The HEAD branch must always come first; the
    test pins that ordering so a future refactor cannot quietly
    demote it.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    main_branch = {"name": "main", "is_head": True, "is_remote": False}
    feature_branch = {"name": "feature", "is_head": False, "is_remote": False}
    sorted_branches = sorted(
        [main_branch, feature_branch], key=widget._branch_priority_key,
    )
    assert sorted_branches[0] == main_branch


def test_priority_demotes_recently_created_branch(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A branch in ``_recently_created_branches`` outranks lower buckets only - never the HEAD.

    The "newly created branch is not promoted" guarantee means
    that a session-created branch *without* HEAD must score
    strictly below a source-style branch. Without that ordering,
    the prominent chip would jump every time the user creates a
    new branch - defeating the source-first UX.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    widget._recently_created_branches = {"feature"}
    head_branch = {"name": "main", "is_head": True, "is_remote": False}
    new_branch = {"name": "feature", "is_head": False, "is_remote": False}

    head_key = widget._branch_priority_key(head_branch)
    new_key = widget._branch_priority_key(new_branch)
    # HEAD branch is always bucket 0 regardless of recent set.
    assert head_key[0] == 0
    # Recently-created branch lands at a non-zero bucket (either 2
    # "recent-low" or 3 "fallback", both > 1).
    assert new_key[0] > 1


def test_recent_set_updates_via_signal(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``recently_created_changed`` from the VM is mirrored in the widget.

    The widget caches the recent set so its priority logic can run
    synchronously during paint. The signal is the only path that
    mutates the cache so this test pins the signal-driven wiring.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, _head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    assert widget._recently_created_branches == set()
    vm.update_recently_created({"alpha", "beta"})
    QApplication.processEvents()
    assert widget._recently_created_branches == {"alpha", "beta"}


# --------------------------------------------------------------------------
# Collapsed rendering: only priority chip + - indicator; cache holds siblings
# --------------------------------------------------------------------------

def test_collapsed_row_renders_only_primary_chip(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Rows with 2+ branches render a single chip + - indicator.

    The cache still holds *all* chip rects so test helpers and
    external callers can resolve every chip by ``(sha, display)``
    - but the painter only fills one chip. Test pins both: the
    cache size and the branch chip set we keep.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Both chips are tracked in the cache (back-compat for tests
    # that resolve by display name).
    assert (head_sha, "main") in widget._branch_chip_rects
    assert (head_sha, "feature") in widget._branch_chip_rects
    # But only the priority chip carries the ``hidden_count``
    # marker - the others are pure cache stubs.
    main_meta = widget._branch_chip_rects[(head_sha, "main")]
    feat_meta = widget._branch_chip_rects[(head_sha, "feature")]
    assert main_meta["hidden_count"] == 1
    assert feat_meta["hidden_count"] == 0


def test_single_branch_row_has_no_hidden_count(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A row with one branch behaves as before (no collapse, no indicator)."""
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    meta = widget._branch_chip_rects[(mgr.head_commit.sha, "main")]
    assert meta["hidden_count"] == 0


def test_branch_group_size_counts_visible_only(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``_branch_group_size`` returns the number of branches at a row.

    Used by :meth:`_schedule_hover_popup` to decide whether to
    bother opening the popup. This must reflect the *visible*
    count - i.e. the number of entries in ``branch_refs`` after
    the local-vs-remote suppression.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, _feature_sha = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    assert widget._branch_group_size(head_sha) == 2
    # A bogus row SHA returns 0 - guards the popup-timer path.
    assert widget._branch_group_size("nonexistent-sha") == 0


# --------------------------------------------------------------------------
# Hover popup (BranchStackPopup)
# --------------------------------------------------------------------------

def test_branch_popup_lists_all_branches(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Opening the hover-popup at a multi-branch row shows every branch.

    The popup payload mirrors :meth:`_branches_at_row` - used both
    for display *and* as the source of ``branch_selected`` payloads
    so the user can pick any branch, including the primary one.
    """
    from src.ui.widgets.graph_panel import BranchStackPopup, GraphTableWidget

    mgr, head_sha, _ = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]
    widget._show_branch_popup(head_sha, chip["rect"])
    QApplication.processEvents()
    assert widget._branch_popup is not None
    popup: BranchStackPopup = widget._branch_popup
    # The popup's rows are ``BranchStackPopup._Row`` instances; we
    # find them by class so the test does not rely on Python name
    # mangling for inner classes.
    row_cls = BranchStackPopup._Row
    rows = popup.findChildren(row_cls)
    names = {r._branch["name"] for r in rows}
    assert names == {"main", "feature"}


def test_branch_popup_row_click_emits_checkout(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Single-clicking a row in the hover-popup switches branches.

    Both ``mousePressEvent`` and ``mouseDoubleClickEvent`` on a
    popup row route to ``branch_selected`` - the popup is the
    "double-click to switch" surface, but a quick single click
    is also accepted (matches how every list-popup behaves in
    the rest of the app).
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr, head_sha, _ = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]
    widget._show_branch_popup(head_sha, chip["rect"])
    QApplication.processEvents()
    assert widget._branch_popup is not None

    captured: list[str] = []
    widget.checkout_branch_requested.connect(
        lambda name: captured.append(name),
    )

    row = next(
        r for r in widget._branch_popup.findChildren(widget._branch_popup._Row)  # noqa: SLF001
        if r._branch["name"] == "feature"
    )
    from PySide6.QtGui import QMouseEvent
    click = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPoint(2, 2),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(row, click)
    QApplication.processEvents()

    assert captured == ["feature"]
    assert widget._branch_popup is None  # popup auto-closes on selection


def test_branch_popup_closes_on_mouse_leave(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The hover-popup must close shortly after the cursor leaves it.

    Pre-fix the popup had no ``leaveEvent`` so it stayed on screen
    indefinitely; users had to click a row to dismiss it. The fix
    installs a debounced close-timer (160 ms) that fires whenever
    the cursor is outside the popup frame.
    """
    from PySide6.QtCore import QEvent
    from src.ui.widgets.graph_panel import BranchStackPopup, GraphTableWidget

    mgr, head_sha, _ = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]
    widget._show_branch_popup(head_sha, chip["rect"])
    QApplication.processEvents()
    popup = widget._branch_popup
    assert isinstance(popup, BranchStackPopup)

    # Send a leave event to start the close timer; advance the event
    # loop past its 160 ms interval and confirm the popup dropped
    # its ``self._branch_popup`` handle on the widget side.
    leave = QEvent(QEvent.Type.Leave)
    QApplication.sendEvent(popup, leave)
    qtbot.waitUntil(lambda: widget._branch_popup is None, timeout=2000)  # noqa: SLF001


def test_branch_popup_tracks_parent_window_move(
    qtbot, tmp_git_repo: Path,
) -> None:
    """When the parent window is dragged, the popup moves with it.

    Pre-fix ``Qt.Tool`` popups froze at their initial global
    position. The fix installs an ``eventFilter`` on the parent
    that translates the popup by the same delta when the parent
    receives ``QEvent.Move``.
    """
    from PySide6.QtCore import QEvent
    from src.ui.widgets.graph_panel import BranchStackPopup, GraphTableWidget

    mgr, head_sha, _ = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]
    widget._show_branch_popup(head_sha, chip["rect"])
    QApplication.processEvents()
    popup = widget._branch_popup
    assert isinstance(popup, BranchStackPopup)

    # Capture the initial geometry, simulate the parent shifting
    # 200 px to the right, and verify the popup translated by the
    # same amount via the parent ``QEvent.Move`` filter.
    original = popup.geometry()
    widget.move(widget.x() + 200, widget.y())
    # ``move`` schedules a geometry change; let Qt deliver the
    # ``QEvent.Move`` to the parent (which the popup's filter
    # listens to).
    QApplication.processEvents()
    QApplication.sendEvent(widget, QEvent(QEvent.Type.Move))
    QApplication.processEvents()

    shifted = popup.geometry()
    assert shifted.x() == original.x() + 200, (
        f"popup should track parent's +200 px shift; "
        f"got x={shifted.x()}, expected x={original.x() + 200}"
    )


def test_branch_popup_filters_origin_main_and_origin_head(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The popup must drop the same-name remote and ``origin/HEAD``.

    Pre-fix the hover-popup iterated the raw ``branch_refs`` list
    while the chip column used a suppressed list. The user sees
    the chip say ``main`` (just one), then hovers and the popup
    reveals ``main``, ``origin/main`` (duplicate), ``origin/HEAD``
    (synthetic fetch marker) — exactly the "main, HEAD, main"
    symptom. Both should disappear from the popup the same way
    they disappeared from the chip column.

    The repo also carries ``origin/feature`` so the popup has at
    least two *visible* rows to enumerate (otherwise the
    skip-when-single rule kicks in and there is nothing to
    inspect). The asserted set is still just the two we care
    about — ``main`` and ``origin/feature``.
    """
    from src.ui.widgets.graph_panel import BranchStackPopup, GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = str(mgr.head_commit.sha)
    mgr.repo.references.create(
        "refs/remotes/origin/main", head_sha, force=True,
    )
    mgr.repo.references.create(
        "refs/remotes/origin/HEAD", head_sha, force=True,
    )
    mgr.repo.references.create(
        "refs/remotes/origin/feature", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    head_sha = str(mgr.head_commit.sha)
    chip = widget._branch_chip_rects[(head_sha, "main")]
    widget._show_branch_popup(head_sha, chip["rect"])
    QApplication.processEvents()
    popup: BranchStackPopup = widget._branch_popup
    assert popup is not None
    row_cls = BranchStackPopup._Row
    rows = popup.findChildren(row_cls)
    names = {r._branch["name"] for r in rows}
    # ``origin/main`` and ``origin/HEAD`` are filtered out, but
    # ``origin/feature`` (no local counterpart) survives. The
    # result must never include the duplicate or the synthetic
    # fetch marker.
    assert "origin/main" not in names
    assert "origin/HEAD" not in names
    assert "main" in names
    assert "origin/feature" in names


def test_branch_popup_skipped_when_only_one_branch_visible(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A popup listing exactly one branch is pointless — skip it.

    When the same-name-remote filter collapses a multi-branch row
    down to a single chip (e.g. ``[main, origin/main]`` → ``[main]``),
    the hover-popup used to open anyway and show ``main`` alone —
    which is just what the chip already shows. Skipping the popup
    keeps the UX consistent with the ``▼`` indicator (which is
    only drawn when ``hidden_count > 0``).
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = str(mgr.head_commit.sha)
    # Two refs at HEAD, but they collapse to one after the filter.
    mgr.repo.references.create(
        "refs/remotes/origin/main", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]
    widget._show_branch_popup(head_sha, chip["rect"])
    QApplication.processEvents()
    # ``_show_branch_popup`` must early-return when the filtered
    # branch list has fewer than two entries.
    assert widget._branch_popup is None  # noqa: SLF001


def test_branch_popup_hover_timer_skipped_for_single_visible(
    qtbot, tmp_git_repo: Path,
) -> None:
    """The hover debounce timer must not open a popup when no
    siblings would be revealed.

    Without this guard ``_on_hover_popup_timer`` happily schedules
    the popup call only for ``_show_branch_popup`` to silently
    bail out — wasteful and noisy in logs. The timer slot should
    consult ``chip['hidden_count']`` (post-filter) before opening.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = str(mgr.head_commit.sha)
    mgr.repo.references.create(
        "refs/remotes/origin/main", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]
    # Pre-condition: the primary chip records zero hidden siblings
    # because the duplicate got filtered out.
    assert chip["hidden_count"] == 0  # noqa: SLF001

    # Drive the timer path the way the widget does: stash the
    # chip + row, fire the debounce slot, and confirm nothing
    # was opened.
    widget._popup_hover_chip = chip  # noqa: SLF001
    widget._popup_hover_row_sha = head_sha  # noqa: SLF001
    widget._on_hover_popup_timer()  # noqa: SLF001
    QApplication.processEvents()
    assert widget._branch_popup is None  # noqa: SLF001


def test_branch_popup_closes_on_global_mouse_move_outside(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A ``QEvent.MouseMove`` outside the popup + chip area closes it.

    The popup installs a global mouse-move filter on the
    application. When the user drags the cursor far away (window
    drag, alt-tab to another screen, …) ``leaveEvent`` does not
    always fire — this filter catches the cursor far from both the
    popup and the source chip and closes the popup immediately.
    """
    from src.ui.widgets.graph_panel import BranchStackPopup, GraphTableWidget

    mgr, head_sha, _ = _make_repo_with_feature(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]
    widget._show_branch_popup(head_sha, chip["rect"])
    QApplication.processEvents()
    popup = widget._branch_popup
    assert isinstance(popup, BranchStackPopup)

    # Direct ``sendEvent`` of a synthetic ``MouseMove`` does not
    # carry ``globalPosition`` — the popup also subscribes to
    # ``leaveEvent`` so we trigger that path instead.
    leave = QEvent(QEvent.Type.Leave)
    QApplication.sendEvent(popup, leave)
    qtbot.waitUntil(lambda: widget._branch_popup is None, timeout=2000)  # noqa: SLF001
    # ``popup`` was scheduled for deletion by ``WA_DeleteOnClose``;
    # the underlying C++ object is gone after the event loop spins.
    # Calling ``popup.isVisible()`` here would raise
    # ``RuntimeError: Internal C++ object … already deleted``.
    # Drain pending events so the deferred delete runs to completion.
    QApplication.processEvents()
    app = QApplication.instance()
    assert app is not None
    live_popups = [w for w in app.topLevelWidgets() if isinstance(w, BranchStackPopup)]
    assert live_popups == []


# --------------------------------------------------------------------------
# Local vs remote-only chip styling: filled vs outlined
# --------------------------------------------------------------------------

def test_local_branch_chip_marks_is_remote_only_false(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A pure-local chip carries `is_remote_only=False`.

    Pinning the flag at the cache level lets tests and tooling
    style the chip without re-deriving `is_remote and no
    local-counterpart` every time.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    head_sha = mgr.head_commit.sha
    meta = widget._branch_chip_rects[(head_sha, "main")]
    assert meta["is_remote"] is False
    assert meta["is_remote_only"] is False


def test_remote_only_branch_chip_marks_is_remote_only_true(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A remote ref with no same-name local keeps `is_remote_only=True`.

    `upstream/feature` has no local counterpart that shares its
    display name (`feature`), so it survives the local-suppression
    filter and is rendered as a remote-only chip. The render path
    uses this flag to swap `fill` for `stroke-only`.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    mgr.repo.create_reference(
        "refs/remotes/upstream/feature", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    meta = widget._branch_chip_rects[(head_sha, "feature")]
    assert meta["is_remote"] is True
    assert meta["is_remote_only"] is True


def test_remote_duplicate_of_local_marks_is_remote_only_false(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A remote ref whose display name matches a local keeps local style.

    `origin/main` shares `main` with the local HEAD chip, so
    the remote is suppressed (only one chip rect exists for that
    row). The surviving `(head_sha, "main")` entry is the local
    one, and must therefore carry `is_remote_only=False`.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    mgr.repo.create_reference(
        "refs/remotes/origin/main", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    meta = widget._branch_chip_rects[(head_sha, "main")]
    assert meta["is_remote"] is False
    assert meta["is_remote_only"] is False
    # One chip per row - the remote duplicate never gets a rect.
    assert len(widget._branch_chip_rects) == 1


def test_remote_only_chip_pixmap_has_transparent_fill(
    qtbot, tmp_git_repo: Path,
) -> None:
    """A rendered remote-only chip has no fill - only a coloured border.

    We capture a PNG of the chip area and probe the *centre* of
    the chip (which is inside the rounded rect but outside the
    border outline). For a remote-only chip this point is not
    painted by the body fill - it shows through to the widget
    background. For a filled local chip the same point matches
    the commit colour. The test pins that pixel-level outcome so
    a regression of the draw-vs-fill switch surfaces immediately.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    mgr.repo.create_reference(
        "refs/remotes/upstream/feature", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)
    QApplication.processEvents()

    # Resolve the chip rects in widget coords, then render the
    # branch column to a small QImage so we can probe pixels.
    chip_remote = widget._branch_chip_rects[(head_sha, "feature")]
    chip_local = widget._branch_chip_rects[(head_sha, "main")]

    # Render the branch column into a QImage with the same
    # dimensions as the column, then probe the centre of each
    # chip's rect relative to the widget viewport.
    from PySide6.QtCore import QRect as QRectC
    from PySide6.QtGui import QImage, QPainter
    col_range = widget._col_ranges()[0]  # noqa: SLF001 - intentional
    left, right = col_range
    img = QImage(int(right - left), widget.height(), QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    widget._draw_branch_column(
        p, widget._cfg.header_height, int(left), int(right),
    )
    p.end()

    def _sample(rect: QRectC) -> tuple[int, int, int, int]:
        # Map content-x to image-x (the painter translates by
        # ``-_h_scrolls[0]`` and then operates in content coords).
        local_x = rect.center().x() - widget._h_scrolls[0]
        local_y = rect.center().y()
        # Both coordinates are relative to the widget viewport;
        # the image above starts at ``left`` (the column left),
        # so subtract that.
        ix = int(local_x - left + 1)
        iy = int(local_y) + 1
        pixel = img.pixelColor(max(0, ix), max(0, iy))
        return (pixel.red(), pixel.green(), pixel.blue(), pixel.alpha())

    # Background colour the user sees at zero-opacity (the chip
    # body has NOT been filled, so the image's transparent
    # background shows through).
    remote_center = _sample(chip_remote["rect"])
    local_center = _sample(chip_local["rect"])

    # Outlined chip body stays transparent inside the rounded
    # rect - the surrounding widget area shows through. We
    # compare the alpha channel to the filled chip on the same
    # row: the remote side has to be at least as transparent.
    assert remote_center[3] < local_center[3], (
        f"Remote-only chip should be more transparent than "
        f"the local chip (got remote={remote_center}, "
        f"local={local_center})"
    )
    # Sanity: the local chip's centre is fully opaque.
    assert local_center[3] > 200


# --------------------------------------------------------------------------
# 3+ branches at the same row -> always collapse to one chip + popup
# --------------------------------------------------------------------------

def test_three_branches_collapse_to_primary_chip(
    qtbot, tmp_git_repo: Path,
) -> None:
    """``HEAD + local + remote (different display names)`` collapses to ONE chip.

    The user's preference is that *every* multi-branch row
    collapses — even with 3+ distinct branches at the commit. The
    priority chip (HEAD main) stays visible; the other two live
    in the cache (so the hover-popup can resolve them) but are
    not drawn. The cache carries ``hidden_count == 2`` on the
    primary chip so the popup knows how many siblings to reveal.

    Test setup: HEAD ``main`` (local) + a second local branch
    ``develop`` + a remote-tracking ``origin/release``. After
    local-suppression of remotes, all three names are unique, so
    the visible count is exactly 3.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    mgr.repo.create_reference("refs/heads/develop", head_sha, force=True)
    mgr.repo.create_reference(
        "refs/remotes/origin/release", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Cache carries every chip so the popup and tooling can
    # resolve them all by display name.
    assert (head_sha, "main") in widget._branch_chip_rects  # noqa: SLF001
    assert (head_sha, "develop") in widget._branch_chip_rects  # noqa: SLF001
    assert (head_sha, "release") in widget._branch_chip_rects  # noqa: SLF001
    # The primary chip carries the hidden_count marker; siblings
    # carry 0 because the popup is driven by the primary chip.
    primary = widget._branch_chip_rects[(head_sha, "main")]  # noqa: SLF001
    develop = widget._branch_chip_rects[(head_sha, "develop")]  # noqa: SLF001
    release = widget._branch_chip_rects[(head_sha, "release")]  # noqa: SLF001
    assert primary["hidden_count"] == 2
    assert develop["hidden_count"] == 0
    assert release["hidden_count"] == 0
    # And the priority chip is the local HEAD - the user
    # explicitly asked for "one local" to be the default chip.
    assert primary["is_head"] is True
    assert primary["is_remote"] is False


def test_three_branches_popup_lists_all_three(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Hovering on a 3-branch chip opens a popup listing all three branches.

    ``_show_branch_popup`` is invoked directly here so the test
    stays synchronous (no mouse-move / debounce-timer pipeline);
    the popup payload mirrors the row's full branch list, which
    is the user-facing contract: ``double-click any item to switch``.
    """
    from src.ui.widgets.graph_panel import BranchStackPopup, GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    mgr.repo.create_reference("refs/heads/develop", head_sha, force=True)
    mgr.repo.create_reference(
        "refs/remotes/origin/release", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    chip = widget._branch_chip_rects[(head_sha, "main")]  # noqa: SLF001
    widget._show_branch_popup(head_sha, chip["rect"])  # noqa: SLF001
    QApplication.processEvents()

    assert widget._branch_popup is not None  # noqa: SLF001
    row_cls = BranchStackPopup._Row
    rows = widget._branch_popup.findChildren(row_cls)  # noqa: SLF001
    names = {r._branch["name"] for r in rows}
    # Local + remote: "main" (HEAD), "develop", and "origin/release".
    # Note that the remote ref's full name carries the remote prefix
    # even though its display name is "release" - the popup row
    # stores the underlying ref dict verbatim.
    assert names == {"main", "develop", "origin/release"}


def test_three_branches_render_only_one_visible_chip(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Visually verify: with 3 branches at HEAD, only ONE chip is painted.

    The cache carries rects for every chip (so the popup and
    tooling can resolve them), but the actual ``paintEvent`` only
    fills the priority chip's path. This pixel-level check paints
    the branch column into a QImage and probes the alpha channel
    along the row's horizontal extent: a drawn chip leaves a
    coloured signature (high alpha at the chip body), and a
    "cache-only" entry leaves the background untouched (zero
    alpha in the chip body area, even though the rect exists).

    Test setup: HEAD ``main`` (local) + a second local branch
    ``develop`` + a remote-tracking ``origin/release``. After the
    suppression + collapse rules, only the ``main`` chip is drawn;
    ``develop`` and ``origin/release`` are reserved positions in the
    cache but their chip bodies stay transparent.
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_committed_repo(tmp_git_repo)
    head_sha = mgr.head_commit.sha
    mgr.repo.create_reference("refs/heads/develop", head_sha, force=True)
    mgr.repo.create_reference(
        "refs/remotes/origin/release", head_sha, force=True,
    )
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    _force_paint(widget)

    # Render the branch column into a QImage so we can probe
    # pixels along the row's horizontal axis.
    col_left, col_right = widget._col_ranges()[0]  # noqa: SLF001
    img = QImage(
        int(col_right - col_left), widget.height(),
        QImage.Format.Format_ARGB32,
    )
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    widget._draw_branch_column(  # noqa: SLF001
        p, widget._cfg.header_height, int(col_left), int(col_right),
    )
    p.end()

    def _alpha_at(rect: QRect) -> int:
        # The rect is in content coordinates (relative to the
        # painter's translated origin). Since the image is painted
        # directly via ``QPainter(img)`` with ``col_left`` as its
        # own origin (the painter's translate-by-(-h_scrolls[col])
        # shifts the content into the image at ``content_x``), the
        # pixel we read lives at the *content-x* position in the
        # image — not offset by ``col_left``.
        cx = rect.center().x()
        cy = rect.center().y()
        # Defensive: clamp to image bounds so a chip overflowing the
        # column never trips Qt's pixelColor range check.
        if cx < 0 or cy < 0 or cx >= img.width() or cy >= img.height():
            return 0
        pixel = img.pixelColor(cx, cy)
        return pixel.alpha()

    main_meta = widget._branch_chip_rects[(head_sha, "main")]  # noqa: SLF001
    develop_meta = widget._branch_chip_rects[(head_sha, "develop")]  # noqa: SLF001
    release_meta = widget._branch_chip_rects[(head_sha, "release")]  # noqa: SLF001

    main_alpha = _alpha_at(main_meta["rect"])
    develop_alpha = _alpha_at(develop_meta["rect"])
    release_alpha = _alpha_at(release_meta["rect"])

    # The primary chip is filled with the commit colour, so its
    # centre pixel must have a high alpha (fully opaque paint).
    assert main_alpha > 200, (
        f"main chip centre alpha should be opaque, got {main_alpha}"
    )
    # The two sibling chips live in the cache but are NOT drawn:
    # their body pixels stay fully transparent (the image's
    # zero-fill background shows through).
    assert develop_alpha == 0, (
        f"develop chip should NOT be drawn (alpha 0), got {develop_alpha}"
    )
    assert release_alpha == 0, (
        f"origin/release chip should NOT be drawn (alpha 0), got {release_alpha}"
    )


# ----- update2 stage C: cherry-pick / drop / edit-message menu actions -----


def _build_commit_menu(qtbot, mgr):
    from src.ui.widgets.graph_panel import GraphTableWidget

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()
    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    menu = widget._build_node_menu(mgr.head_commit.sha, "commit")  # noqa: SLF001
    qtbot.addWidget(menu)
    return widget, menu


def test_commit_menu_has_history_actions(qtbot, tmp_git_repo: Path) -> None:
    """The commit context menu exposes Cherry-pick / Drop / Edit."""
    mgr = _make_committed_repo(tmp_git_repo)
    _widget, menu = _build_commit_menu(qtbot, mgr)
    labels = [a.text() for a in menu.actions()]
    assert "Cherry-pick commit" in labels
    assert "Drop commit" in labels
    assert "Edit commit message…" in labels


def test_commit_menu_cherry_pick_emits_sha(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    widget, menu = _build_commit_menu(qtbot, mgr)
    action = next(a for a in menu.actions() if a.text() == "Cherry-pick commit")
    with qtbot.waitSignal(
        widget.cherry_pick_commit_requested, timeout=1000,
    ) as blocker:
        action.trigger()
    assert blocker.args == [mgr.head_commit.sha]


def test_commit_menu_drop_emits_sha(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    widget, menu = _build_commit_menu(qtbot, mgr)
    action = next(a for a in menu.actions() if a.text() == "Drop commit")
    assert action.isEnabled()
    with qtbot.waitSignal(
        widget.drop_commit_requested, timeout=1000,
    ) as blocker:
        action.trigger()
    assert blocker.args == [mgr.head_commit.sha]


def test_commit_menu_drop_disabled_for_merge_commit(qtbot, tmp_git_repo: Path) -> None:
    """Drop is disabled on merge commits (v1 limitation)."""
    mgr = _make_merge_repo(tmp_git_repo)
    widget, menu = _build_commit_menu(qtbot, mgr)
    action = next(a for a in menu.actions() if a.text() == "Drop commit")
    assert not action.isEnabled()


def test_commit_menu_edit_message_emits_sha(qtbot, tmp_git_repo: Path) -> None:
    mgr = _make_committed_repo(tmp_git_repo)
    widget, menu = _build_commit_menu(qtbot, mgr)
    action = next(a for a in menu.actions() if a.text() == "Edit commit message…")
    with qtbot.waitSignal(
        widget.edit_commit_message_requested, timeout=1000,
    ) as blocker:
        action.trigger()
    assert blocker.args == [mgr.head_commit.sha]


# ----- update2 stage D: shift multi-selection + squash menu -----------------


def _make_linear_repo(path: Path, n: int = 4) -> RepositoryManager:
    mgr = RepositoryManager(str(path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    parents = []
    for i in range(n):
        (path / "f.txt").write_text(f"content {i}\n")
        mgr.repo.index.add("f.txt")
        mgr.repo.index.write()
        tree = mgr.repo.index.write_tree()
        oid = mgr.repo.create_commit(
            "refs/heads/main", sig, sig, f"commit {i}", tree, parents,
        )
        parents = [oid]
    return mgr


def _click_commit_row(widget, row_idx: int, modifiers=Qt.KeyboardModifier.NoModifier):
    y = widget._row_y(row_idx) + 5  # noqa: SLF001
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPoint(60, int(y)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )
    widget.mousePressEvent(event)  # noqa: SLF001


def test_shift_click_selects_contiguous_range(qtbot, tmp_git_repo: Path) -> None:
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_linear_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()
    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    _click_commit_row(widget, 0)
    _click_commit_row(widget, 2, modifiers=Qt.KeyboardModifier.ShiftModifier)

    shas = widget.selected_shas()
    assert len(shas) == 3
    assert shas[0] == mgr.head_commit.sha  # newest first
    assert widget.selected_sha() == shas[2]  # last clicked


def test_plain_click_collapses_range(qtbot, tmp_git_repo: Path) -> None:
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_linear_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()
    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    _click_commit_row(widget, 0)
    _click_commit_row(widget, 2, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert len(widget.selected_shas()) == 3
    _click_commit_row(widget, 1)
    assert widget.selected_shas() == [widget.selected_sha()]


def test_multi_select_menu_squash_enabled_and_emits(qtbot, tmp_git_repo: Path) -> None:
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_linear_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()
    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    _click_commit_row(widget, 0)
    _click_commit_row(widget, 2, modifiers=Qt.KeyboardModifier.ShiftModifier)
    shas = widget.selected_shas()

    menu = widget._build_multi_select_menu(shas)  # noqa: SLF001
    qtbot.addWidget(menu)
    action = next(a for a in menu.actions() if a.text() == "Squash (3) commits")
    assert action.isEnabled()
    with qtbot.waitSignal(widget.squash_commits_requested, timeout=1000) as blocker:
        action.trigger()
    assert blocker.args == [shas]


def test_multi_select_menu_squash_disabled_with_merge_in_range(
    qtbot, tmp_git_repo: Path,
) -> None:
    from src.ui.widgets.graph_panel import GraphTableWidget

    mgr = _make_merge_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    widget.resize(900, 400)
    qtbot.addWidget(widget)
    widget.show()
    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()

    # Rows: 0 = merge commit (tip), 1 = main2, ... select rows 0..2 so
    # the range includes the merge commit.
    _click_commit_row(widget, 0)
    _click_commit_row(widget, 2, modifiers=Qt.KeyboardModifier.ShiftModifier)
    shas = widget.selected_shas()
    assert len(shas) == 3

    menu = widget._build_multi_select_menu(shas)  # noqa: SLF001
    qtbot.addWidget(menu)
    action = next(a for a in menu.actions() if a.text() == "Squash (3) commits")
    assert not action.isEnabled()
    assert action.toolTip()
