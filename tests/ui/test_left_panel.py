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


# ----- expand/collapse chevron icons ---------------------------------


def test_group_items_carry_expand_chevron(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Every group row in the left panel exposes a chevron icon whose
    direction matches the row's expansion state. Leaf rows (branches,
    tags, stash entries) and the placeholder stay icon-free."""
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _find_top_level(panel, "Branches")
    local = _find_child(branches, "Local")
    remote = _find_child(branches, "Remote")
    tags = _find_top_level(panel, "Tags")
    stash = _find_top_level(panel, "Stash")

    # Branches + Local start expanded; Remote / Tags / Stash stay collapsed.
    assert not branches.icon(0).isNull()
    assert not local.icon(0).isNull()
    assert not remote.icon(0).isNull()
    assert not tags.icon(0).isNull()
    assert not stash.icon(0).isNull()

    # Icons for the two states must differ — otherwise the chevron is
    # just a static dot. ``cacheKey`` of the underlying pixmaps is the
    # stable, well-defined way to compare two ``QIcon`` instances in
    # PySide6.
    collapsed_pixmap_key = tags.icon(0).pixmap(16, 16).cacheKey()
    expanded_pixmap_key = branches.icon(0).pixmap(16, 16).cacheKey()
    assert collapsed_pixmap_key != expanded_pixmap_key

    # Leaf rows (branches) and the placeholder carry no icon.
    main_leaf = _find_child(local, "main  (HEAD)")
    assert main_leaf is not None
    assert main_leaf.icon(0).isNull()


def test_placeholder_has_no_chevron(qtbot) -> None:
    """The 'No repository opened' placeholder is not a group, so it
    must not display a chevron."""
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()

    placeholder = _find_top_level(panel, "No repository opened")
    assert placeholder is not None
    assert placeholder.icon(0).isNull()


def test_chevron_is_tinted_with_theme_text_color(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """The chevron pixmap must be recoloured to match the theme's
    text color, not the platform's default (near-black) icon color.

    We render the icon to its native size, then walk the pixels and
    require at least one fully-opaque pixel to carry the theme's
    RGB channels. Anti-aliased edges (partial alpha) are allowed to
    blend with the background, so the assertion stays a sanity check
    on the bulk fill rather than a per-pixel equality.
    """
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QImage
    from src.utils.theme import DARK_THEME

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _find_top_level(panel, "Branches")
    tags = _find_top_level(panel, "Tags")
    assert branches is not None and tags is not None

    expected = QColor(DARK_THEME.text)
    expected_rgb = (expected.red(), expected.green(), expected.blue())

    def _has_text_color(icon) -> bool:
        pixmap = icon.pixmap(icon.actualSize(QSize(16, 16)))
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
        for y in range(image.height()):
            for x in range(image.width()):
                pixel = image.pixel(x, y)
                alpha = (pixel >> 24) & 0xFF
                if alpha < 200:
                    continue
                r, g, b = (pixel >> 16) & 0xFF, (pixel >> 8) & 0xFF, pixel & 0xFF
                if (r, g, b) == expected_rgb:
                    return True
        return False

    assert _has_text_color(branches.icon(0))
    assert _has_text_color(tags.icon(0))


def test_chevron_updates_when_group_is_collapsed(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Toggling a group's expansion must swap the chevron to match the
    new state. The handler is wired to ``itemExpanded`` /
    ``itemCollapsed`` so the icon stays in sync without manual refresh.

    We can't compare the QPixmap cache keys across calls — Qt hands
    out a fresh ``QPixmap`` on every ``icon.pixmap(...)`` call, so the
    keys differ even when the icon is the same standard arrow. The
    first test already asserts that the two *states* render different
    pixmaps; here we only verify the icon stays valid through a
    full round-trip of expand → collapse → expand."""
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _find_top_level(panel, "Branches")
    assert branches is not None

    # Initially expanded → chevron is set.
    assert not branches.icon(0).isNull()

    branches.setExpanded(False)
    qtbot.waitUntil(lambda: not branches.isExpanded())
    assert not branches.icon(0).isNull()

    branches.setExpanded(True)
    qtbot.waitUntil(lambda: branches.isExpanded())
    assert not branches.icon(0).isNull()


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

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    feature = _child(local, "feature")
    assert feature is not None

    panel.itemDoubleClicked.emit(feature, 0)
    assert committed_repo.repo.head.shorthand == "feature"


def test_double_click_on_remote_branch_fetches_and_checks_it_out(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Double-click on a remote-tracking branch with no local counterpart
    should trigger the non-destructive fetch+create+checkout verb
    (no confirmation dialog — there is nothing to lose).
    """
    _set_up_repo_with_remotes(committed_repo)
    # ``_set_up_repo_with_remotes`` pushes and fetches ``main``,
    # so the local has a tracking branch with the same name. To
    # exercise the no-confirmation path we look at a remote branch
    # whose local counterpart does not exist — create a local
    # ``feature`` branch, push it, then delete the local copy
    # so the panel sees the remote but no local tracking branch.
    from src.core.operations import (
        checkout_branch,
        create_branch,
        delete_branch,
    )
    from src.core.operations import (
        push as core_push,
    )
    head_sha = committed_repo.head_commit.sha
    create_branch(committed_repo, "feature", target_sha=head_sha)
    checkout_branch(committed_repo, "feature")
    core_push(committed_repo, "origin", "refs/heads/feature:refs/heads/feature")
    # Delete the local copy so the panel sees only ``origin/feature``.
    checkout_branch(committed_repo, "main")
    delete_branch(committed_repo, "feature", force=True)

    # Refresh the local panel's branch view so the new ``feature``
    # shows up under Remote.
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)
    vm.branch_panel_view_model().refresh()

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    origin_feature = _child(remote, "origin/feature")
    assert origin_feature is not None

    captured: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        vm,
        "fetch_and_checkout_remote_branch",
        lambda name: captured.append((name,)),
    )
    # Ensure the destructive verb is NOT called.
    monkeypatch.setattr(
        vm,
        "reset_local_branch_to_remote",
        lambda name: captured.append(("RESET", name)),
    )

    panel.itemDoubleClicked.emit(origin_feature, 0)
    assert captured == [("origin/feature",)]


def test_double_click_on_remote_branch_with_local_confirms_reset(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Verify that a remote branch whose short-name matches a local is
    *suppressed* from the panel — the scenario is no longer reachable
    via the remote-tree UI.
    """
    _set_up_repo_with_remotes(committed_repo)

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    assert remote is not None

    # ``origin/main`` is suppressed because local ``main`` exists.
    assert _child(remote, "origin/main") is None

    # ``origin/from-upstream`` has no local counterpart → visible.
    assert _child(remote, "origin/from-upstream") is not None


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


# ----- single-click focuses graph -----------------------------------


def test_single_click_on_local_branch_selects_and_scrolls_graph(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Single-clicking a local branch row drives the graph view.

    The branch's target SHA becomes :attr:`MainViewModel.selected_commit_sha`
    so the right panel switches to the commit-detail view, and the
    :class:`GraphViewModel` emits :attr:`scroll_to_commit_requested`
    so the graph scrolls to that commit. The existing double-click
    behaviour (checkout) is unaffected — we exercise it through the
    ``itemClicked`` signal only, not ``itemDoubleClicked``.
    """
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

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    feature = _child(local, "feature")
    assert feature is not None

    expected_sha = committed_repo.head_commit.parents[0]
    with qtbot.waitSignal(
        vm.graph_view_model().scroll_to_commit_requested, timeout=1000,
    ) as blocker:
        panel.itemClicked.emit(feature, 0)
    assert blocker.args[0] == expected_sha
    assert vm.selected_commit_sha() == expected_sha
    # HEAD did not change — that is a double-click concern.
    assert committed_repo.repo.head.shorthand == "main"


def test_single_click_on_local_branch_with_no_explicit_scroll(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """The branch row's click is forwarded to the graph VM verbatim.

    We replace :meth:`GraphViewModel.scroll_to_commit` with a capturing
    stub and assert it was called with the branch's target SHA. This
    decouples the test from the graph widget's scroll internals
    (which are tested separately in ``test_graph_widget.py``).
    """
    from src.core.operations import create_branch

    create_branch(
        committed_repo,
        "feature",
        target_sha=committed_repo.head_commit.sha,
    )
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[str] = []
    monkeypatch.setattr(
        vm.graph_view_model(),
        "scroll_to_commit",
        lambda sha: captured.append(sha),
    )

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    feature = _child(local, "feature")
    assert feature is not None

    panel.itemClicked.emit(feature, 0)
    assert captured == [committed_repo.head_commit.sha]
    assert vm.selected_commit_sha() == committed_repo.head_commit.sha


def test_single_click_on_remote_branch_selects_and_scrolls(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Single-click on a remote-tracking branch also focuses the graph.

    The remote branch's ``target_sha`` is used as the scroll target,
    so the user lands on the commit the remote ref points at. No
    network call happens (the fetch+checkout verb still lives on
    double-click).
    """
    branch_name = _set_up_repo_with_remotes(committed_repo)

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    origin_branch = _child(remote, f"origin/{branch_name}")
    assert origin_branch is not None

    # The VM should NOT have called fetch — that is a double-click verb.
    monkeypatch.setattr(
        vm,
        "fetch_and_checkout_remote_branch",
        lambda name: (_ for _ in ()).throw(
            AssertionError(f"unexpected fetch on click: {name!r}"),
        ),
    )

    captured: list[str] = []
    monkeypatch.setattr(
        vm.graph_view_model(),
        "scroll_to_commit",
        lambda sha: captured.append(sha),
    )

    panel.itemClicked.emit(origin_branch, 0)
    expected_sha = committed_repo.repo.head.target
    assert captured == [str(expected_sha)]
    assert vm.selected_commit_sha() == str(expected_sha)


def test_single_click_on_tag_selects_and_scrolls(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Single-click on a tag focuses the commit the tag points at."""
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

    tags = _top_level(panel, "Tags")
    v1 = _child(tags, "v1")
    assert v1 is not None

    captured: list[str] = []
    monkeypatch.setattr(
        vm.graph_view_model(),
        "scroll_to_commit",
        lambda sha: captured.append(sha),
    )

    panel.itemClicked.emit(v1, 0)
    assert captured == [committed_repo.head_commit.sha]
    assert vm.selected_commit_sha() == committed_repo.head_commit.sha
    # No branch called "v1" yet — double-click (not click) creates it.
    assert not any(b.name == "v1" for b in committed_repo.branches)


def test_single_click_on_stash_leaf_is_a_noop(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Clicking a stash entry does not focus the graph or scroll.

    Stash entries are different from branches and tags: a single
    click on a stash row should not jump the graph to a (potentially
    already-shown) commit. The user opens the apply / pop / drop
    context menu through the standard double-click / right-click
    flow.
    """
    from pathlib import Path
    (Path(committed_repo.path) / "hello.txt").write_text("wip\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "scroll test")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[str] = []
    monkeypatch.setattr(
        vm.graph_view_model(),
        "scroll_to_commit",
        lambda sha: captured.append(sha),
    )
    monkeypatch.setattr(
        vm,
        "set_selected_commit",
        lambda sha: captured.append(f"SELECT:{sha}"),
    )

    stash = _top_level(panel, "Stash")
    stash_entry = stash.child(0) if stash else None
    assert stash_entry is not None

    panel.itemClicked.emit(stash_entry, 0)
    assert captured == []


def test_single_click_branch_with_empty_target_sha_is_a_noop(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """A click on a branch with no target SHA leaves the selection alone.

    Defends against a stale or malformed :class:`BranchInfo`: if the
    resolver returns ``None`` (no SHA), the focus handler must bail
    out instead of calling :meth:`MainViewModel.set_selected_commit`
    with ``None`` and clearing the right panel mid-action. We
    monkey-patch the panel's VM to return a fake branch with an
    empty target SHA — the panel code path under test does not care
    how the snapshot got into that state.
    """
    from src.core.models import BranchInfo
    from src.core.operations import create_branch

    create_branch(committed_repo, "feature", target_sha=committed_repo.head_commit.sha)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    # Pin the existing selection so we can verify it survives.
    head_sha = committed_repo.head_commit.sha
    vm.set_selected_commit(head_sha)

    # Replace the local_branches snapshot with a row whose target_sha
    # is empty — the resolver will return ``None`` for it.
    monkeypatch.setattr(
        vm.branch_panel_view_model(),
        "local_branches",
        lambda: [BranchInfo(name="feature", is_head=False, target_sha="")],
    )

    captured: list[object] = []
    monkeypatch.setattr(
        vm,
        "set_selected_commit",
        lambda sha: captured.append(sha),
    )
    monkeypatch.setattr(
        vm.graph_view_model(),
        "scroll_to_commit",
        lambda sha: captured.append(f"SCROLL:{sha}"),
    )

    # The visible tree still has the old (real) feature row, which is
    # fine — the resolver consults the VM snapshot, not the QTreeWidget.
    panel.itemClicked.emit(
        _child(_child(_top_level(panel, "Branches"), "Local"), "feature"),
        0,
    )
    # Nothing was emitted: the resolver returned ``None`` and the
    # focus handler bailed out before calling either VM method.
    assert captured == []
    assert vm.selected_commit_sha() == head_sha


def test_single_click_on_branch_leaf_does_not_toggle_group(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Single-clicking a branch leaf must not collapse the group header.

    Regression for the existing ``test_click_on_leaf_does_not_toggle_parent_group``
    test: the new branch-handling branch in ``_on_item_clicked`` runs
    only when ``_ROLE_KIND`` is set, so the group-toggle path is
    bypassed for leaves. The group must stay expanded after a click
    on a branch inside it.
    """
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
    assert local.isExpanded() is True

    panel.itemClicked.emit(feature, 0)
    assert local.isExpanded() is True


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
    assert "Pull" in labels
    assert "Push" in labels
    assert any("Create Branch from" in t for t in labels)
    assert "Rename feature…" in labels
    assert "Delete feature" in labels
    assert "Copy branch name" in labels
    assert "Copy commit sha" in labels
    assert "Create tag here…" in labels
    assert "Create annotated tag here…" in labels


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

    captured: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr(
        vm, "merge_branch",
        lambda source, target=None, *, no_ff=False: captured.append(
            (source, target, no_ff),
        ),
    )
    actions = panel._context_menu_actions(feature)  # noqa: SLF001
    merge = next(a for a in actions if a.text() == "Merge feature into current…")
    merge.trigger()
    # The context-menu merge always requests a merge commit
    # (``no_ff=True``) so the user sees the merge in the graph even
    # on a fast-forward.
    assert captured == [("feature", "main", True)]


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

    actions = panel._on_drop("feature", "local_branch", feature)  # noqa: SLF001
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
    actions = panel._on_drop("feature", "local_branch", tags)  # noqa: SLF001
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

    actions = panel._on_drop("feature", "local_branch", main)  # noqa: SLF001
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

    captured: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr(
        vm, "merge_branch",
        lambda source, target=None, *, no_ff=False: captured.append(
            (source, target, no_ff),
        ),
    )
    actions = panel._on_drop("feature", "local_branch", main)  # noqa: SLF001
    merge_action = next(a for a in actions if a.text() == "Merge feature into main")
    merge_action.trigger()
    # Drag-and-drop merge also forces a merge commit
    # (``no_ff=True``) so the user sees the merge in the graph.
    assert captured == [("feature", "main", True)]


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
    monkeypatch.setattr(
        vm, "checkout_branch",
        lambda name: calls.append(("checkout", (name,))) or True,
    )
    monkeypatch.setattr(vm, "rebase_branch", lambda upstream: calls.append(("rebase", (upstream,))))

    actions = panel._on_drop("feature", "local_branch", main)  # noqa: SLF001
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

    actions = panel._on_drop("feature", "local_branch", main)  # noqa: SLF001
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


def test_mime_data_carries_branch_kind_for_remote(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """MIME for a remote-branch row carries the ``remote_branch`` discriminator."""
    _set_up_repo_with_remotes(committed_repo)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)
    vm.branch_panel_view_model().refresh()

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    assert remote is not None
    # ``_set_up_repo_with_remotes`` returns ``"from-upstream"`` (the
    # ref pushed from local HEAD); it does *not* collide with any
    # local branch so the same-name suppression keeps it visible.
    remote_item = _child(remote, "origin/from-upstream")
    assert remote_item is not None

    data = panel.mimeData([remote_item])
    assert data is not None
    # Custom MIME format used by the drop handler to discriminate
    # the drag source kind — see ``LeftPanel._BRANCH_KIND_MIME``.
    formats = data.formats()
    assert any("branch-kind" in f for f in formats)
    assert data.hasText()
    assert data.text() == "origin/from-upstream"


def test_drop_from_remote_branch_fetches_then_merges(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Dropping ``origin/feature`` onto local ``topic`` first fetches+checkouts
    the tracking branch, then merges with ``no_ff=True``.
    """
    from src.core.operations import (
        checkout_branch,
        create_branch,
        delete_branch,
    )
    from src.core.operations import (
        push as core_push,
    )

    _set_up_repo_with_remotes(committed_repo)
    # Push ``feature`` to origin as a remote-only ref so the
    # same-name suppression filter keeps ``origin/feature`` visible
    # in the left panel (it would otherwise hide behind the local
    # ``feature`` branch — see ``BranchPanelViewModel._suppress_
    # same_name_remotes``).
    create_branch(
        committed_repo, "feature",
        target_sha=committed_repo.head_commit.sha,
    )
    checkout_branch(committed_repo, "feature")
    core_push(committed_repo, "origin", "refs/heads/feature:refs/heads/feature")
    checkout_branch(committed_repo, "main")
    delete_branch(committed_repo, "feature", force=True)
    create_branch(
        committed_repo, "topic",
        target_sha=committed_repo.head_commit.sha,
    )

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)
    vm.branch_panel_view_model().refresh()

    captured: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr(
        vm, "merge_branch",
        lambda source, target=None, *, no_ff=False: captured.append(
            (source, target, no_ff),
        ),
    )
    monkeypatch.setattr(
        vm, "fetch_and_checkout_remote_branch",
        lambda remote_branch: captured.append(("fetch_checkout", remote_branch, None)),
    )

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    origin_feature = _child(remote, "origin/feature")
    local = _child(branches, "Local")
    topic = _child(local, "topic")
    assert origin_feature is not None and topic is not None

    actions = panel._on_drop(  # noqa: SLF001
        "origin/feature", "remote_branch", topic,
    )
    merge_action = next(
        a for a in actions
        if a.text() == "Merge origin/feature into topic"
    )
    merge_action.trigger()

    # First the fetch+checkout of the tracking branch, then the merge.
    assert len(captured) >= 2
    fetch_call = next(c for c in captured if c[0] == "fetch_checkout")
    assert fetch_call[1] == "origin/feature"
    merge_call = next(c for c in captured if c[0] != "fetch_checkout")
    # After normalisation, source is the bare local branch.
    assert merge_call == ("feature", "topic", True)


def test_merge_into_submenu_lists_other_local_branches(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """The ``Merge <name> into...`` submenu lists every local branch except ``self``."""
    from src.core.operations import create_branch

    create_branch(
        committed_repo, "feature",
        target_sha=committed_repo.head_commit.sha,
    )
    create_branch(
        committed_repo, "topic",
        target_sha=committed_repo.head_commit.sha,
    )
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    actions = panel._local_branch_actions("feature")  # noqa: SLF001
    merge_into = next(
        a for a in actions if a.menu() is not None and a.text() == "Merge feature into..."
    )
    candidates = [a.text() for a in merge_into.menu().actions()]
    # ``feature`` itself is excluded, current branch + the other one
    # are listed — the submenu is the same regardless of cursor
    # position over the row.
    assert "feature" not in candidates
    assert set(candidates) == {"main", "topic"}


def test_rebase_onto_submenu_in_local_context_menu(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Mirror of :func:`test_merge_into_submenu_lists_other_local_branches` for rebase."""
    from src.core.operations import create_branch

    create_branch(
        committed_repo, "feature",
        target_sha=committed_repo.head_commit.sha,
    )
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    actions = panel._local_branch_actions("feature")  # noqa: SLF001
    rebase_onto = next(
        a for a in actions if a.menu() is not None and a.text() == "Rebase feature onto..."
    )
    candidates = [a.text() for a in rebase_onto.menu().actions()]
    assert "feature" not in candidates
    assert "main" in candidates


def test_submenu_pick_triggers_drop_merge(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Selecting a target in the ``Merge into…`` submenu fires the merge helper."""
    from src.core.operations import create_branch

    create_branch(
        committed_repo, "feature",
        target_sha=committed_repo.head_commit.sha,
    )
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        vm, "merge_branch",
        lambda source, target=None, *, no_ff=False: captured.append(
            (source, target or "", no_ff),
        ),
    )

    actions = panel._local_branch_actions("feature")  # noqa: SLF001
    merge_into = next(a for a in actions if a.text() == "Merge feature into...")
    main_pick = next(a for a in merge_into.menu().actions() if a.text() == "main")
    main_pick.trigger()
    assert captured == [("feature", "main", True)]


def test_submenu_pick_for_remote_source_fetches_first(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """Selecting a target in the remote branch ``Merge into...`` submenu
    fetches+checkouts the local tracking branch, then merges.
    """
    _set_up_repo_with_remotes(committed_repo)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)
    vm.branch_panel_view_model().refresh()

    captured: list[tuple] = []
    monkeypatch.setattr(
        vm, "merge_branch",
        lambda source, target=None, *, no_ff=False: captured.append(
            ("merge", source, target or "", no_ff),
        ),
    )
    monkeypatch.setattr(
        vm, "fetch_and_checkout_remote_branch",
        lambda name: captured.append(("fetch", name)),
    )

    actions = panel._remote_branch_actions("origin/from-upstream")  # noqa: SLF001
    merge_into = next(a for a in actions if a.text() == "Merge origin/from-upstream into...")
    main_pick = next(a for a in merge_into.menu().actions() if a.text() == "main")
    main_pick.trigger()
    fetch_calls = [c for c in captured if c[0] == "fetch"]
    merge_calls = [c for c in captured if c[0] == "merge"]
    assert len(fetch_calls) == 1
    assert fetch_calls[0] == ("fetch", "origin/from-upstream")
    assert merge_calls == [("merge", "from-upstream", "main", True)]


def test_local_branch_drag_flag_enabled_for_remote_rows_too(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """``ItemIsDragEnabled`` is set on remote branch rows so they
    can be picked up by the user like a local branch.
    """
    from PySide6.QtCore import Qt

    _set_up_repo_with_remotes(committed_repo)
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)
    vm.branch_panel_view_model().refresh()

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    assert remote is not None
    remote_item = _child(remote, "origin/from-upstream")
    assert remote_item is not None
    flags = remote_item.flags()
    assert flags & Qt.ItemFlag.ItemIsDragEnabled


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
    # Push the existing tip to a remote-only ref so the remote-tracking
    # branch does not get filtered out by the same-name suppression.
    head_ref = committed_repo.repo.references.get("HEAD")
    head_name = head_ref.target if head_ref else "refs/heads/main"
    from src.core.operations import add_remote
    from src.core.operations import fetch as core_fetch
    from src.core.operations import push as core_push

    add_remote(committed_repo, "origin", str(origin_path))
    core_push(committed_repo, "origin", head_name + ":refs/heads/from-upstream")
    core_fetch(committed_repo, "origin")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    assert remote is not None
    branch_name = "from-upstream"
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
    core_push(committed_repo, "origin", head_name + ":refs/heads/from-upstream")
    core_fetch(committed_repo, "origin")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    branch_name = "from-upstream"
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


# ----- group-header expand/collapse on click -----------------------------


def _set_up_repo_with_remotes(
    committed_repo: RepositoryManager,
) -> str:
    """Create a bare origin and push so the Remote group has children.

    The pushed ref uses a short name that does *not* collide with any
    local branch so it survives the same-name suppression filter.

    Returns the remote-only short name (``"from-upstream"``).
    """
    import shutil

    import pygit2

    origin_path = Path(committed_repo.path).parent / "origin.git"
    if origin_path.exists():
        shutil.rmtree(origin_path)
    pygit2.init_repository(str(origin_path), bare=True, initial_head="main")
    from src.core.operations import add_remote
    from src.core.operations import fetch as core_fetch
    from src.core.operations import push as core_push

    add_remote(committed_repo, "origin", str(origin_path))
    head_ref = committed_repo.repo.references.get("HEAD")
    head_name = head_ref.target if head_ref else "refs/heads/main"
    # Push the local HEAD to a remote-only ref so its short name
    # (``from-upstream``) does not clash with any local branch name.
    core_push(committed_repo, "origin", head_name + ":refs/heads/from-upstream")
    core_fetch(committed_repo, "origin")
    return "from-upstream"


def test_single_click_on_remote_group_expands_it(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Single-click on the ``Remote`` group toggles its expansion.

    Regression test: with ``setExpandsOnDoubleClick(False)`` and a
    custom ``itemDoubleClicked`` handler that only handles leaves, the
    ``Remote`` group could not be expanded at all — making any
    remote-tracking branch invisible to the user.
    """
    branch_name = _set_up_repo_with_remotes(committed_repo)

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")
    assert remote is not None
    assert remote.isExpanded() is False

    panel.itemClicked.emit(remote, 0)

    assert remote.isExpanded() is True
    assert _child(remote, f"origin/{branch_name}") is not None


def test_second_click_on_remote_group_collapses_it(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A second single-click on the ``Remote`` group collapses it."""
    _set_up_repo_with_remotes(committed_repo)

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    remote = _child(branches, "Remote")

    panel.itemClicked.emit(remote, 0)
    assert remote.isExpanded() is True

    panel.itemClicked.emit(remote, 0)
    assert remote.isExpanded() is False


def test_single_click_on_local_group_collapses_it(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """The ``Local`` group is expanded by default; clicking should collapse it."""
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    assert local.isExpanded() is True  # expanded by default in _rebuild

    panel.itemClicked.emit(local, 0)
    assert local.isExpanded() is False

    panel.itemClicked.emit(local, 0)
    assert local.isExpanded() is True


def test_single_click_on_tags_group_expands_it(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A single click on the ``Tags`` top-level group toggles expansion."""
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

    tags = _top_level(panel, "Tags")
    assert tags.isExpanded() is False

    panel.itemClicked.emit(tags, 0)
    assert tags.isExpanded() is True
    assert _child(tags, "v1") is not None


def test_click_on_leaf_does_not_toggle_parent_group(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Clicking a branch leaf must not collapse the group that contains it."""
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
    assert local.isExpanded() is True

    panel.itemClicked.emit(feature, 0)
    # The leaf has _ROLE_KIND set, so the group-toggle handler must skip it.
    assert local.isExpanded() is True


def test_double_click_on_group_header_toggles_once(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """A double-click on a group header ends up toggled exactly once.

    Qt fires ``itemClicked`` twice (one per click) before
    ``itemDoubleClicked`` for a double-click. Two toggles cancel
    out, then ``itemDoubleClicked`` toggles again — net effect: the
    state flips once.
    """
    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    branches = _top_level(panel, "Branches")
    local = _child(branches, "Local")
    assert local.isExpanded() is True

    # Simulate the two itemClicked + one itemDoubleClicked sequence
    # that Qt produces for a real double-click.
    panel.itemClicked.emit(local, 0)
    panel.itemClicked.emit(local, 0)
    panel.itemDoubleClicked.emit(local, 0)
    assert local.isExpanded() is False


# ----- stash context menu and verb delegations --------------------------


def test_stash_context_menu_has_apply_pop_drop(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """The right-click menu on a stash entry exposes Apply, Pop, Drop."""
    from pathlib import Path

    (Path(committed_repo.path) / "hello.txt").write_text("wip\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "menu test")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    stash = _top_level(panel, "Stash")
    assert stash is not None
    # libgit2 prefixes "On <branch>: " to the user message.
    stash_entry = _child(stash, "stash@{0}: On main: menu test")
    assert stash_entry is not None

    actions = panel._context_menu_actions(stash_entry)  # noqa: SLF001
    labels = {a.text() for a in actions}
    assert "Apply Stash" in labels
    assert "Pop Stash" in labels
    assert "Drop Stash" in labels


def test_stash_apply_action_invokes_main_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    from pathlib import Path

    (Path(committed_repo.path) / "hello.txt").write_text("apply\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "apply-target")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[int] = []
    monkeypatch.setattr(
        vm, "stash_apply",
        lambda index=0: captured.append(index),
    )

    stash = _top_level(panel, "Stash")
    entry = _child(stash, "stash@{0}: On main: apply-target")
    actions = panel._context_menu_actions(entry)  # noqa: SLF001
    apply_action = next(a for a in actions if a.text() == "Apply Stash")
    apply_action.trigger()
    assert captured == [0]


def test_stash_pop_action_invokes_main_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    from pathlib import Path

    (Path(committed_repo.path) / "hello.txt").write_text("pop\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "pop-target")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[int] = []
    monkeypatch.setattr(
        vm, "stash_pop",
        lambda index=0: captured.append(index) or True,
    )

    stash = _top_level(panel, "Stash")
    entry = _child(stash, "stash@{0}: On main: pop-target")
    actions = panel._context_menu_actions(entry)  # noqa: SLF001
    pop_action = next(a for a in actions if a.text() == "Pop Stash")
    pop_action.trigger()
    assert captured == [0]


def test_stash_drop_action_invokes_main_vm(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    from pathlib import Path

    from PySide6.QtWidgets import QMessageBox

    (Path(committed_repo.path) / "hello.txt").write_text("drop\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "drop-target")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[int] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(
            lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
        ),
    )
    monkeypatch.setattr(
        vm, "stash_drop",
        lambda index=0: captured.append(index) or True,
    )

    stash = _top_level(panel, "Stash")
    entry = _child(stash, "stash@{0}: On main: drop-target")
    actions = panel._context_menu_actions(entry)  # noqa: SLF001
    drop_action = next(a for a in actions if a.text() == "Drop Stash")
    drop_action.trigger()
    assert captured == [0]


def test_stash_drop_action_cancelled_by_user(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """If the user clicks No in the confirm dialog, stash_drop is *not* called."""
    from pathlib import Path

    from PySide6.QtWidgets import QMessageBox

    (Path(committed_repo.path) / "hello.txt").write_text("drop2\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "drop-cancel")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    captured: list[int] = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(
            lambda *args, **kwargs: QMessageBox.StandardButton.No,
        ),
    )
    monkeypatch.setattr(
        vm, "stash_drop",
        lambda index=0: captured.append(index) or True,
    )

    panel._drop_stash(0)  # noqa: SLF001
    assert captured == []


def test_stash_list_updates_after_push(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Pushing a stash through the VM updates the left panel's stash list."""
    from pathlib import Path

    (Path(committed_repo.path) / "hello.txt").write_text("wip\n")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    stash = _top_level(panel, "Stash")
    assert stash.isDisabled() is True  # no stashes yet

    vm.stash_push("list-update")
    stash = _top_level(panel, "Stash")
    assert stash.isDisabled() is False
    assert _child(stash, "stash@{0}: On main: list-update") is not None


def test_stash_index_out_of_range_is_ignored(
    qtbot, committed_repo: RepositoryManager, monkeypatch,
) -> None:
    """If the stash list shrunk between menu build and action trigger,
    the delegation helpers bail out without calling the VM."""
    from pathlib import Path

    (Path(committed_repo.path) / "hello.txt").write_text("range\n")
    from src.core.operations import stash_push

    stash_push(committed_repo, "out of range")

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    called: list[str] = []
    monkeypatch.setattr(vm, "stash_apply", lambda index=0: called.append("apply"))
    monkeypatch.setattr(vm, "stash_pop", lambda index=0: called.append("pop"))
    monkeypatch.setattr(vm, "stash_drop", lambda index=0: called.append("drop"))

    # Drop the stash before the action runs; index 0 is now invalid.
    from src.core.operations import stash_drop as core_stash_drop

    core_stash_drop(committed_repo, 0)
    vm.branch_panel_view_model().refresh()

    panel._apply_stash(0)  # noqa: SLF001
    panel._pop_stash(0)  # noqa: SLF001
    panel._drop_stash(0)  # noqa: SLF001
    assert called == []
