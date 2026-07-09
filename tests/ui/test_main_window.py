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


# ----- WIP context-menu wiring ----------------------------------------


def test_graph_wip_stash_push_invokes_main_vm(
    qtbot, committed_repo, monkeypatch,
) -> None:
    """Emitting ``stash_push_requested`` from the graph routes to the VM.

    The graph's WIP context menu emits ``stash_push_requested`` with
    the ``"WIP"`` marker; the :class:`MainWindow` handler must
    ignore the payload and call :meth:`MainViewModel.stash_push`
    with the default ``"WIP"`` message so the operation lands on
    the undo stack.
    """
    window = MainWindow(config_path=None)
    qtbot.addWidget(window)
    window.set_repository(committed_repo)

    captured: list[str] = []
    monkeypatch.setattr(
        window._main_vm, "stash_push",
        lambda message="WIP": captured.append(message),
    )

    window._on_stash_push_graph("WIP")  # noqa: SLF001
    assert captured == ["WIP"]

    window.close()


def test_main_window_routes_create_branch_here_to_viewmodel(
    qtbot, committed_repo, monkeypatch,
) -> None:
    """`create_branch_here_requested` from the graph routes to the VM.

    The graph's branch-chip context menu spawns an inline editor;
    on Enter, the signal `(sha, name)` fires. The
    :class:MainWindow handler must forward both arguments to
    :meth:MainViewModel.create_branch so the new branch lands
    on the undo stack (`CreateBranchCommand`).
    """
    from src.ui.main_window import MainWindow

    window = MainWindow(config_path=None)
    qtbot.addWidget(window)
    window.set_repository(committed_repo)

    captured: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        window._main_vm, "create_branch",
        lambda name, target_sha=None: captured.append((name, target_sha)),
    )

    head_sha = committed_repo.head_commit.sha
    window._on_create_branch_here(head_sha, "hotfix")  # noqa: SLF001
    assert captured == [("hotfix", head_sha)]

    window.close()


# ----- Branch-chip copy wiring -----------------------------------------


def test_graph_copy_branch_name_routes_to_clipboard(
    qtbot, committed_repo, monkeypatch,
) -> None:
    """`copy_branch_name_requested` from the graph routes to ``MainViewModel.copy_to_clipboard``.

    Right-clicking a branch chip surfaces a 'Copy branch name' item;
    the :class:`MainWindow` handler must forward the chip's full
    ref name to :meth:`MainViewModel.copy_to_clipboard` (which in
    turn writes to ``QApplication.clipboard()``). Empty payloads
    are silently ignored so a stale graph rebuild cannot clear the
    clipboard.
    """
    from src.ui.main_window import MainWindow

    window = MainWindow(config_path=None)
    qtbot.addWidget(window)
    window.set_repository(committed_repo)

    captured: list[str] = []
    monkeypatch.setattr(
        window._main_vm, "copy_to_clipboard",
        lambda text: captured.append(text),
    )

    window._on_copy_branch_name("main")  # noqa: SLF001
    window._on_copy_branch_name("")  # noqa: SLF001 — empty is a no-op
    assert captured == ["main"]

    window.close()


def test_graph_copy_commit_sha_routes_to_clipboard(
    qtbot, committed_repo, monkeypatch,
) -> None:
    """`copy_commit_sha_requested` from the graph routes to the clipboard helper.

    Same contract as the branch-name variant: the handler forwards
    the chip's row SHA to :meth:`MainViewModel.copy_to_clipboard`,
    skipping empty payloads.
    """
    from src.ui.main_window import MainWindow

    window = MainWindow(config_path=None)
    qtbot.addWidget(window)
    window.set_repository(committed_repo)

    captured: list[str] = []
    monkeypatch.setattr(
        window._main_vm, "copy_to_clipboard",
        lambda text: captured.append(text),
    )

    sha = committed_repo.head_commit.sha
    window._on_copy_commit_sha(sha)  # noqa: SLF001
    window._on_copy_commit_sha("")  # noqa: SLF001 — empty is a no-op
    assert captured == [sha]

    window.close()


# ----- Repo tab context-menu wiring -------------------------------------


def test_show_repo_folder_routes_to_main_vm(
    qtbot, monkeypatch,
) -> None:
    """``show_folder_requested`` forwards to ``MainViewModel.show_repo_in_folder``.

    The :class:`MainWindow` handler is a thin wrapper that calls the
    VM's Explorer-opening helper and shows a status-bar message.
    Pinning both the routing and the empty-payload guard keeps a
    refactor of the menu builder from breaking the contract.
    """
    from src.ui.main_window import MainWindow

    window = MainWindow(config_path=None)
    qtbot.addWidget(window)

    captured: list[str] = []
    monkeypatch.setattr(
        window._main_vm, "show_repo_in_folder",
        lambda path: captured.append(path),
    )

    window._on_show_repo_folder("/repos/sample")  # noqa: SLF001
    window._on_show_repo_folder("")  # noqa: SLF001 — empty is a no-op
    assert captured == ["/repos/sample"]

    window.close()


def test_copy_repo_path_routes_to_main_vm(
    qtbot, monkeypatch,
) -> None:
    """``copy_path_requested`` from the repo bar forwards to ``MainViewModel.copy_repo_path``.

    The handler must ignore empty payloads so a stale menu cannot
    silently overwrite the user's clipboard.
    """
    from src.ui.main_window import MainWindow

    window = MainWindow(config_path=None)
    qtbot.addWidget(window)

    captured: list[str] = []
    monkeypatch.setattr(
        window._main_vm, "copy_repo_path",
        lambda path: captured.append(path),
    )

    window._on_copy_repo_path("/repos/sample")  # noqa: SLF001
    window._on_copy_repo_path("")  # noqa: SLF001 — empty is a no-op
    assert captured == ["/repos/sample"]

    window.close()


def test_repo_bar_signals_are_wired_to_main_window(qtbot) -> None:
    """The widget's context-menu signals reach the MainWindow slots.

    Catches a refactor that renames either signal or slot without
    updating :meth:`MainWindow._build_repo_bar` — both
    ``show_folder_requested`` and ``copy_path_requested`` must hit
    their respective handlers when emitted from the widget.
    """
    from src.ui.main_window import MainWindow

    window = MainWindow(config_path=None)
    qtbot.addWidget(window)

    show_calls: list[str] = []
    copy_calls: list[str] = []
    window._main_vm.show_repo_in_folder = (  # type: ignore[method-assign]
        lambda path: show_calls.append(path)
    )
    window._main_vm.copy_repo_path = (  # type: ignore[method-assign]
        lambda path: copy_calls.append(path)
    )

    window._repo_bar.show_folder_requested.emit("/repo/a")  # noqa: SLF001
    window._repo_bar.copy_path_requested.emit("/repo/b")  # noqa: SLF001
    assert show_calls == ["/repo/a"]
    assert copy_calls == ["/repo/b"]

    window.close()
