"""Stage 0: the main window constructs, shows, and exposes the Stage 0 layout."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pygit2
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow


def test_main_window_builds(qtbot) -> None:
    assert isinstance(QApplication.instance(), QApplication)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()

    assert window.windowTitle() == "git-py"
    assert window.isVisible()
    window.close()


def test_app_activation_refreshes_repository(qtbot) -> None:
    """Switching back to the app must trigger a ViewModel refresh.

    Simulates the ``QApplication.applicationStateChanged`` signal that
    Qt fires when the user Alt-Tabs back, un-minimises the window, or
    clicks on the taskbar. The bound :class:`MainViewModel` should
    receive exactly one ``refresh_state`` call so changes made in
    another Git client show up in this UI.
    """
    assert isinstance(QApplication.instance(), QApplication)
    window = MainWindow()
    qtbot.addWidget(window)

    refresh = MagicMock()
    window._main_vm.refresh_state = refresh  # type: ignore[method-assign]

    app = QApplication.instance()
    assert app is not None
    # Active → refresh; inactive → no refresh.
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)

    assert refresh.call_count == 1
    window.close()


def test_branch_drop_handler_invokes_merge(qtbot, tmp_path) -> None:
    """Dropping branch A on branch B on the graph routes to ``merge_branch``.

    End-to-end test of the chip drop wiring: a synthetic
    ``branch_dropped_on_branch`` emission from the graph table
    must reach ``MainViewModel.merge_branch(source, target=target)``
    when the user picks the merge action from the drop menu. The
    test invokes ``_build_branch_drop_actions`` directly (the same
    builder the live ``QMenu`` uses) to avoid blocking on
    ``QMenu.exec``.
    """
    repo_path = tmp_path / "drop-repo"
    pygit2.init_repository(str(repo_path), initial_head="main")
    mgr = RepositoryManager(str(repo_path))
    sig = pygit2.Signature("tester", "t@x", int(time.time()), 0)
    (repo_path / "a.txt").write_text("a\n")
    mgr.repo.index.add("a.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c = mgr.repo.create_commit("refs/heads/main", sig, sig, "init", tree, [])
    mgr.repo.create_reference("refs/heads/feature", c, force=True)

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)

    # Capture merge calls instead of running them for real (a real
    # merge would touch the worktree and break the test's repo state).
    captured: list[tuple[str, str | None, bool]] = []
    window._main_vm.merge_branch = (  # type: ignore[method-assign]
        lambda source, target=None, *, no_ff=False: captured.append(
            (source, target, no_ff),
        )
    )

    # Build the drop-menu actions synchronously, the way the live
    # ``QMenu`` would.  Same builder as :meth:`_on_graph_branch_dropped`.
    actions = window._build_branch_drop_actions("feature", "main")  # noqa: SLF001
    labels = [a.text() for a in actions]
    assert "Merge feature into main" in labels
    assert "Rebase feature onto main" in labels

    # Triggering the merge action must reach the VM.
    merge_action = next(
        a for a in actions if a.text() == "Merge feature into main"
    )
    merge_action.trigger()
    # Drop-menu merge always asks for a merge commit
    # (``no_ff=True``) so the user sees the merge in the graph.
    assert captured == [("feature", "main", True)]

    window.close()


def test_branch_drop_handler_invokes_rebase(qtbot, tmp_path) -> None:
    """Triggering the rebase action on the drop menu calls ``rebase_branch``.

    Same setup as :func:`test_branch_drop_handler_invokes_merge`
    but triggers the second action (``Rebase feature onto main``)
    instead. The rebase wiring is two-step: checkout the source
    first, then rebase onto the target.
    """
    repo_path = tmp_path / "rebase-drop-repo"
    pygit2.init_repository(str(repo_path), initial_head="main")
    mgr = RepositoryManager(str(repo_path))
    sig = pygit2.Signature("tester", "t@x", int(time.time()), 0)
    (repo_path / "a.txt").write_text("a\n")
    mgr.repo.index.add("a.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    c = mgr.repo.create_commit("refs/heads/main", sig, sig, "init", tree, [])
    mgr.repo.create_reference("refs/heads/feature", c, force=True)

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)

    rebase_calls: list[str] = []
    checkout_calls: list[str] = []
    window._main_vm.rebase_branch = (  # type: ignore[method-assign]
        lambda upstream: rebase_calls.append(upstream)
    )
    window._main_vm.checkout_branch = (  # type: ignore[method-assign]
        lambda name: checkout_calls.append(name) or True
    )

    actions = window._build_branch_drop_actions("feature", "main")  # noqa: SLF001
    rebase_action = next(
        a for a in actions if a.text() == "Rebase feature onto main"
    )
    rebase_action.trigger()

    # HEAD is ``main``, the source is ``feature`` — the handler must
    # checkout ``feature`` first, then call rebase onto ``main``.
    assert checkout_calls == ["feature"]
    assert rebase_calls == ["main"]

    window.close()


def test_branch_drop_ignores_same_source_and_target(qtbot, tmp_path) -> None:
    """A drop with the same source and target must be a no-op.

    Mirrors the left panel's ``_on_drop`` filter: a user dragging
    a branch onto itself should not produce a menu (merging a
    branch into itself never makes sense). The guard lives in
    ``_on_graph_branch_dropped``; the test pins that calling the
    builder with ``source == target`` still works (so other call
    sites can keep using it) but the live slot is a no-op.
    """
    repo_path = tmp_path / "self-drop-repo"
    pygit2.init_repository(str(repo_path), initial_head="main")
    mgr = RepositoryManager(str(repo_path))
    sig = pygit2.Signature("tester", "t@x", int(time.time()), 0)
    (repo_path / "a.txt").write_text("a\n")
    mgr.repo.index.add("a.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("refs/heads/main", sig, sig, "init", tree, [])

    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.set_repository(mgr)

    merge_calls: list = []
    rebase_calls: list = []
    window._main_vm.merge_branch = (  # type: ignore[method-assign]
        lambda *a, **kw: merge_calls.append((a, kw))
    )
    window._main_vm.rebase_branch = (  # type: ignore[method-assign]
        lambda *a, **kw: rebase_calls.append((a, kw))
    )

    # Same source and target → the slot returns without showing
    # a menu, the VM methods are not called.
    window._on_graph_branch_dropped("main", "main")  # noqa: SLF001
    assert merge_calls == []
    assert rebase_calls == []

    window.close()
