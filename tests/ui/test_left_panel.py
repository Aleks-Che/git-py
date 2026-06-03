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


def test_context_menu_has_merge_and_rebase_actions(
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
    assert "Merge feature into current…" in labels
    assert "Rebase feature onto current…" in labels


def test_context_menu_merge_disabled_on_current_branch(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _find_top_level(panel, "Branches")
    local = _find_child(branches, "Local")
    main = _find_child(local, "main  (HEAD)")
    actions = panel._context_menu_actions(main)  # noqa: SLF001
    # Find the merge action — its text contains "Merge main into current…"
    merge_action = next(a for a in actions if a.text().startswith("Merge main into"))
    assert merge_action.isEnabled() is False
    rebase_action = next(a for a in actions if a.text().startswith("Rebase main onto"))
    assert rebase_action.isEnabled() is False


def test_context_menu_merge_into_current_invokes_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
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

    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        vm, "merge_branch",
        lambda source, target=None: captured.append((source, target)),
    )
    actions = panel._context_menu_actions(feature)  # noqa: SLF001
    merge = next(a for a in actions if a.text() == "Merge feature into current…")
    merge.trigger()
    assert captured == [("feature", "main")]


# ----- drag-and-drop --------------------------------------------------


def _top_level(panel: LeftPanel, label: str):
    for i in range(panel.topLevelItemCount()):
        item = panel.topLevelItem(i)
        if item.text(0) == label:
            return item
    return None


def _child(group, text: str):
    for i in range(group.childCount()):
        c = group.child(i)
        if c.text(0) == text:
            return c
    return None


def test_drop_event_filters_same_source_and_target(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Dropping a branch onto itself must produce no actions."""
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    feature = _child(local, "feature")
    assert feature is not None

    actions = panel._on_drop("feature", feature)  # noqa: SLF001
    assert actions == []  # source == target → no actions


def test_drop_event_filters_non_branch_target(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Dropping onto a tag or remote branch produces no actions."""
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    tags = _top_level(panel, "Tags")
    assert tags is not None
    # Even on an empty tags group, the drop should be ignored (kind is None).
    actions = panel._on_drop("feature", tags)  # noqa: SLF001
    assert actions == []


def test_drop_actions_have_merge_and_rebase(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    main = _child(local, "main  (HEAD)")
    feature = _child(local, "feature")
    assert main is not None and feature is not None

    actions = panel._on_drop("feature", main)  # noqa: SLF001
    labels = [a.text() for a in actions]
    assert "Merge feature into main" in labels
    assert "Rebase feature onto main" in labels


def test_drop_actions_trigger_correct_vm_methods(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Triggering a Merge action calls vm.merge_branch(source, target)."""
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    main = _child(local, "main  (HEAD)")

    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        vm, "merge_branch",
        lambda source, target=None: captured.append((source, target)),
    )
    actions = panel._on_drop("feature", main)  # noqa: SLF001
    merge_action = next(a for a in actions if a.text() == "Merge feature into main")
    merge_action.trigger()
    assert captured == [("feature", "main")]


def test_drop_rebase_action_triggers_checkout_then_rebase(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    main = _child(local, "main  (HEAD)")

    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(vm, "checkout_branch", lambda name: calls.append(("checkout", (name,))))
    monkeypatch.setattr(vm, "rebase_branch", lambda upstream: calls.append(("rebase", (upstream,))))

    actions = panel._on_drop("feature", main)  # noqa: SLF001
    rebase_action = next(a for a in actions if a.text() == "Rebase feature onto main")
    rebase_action.trigger()
    # We're currently on main, so the panel checks out feature first,
    # then rebases onto main.
    assert calls == [("checkout", ("feature",)), ("rebase", ("main",))]


def test_drop_rebase_skips_checkout_when_already_on_source(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """If the current HEAD is the source branch, no checkout happens."""
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    # Force HEAD onto feature (so rebase is a one-step op).
    from src.core.operations import checkout_branch
    checkout_branch(committed_repo, "feature")

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    main = _child(local, "main  (HEAD)")

    calls: list[tuple[str, tuple]] = []
    monkeypatch.setattr(vm, "checkout_branch", lambda name: calls.append(("checkout", (name,))))
    monkeypatch.setattr(vm, "rebase_branch", lambda upstream: calls.append(("rebase", (upstream,))))

    actions = panel._on_drop("feature", main)  # noqa: SLF001
    rebase_action = next(a for a in actions if a.text() == "Rebase feature onto main")
    rebase_action.trigger()
    # No checkout — already on feature.
    assert calls == [("rebase", ("main",))]


def test_mime_data_uses_bare_branch_name(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Drag mime data should be the branch name, not the display text."""
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    feature = _child(local, "feature")

    data = panel.mimeData([feature])
    assert data is not None
    assert data.hasText()
    assert data.text() == "feature"


# ----- fetch context menu on remote branches ------------------------------


def test_remote_branch_context_menu_has_fetch_action(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Right-clicking a remote-tracking branch exposes a ``Fetch from <name>`` action."""
    import shutil

    import pygit2

    # Build a remote-tracking branch via a local bare origin.
    origin_path = committed_repo.path and (
        Path(committed_repo.path).parent / "origin.git"
    )
    if origin_path is None:
        return
    # Re-init the bare repo (it might have been set up by a sibling test).
    if origin_path.exists():
        shutil.rmtree(origin_path)
    pygit2.init_repository(str(origin_path), bare=True, initial_head="main")
    # Push the existing tip to origin and fetch so a remote-tracking
    # branch materialises.
    head_ref = committed_repo.repo.references.get("HEAD")
    head_name = head_ref.target if head_ref else "refs/heads/main"
    from src.core.operations import add_remote
    from src.core.operations import fetch as core_fetch
    from src.core.operations import push as core_push

    add_remote(committed_repo, "origin", str(origin_path))
    core_push(committed_repo, "origin", head_name)
    core_fetch(committed_repo, "origin")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    assert remote is not None
    branch_name = head_name.removeprefix("refs/heads/")
    origin_branch = _child(remote, f"origin/{branch_name}")
    assert origin_branch is not None

    actions = panel._context_menu_actions(origin_branch)  # noqa: SLF001
    labels = [a.text() for a in actions]
    assert any("Create Branch from" in t for t in labels)
    assert "Fetch from origin" in labels


def test_remote_branch_fetch_action_invokes_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Triggering ``Fetch from origin`` calls :meth:`MainViewModel.fetch_changes`."""
    import shutil

    import pygit2

    origin_path = committed_repo.path and (
        Path(committed_repo.path).parent / "origin.git"
    )
    if origin_path is None:
        return
    if origin_path.exists():
        shutil.rmtree(origin_path)
    pygit2.init_repository(str(origin_path), bare=True, initial_head="main")
    from src.core.operations import add_remote
    from src.core.operations import fetch as core_fetch
    from src.core.operations import push as core_push

    add_remote(committed_repo, "origin", str(origin_path))
    head_ref = committed_repo.repo.references.get("HEAD")
    head_name = head_ref.target if head_ref else "refs/heads/main"
    core_push(committed_repo, "origin", head_name)
    core_fetch(committed_repo, "origin")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    branch_name = head_name.removeprefix("refs/heads/")
    origin_branch = _child(remote, f"origin/{branch_name}")

    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        vm,
        "fetch_changes",
        lambda remote, refspec=None: captured.append((remote, refspec)),
    )
    actions = panel._context_menu_actions(origin_branch)  # noqa: SLF001
    fetch = next(a for a in actions if a.text() == "Fetch from origin")
    fetch.trigger()
    assert captured == [("origin", None)]
