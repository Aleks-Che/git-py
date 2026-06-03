"""UI tests for the :class:`LeftPanel` references tree.

The tests run under ``pytest-qt`` (``QT_QPA_PLATFORM=offscreen`` for
headless CI). They drive the panel through a real
:class:`MainViewModel` bound to a real :class:`RepositoryManager` so
the data flow is exercised end-to-end. We assert against the
**public** ViewModel state (lists / current branch) and against the
on-disk repository — never against private widget attributes.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt
from src.core.repository import RepositoryManager
from src.ui.widgets.left_panel import LeftPanel
from src.viewmodels.main_viewmodel import MainViewModel


def _find_top_level(panel: LeftPanel, label: str):
    for i in range(panel.topLevelItemCount()):
        item = panel.topLevelItem(i)
        if item.text(0) == label:
            return item
    return None


def _find_child(group, text: str):
    for i in range(group.childCount()):
        child = group.child(i)
        if child.text(0) == text:
            return child
    return None


# ----- placeholder ---------------------------------------------------


def test_panel_shows_placeholder_without_repo(qtbot) -> None:
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    placeholder = _find_top_level(panel, "No repository opened")
    assert placeholder is not None
    assert placeholder.isDisabled()


# ----- populating the tree -------------------------------------------


def test_panel_shows_branches_after_open(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _find_top_level(panel, "Branches")
    assert branches is not None
    local = _find_child(branches, "Local")
    assert local is not None
    main_item = _find_child(local, "main  (HEAD)")
    assert main_item is not None


def test_panel_picks_up_new_branch_after_refresh(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    from src.core.operations import create_branch

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    create_branch(
        committed_repo,
        "topic",
        target_sha=committed_repo.head_commit.sha,
    )
    vm.branch_panel_view_model().refresh()

    branches = _find_top_level(panel, "Branches")
    local = _find_child(branches, "Local")
    assert _find_child(local, "topic") is not None


def test_panel_rebuilds_when_repository_changes(qtbot, tmp_git_repo: Path) -> None:
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()

    # Bind to a fresh repo and verify the placeholder is gone.
    mgr = RepositoryManager(str(tmp_git_repo))
    vm.set_repository(mgr)
    assert _find_top_level(panel, "No repository opened") is None
    assert _find_top_level(panel, "Branches") is not None

    # Unbind and verify the placeholder returns.
    vm.set_repository(None)
    assert _find_top_level(panel, "No repository opened") is not None


# ----- double-click --------------------------------------------------


def test_double_click_on_local_branch_checks_it_out(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    from src.core.operations import create_branch

    create_branch(
        committed_repo,
        "feature",
        target_sha=committed_repo.head_commit.parents[0],
    )
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _find_top_level(panel, "Branches")
    local = _find_child(branches, "Local")
    feature = _find_child(local, "feature")
    assert feature is not None

    panel.itemDoubleClicked.emit(feature, 0)
    assert committed_repo.repo.head.shorthand == "feature"


def test_double_click_on_tag_creates_branch_from_it(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    import time

    import pygit2

    sig = pygit2.Signature("t", "t@x", int(time.time()), 0)
    obj = committed_repo.repo.revparse_single("HEAD").peel(pygit2.Commit)
    committed_repo.repo.create_tag("v1", obj.id, pygit2.GIT_OBJECT_COMMIT, sig, "v1")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    tags = _find_top_level(panel, "Tags")
    v1 = _find_child(tags, "v1")
    assert v1 is not None
    panel.itemDoubleClicked.emit(v1, 0)
    assert any(b.name == "v1" for b in committed_repo.branches)


# ----- context menu --------------------------------------------------


def test_context_menu_has_checkout_on_local_branch(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _find_top_level(panel, "Branches")
    local = _find_child(branches, "Local")
    feature = _find_child(local, "feature")

    actions = panel._context_menu_actions(feature)  # noqa: SLF001
    labels = {a.text() for a in actions}
    assert "Checkout" in labels
    assert any("Create Branch from" in t for t in labels)
    assert "Rename…" in labels
    assert "Delete…" in labels


def test_context_menu_create_branch_invokes_main_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Right-click → Create Branch, monkeypatched QInputDialog, verify VM call."""
    from PySide6.QtWidgets import QInputDialog

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("topic", True)),
    )
    monkeypatch.setattr(
        vm, "create_branch",
        lambda name, target_sha=None: captured.append((name, target_sha)),
    )

    panel._prompt_create_branch(from_name="main")  # noqa: SLF001
    assert captured == [("topic", "main")]


def test_context_menu_rename_invokes_main_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    from PySide6.QtWidgets import QInputDialog
    from src.core.operations import create_branch

    create_branch(committed_repo, "old", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("renamed", True)),
    )
    monkeypatch.setattr(
        vm, "rename_branch",
        lambda old, new, force=False: captured.append((old, new)),
    )
    panel._prompt_rename("old")  # noqa: SLF001
    assert captured == [("old", "renamed")]


def test_context_menu_delete_invokes_main_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    from PySide6.QtWidgets import QMessageBox
    from src.core.operations import create_branch

    create_branch(committed_repo, "doomed", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        ),
    )
    monkeypatch.setattr(
        vm, "delete_branch", lambda name, force=False: captured.append(name),
    )
    panel._prompt_delete("doomed")  # noqa: SLF001
    assert captured == ["doomed"]


# ----- drag-and-drop stub -------------------------------------------


def test_drop_with_text_payload_shows_info_dialog(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QMessageBox

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    seen: list[bool] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *args, **kwargs: (seen.append(True) or QMessageBox.StandardButton.Ok)),
    )

    mime = QMimeData()
    mime.setText("feature")
    event = QDropEvent(
        QPoint(0, 0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    panel.dropEvent(event)
    assert seen
