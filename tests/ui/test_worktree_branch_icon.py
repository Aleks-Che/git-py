"""A sibling checkout is visible on the graph, including clean worktrees and popups."""
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QLabel
from src.ui.widgets.graph_panel import BranchStackPopup, GraphTableWidget
from src.viewmodels.graph_viewmodel import GraphViewModel


@pytest.mark.parametrize("dirty", [False, True])
def test_worktree_branch_has_visible_tree_before_name_and_in_popup(
    qtbot, committed_repo, linked_worktree, dirty,
):
    repo = linked_worktree.repo
    head = repo.head.peel()
    tip = repo.create_commit("HEAD", head.author, head.committer, "linked tip",
                             head.tree_id, [head.id])
    branch = repo.head.shorthand
    if dirty:
        (Path(linked_worktree.path) / "hello.txt").write_bytes(b"work in progress\n")
    vm = GraphViewModel(committed_repo)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.resize(1100, 400)
    widget.show()
    vm.refresh_graph()
    qtbot.wait(10)
    widget.repaint()

    chip = widget._branch_chip_rects[(str(tip), branch)]
    assert chip["worktree_path"] == Path(linked_worktree.path).as_posix()
    main_chip = widget._branch_chip_rects[(committed_repo.head_commit.sha, "main")]
    assert main_chip["worktree_path"] is None

    # Check actual pixels in the leading icon slot, before the branch text.
    image = QImage(widget.width(), widget.height(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    widget._draw_branch_column(painter, widget._cfg.header_height, 0, widget.width())
    painter.end()
    rect = chip["rect"]
    icon_left = rect.left() + 5
    pixels = [image.pixelColor(x, y) for x in range(icon_left, icon_left + 10)
              for y in range(rect.center().y() - 5, rect.center().y() + 6)]
    assert sum(min(c.red(), c.green(), c.blue()) > 200 for c in pixels) >= 8
    # Overflow measurement includes the same slot, keeping long names reachable.
    row = next(row for row in widget._rows if row["sha"] == str(tip))
    assert widget._measure_branch_row(row["branch_refs"], widget.fontMetrics()) == rect.width() + 6

    # A second branch collapses the row; the popup must retain the marker.
    repo.create_branch("zz-same-commit", repo.head.peel())
    vm.refresh_graph()
    widget.repaint()
    chip = widget._branch_chip_rects[(str(tip), branch)]
    widget._show_branch_popup(str(tip), chip["rect"])
    popup = widget._branch_popup
    assert popup is not None
    rows = popup.findChildren(BranchStackPopup._Row)
    marked = next(row for row in rows if row._branch["name"] == branch)
    plain = next(row for row in rows if row._branch["name"] == "zz-same-commit")
    icon = marked.findChild(QLabel, "worktree-indicator")
    assert icon is not None and not icon.pixmap().isNull()
    assert marked.layout().itemAt(0).widget() is icon
    assert Path(linked_worktree.path).as_posix() in marked.toolTip()
    assert plain.findChild(QLabel, "worktree-indicator") is None
    with qtbot.waitSignal(widget.checkout_branch_requested) as clicked:
        qtbot.mouseClick(marked, Qt.MouseButton.LeftButton)
    assert clicked.args == [branch]
    widget.close()
