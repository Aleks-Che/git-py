"""Tests for :class:`src.ui.widgets.repo_bar_widget.RepoBarWidget`.

Pins the contract of the right-click context menu the user sees on
repository tabs:

* **Show repo folder** → :attr:`show_folder_requested` with the
  clicked tab's repo path.
* **Copy repo path** → :attr:`copy_path_requested` with the same.
* **Close repo tab** → calls
  :meth:`RepoTabViewModel.remove_tab` with the clicked index.
* **Close other tabs** → enabled only with >1 tab; calls
  :meth:`RepoTabViewModel.close_others`.
* **Close tabs to the right** → enabled only when the clicked tab is
  not already rightmost; calls
  :meth:`RepoTabViewModel.close_to_right`.

Right-clicks that miss every tab (``tabAt`` returns ``-1``) must not
emit anything — the menu is suppressed rather than falling back to
whatever path the bar happens to have cached.

Each test drives the synchronous builder
:meth:`RepoBarWidget._build_tab_context_menu_actions` directly — the
same builder the live :class:`QMenu` consumes — to avoid blocking on
``QMenu.exec`` (which would suspend on user input).

Note on paths
-------------
``RepoTabViewModel.add_tab`` normalises inputs through
``Path(path).resolve()``; on Windows that turns ``"/repo/a"`` into
``"C:/repo/a"``. The tests create matching directories under
``C:/repo`` (skipping on non-Windows where the path is fine as-is) and
compare ``vm.tabs`` through a resolved set, so the assertions are
portable.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication
from src.ui.widgets.repo_bar_widget import RepoBarWidget
from src.viewmodels.repo_tabs_viewmodel import RepoTabViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _make_repo(name: str) -> str:
    """Materialise ``/repo/<name>`` and return the resolved path string.

    On Windows the absolute drive letter is included; the returned
    string is what we feed into :meth:`RepoTabViewModel.add_tab`.
    """
    repo_root = Path("C:/repo") if sys.platform == "win32" else Path("/repo")
    target = repo_root / name
    target.mkdir(parents=True, exist_ok=True)
    return str(target.resolve())


def _resolved_set(vm: RepoTabViewModel) -> set[str]:
    return {Path(p).resolve().as_posix() for p in vm.tabs}


def _action_labels(actions: list[QAction]) -> list[str]:
    """Return text labels of non-separator actions, in order."""
    return [a.text() for a in actions if not a.isSeparator()]


def _enabled(actions: list[QAction]) -> dict[str, bool]:
    """Map action label → ``setEnabled`` state for non-separator actions."""
    return {a.text(): a.isEnabled() for a in actions if not a.isSeparator()}


# ----- menu structure ----------------------------------------------------


def test_context_menu_lists_all_five_user_actions(qtbot) -> None:
    """All five actions must appear in the documented order."""
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("a"))
    vm.add_tab(_make_repo("b"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=0, path="/repo/a", tab_count=2,
    )
    labels = _action_labels(actions)
    assert labels == [
        "Show repo folder",
        "Copy repo path",
        "Close repo tab",
        "Close other tabs",
        "Close tabs to the right",
    ]


def test_context_menu_has_exactly_one_separator(qtbot) -> None:
    """A single separator separates "paths" from "tab ops"."""
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("a"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=0, path="/repo/a", tab_count=1,
    )
    seps = [a for a in actions if a.isSeparator()]
    assert len(seps) == 1


# ----- disabled-state rules ---------------------------------------------


def test_close_other_tabs_disabled_when_only_one(qtbot) -> None:
    """With a single tab, 'Close other tabs' is meaningless — disabled."""
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("only"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=0, path="/repo/only", tab_count=1,
    )
    enabled = _enabled(actions)
    assert enabled["Close other tabs"] is False
    assert enabled["Close tabs to the right"] is False
    # The "single-tab" actions stay enabled so the user can still
    # close the one tab or copy its path.
    assert enabled["Close repo tab"] is True
    assert enabled["Show repo folder"] is True
    assert enabled["Copy repo path"] is True


def test_close_to_right_disabled_on_rightmost_tab(qtbot) -> None:
    """Right-click on the last tab — nothing to the right."""
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("a"))
    vm.add_tab(_make_repo("b"))
    vm.add_tab(_make_repo("c"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=2, path="/repo/c", tab_count=3,
    )
    enabled = _enabled(actions)
    assert enabled["Close tabs to the right"] is False
    # Other-tabs action *is* enabled (there are 3 tabs).
    assert enabled["Close other tabs"] is True


def test_close_to_right_enabled_on_non_rightmost(qtbot) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("a"))
    vm.add_tab(_make_repo("b"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=0, path="/repo/a", tab_count=2,
    )
    enabled = _enabled(actions)
    assert enabled["Close tabs to the right"] is True


# ----- action triggers ---------------------------------------------------


def test_show_folder_action_emits_signal_with_clicked_path(qtbot) -> None:
    """Triggering Show repo folder emits the clicked tab's path."""
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("first"))
    vm.add_tab(_make_repo("second"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    captured: list[str] = []
    bar.show_folder_requested.connect(captured.append)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=1, path="/repo/second", tab_count=2,
    )
    show = next(a for a in actions if a.text() == "Show repo folder")
    show.trigger()
    assert captured == ["/repo/second"]


def test_copy_path_action_emits_signal_with_clicked_path(qtbot) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("only"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    captured: list[str] = []
    bar.copy_path_requested.connect(captured.append)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=0, path="/repo/only", tab_count=1,
    )
    copy = next(a for a in actions if a.text() == "Copy repo path")
    copy.trigger()
    assert captured == ["/repo/only"]


def test_close_repo_tab_action_invokes_remove_tab(qtbot) -> None:
    """Triggering Close repo tab forwards the clicked index to the VM."""
    _ensure_app()
    vm = RepoTabViewModel()
    path_a = _make_repo("rt_a")
    path_b = _make_repo("rt_b")
    path_c = _make_repo("rt_c")
    vm.add_tab(path_a)
    vm.add_tab(path_b)
    vm.add_tab(path_c)
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=1, path="/repo/rt_b", tab_count=3,
    )
    close = next(a for a in actions if a.text() == "Close repo tab")
    close.trigger()
    assert _resolved_set(vm) == {Path(path_a).resolve().as_posix(),
                                  Path(path_c).resolve().as_posix()}


def test_close_other_tabs_action_invokes_close_others(qtbot) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    path_a = _make_repo("co_a")
    path_b = _make_repo("co_b")
    path_c = _make_repo("co_c")
    vm.add_tab(path_a)
    vm.add_tab(path_b)
    vm.add_tab(path_c)
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=1, path="/repo/co_b", tab_count=3,
    )
    close = next(a for a in actions if a.text() == "Close other tabs")
    close.trigger()
    assert _resolved_set(vm) == {Path(path_b).resolve().as_posix()}


def test_close_tabs_to_right_action_invokes_close_to_right(qtbot) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    path_a = _make_repo("cr_a")
    path_b = _make_repo("cr_b")
    path_c = _make_repo("cr_c")
    path_d = _make_repo("cr_d")
    vm.add_tab(path_a)
    vm.add_tab(path_b)
    vm.add_tab(path_c)
    vm.add_tab(path_d)
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    actions = bar._build_tab_context_menu_actions(  # noqa: SLF001
        index=1, path="/repo/cr_b", tab_count=4,
    )
    close = next(a for a in actions if a.text() == "Close tabs to the right")
    close.trigger()
    assert _resolved_set(vm) == {Path(path_a).resolve().as_posix(),
                                  Path(path_b).resolve().as_posix()}


# ----- right click on empty space ---------------------------------------


def test_on_tab_context_menu_skips_when_no_tab_hit(qtbot) -> None:
    """A right click that misses every tab must not show a menu.

    The ``customContextMenuRequested`` slot resolves the tab through
    :meth:`QTabBar.tabAt`; a ``-1`` is a strong "miss" signal, and we
    silently ``return`` so the slot does not pop a menu populated
    with bogus data.
    """
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("only_for_miss"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    emitted_show: list[str] = []
    emitted_copy: list[str] = []
    bar.show_folder_requested.connect(emitted_show.append)
    bar.copy_path_requested.connect(emitted_copy.append)

    bar._on_tab_context_menu(QPoint(10_000, 0))  # noqa: SLF001

    assert emitted_show == []
    assert emitted_copy == []


def test_on_tab_context_menu_skips_when_tab_data_empty(qtbot) -> None:
    """Defensive guard: a tab whose data is missing/empty must not crash.

    During a brief moment between ``_rebuild_tabs`` and the actual
    ``setTabData`` call, ``tabData(index)`` may return ``""`` — a
    menu built from that would emit the empty path and silently
    clear the clipboard / fail to open Explorer.
    """
    _ensure_app()
    vm = RepoTabViewModel()
    vm.add_tab(_make_repo("empty_data"))
    bar = RepoBarWidget(vm)
    qtbot.addWidget(bar)

    # Force the bar into a state where the tab exists but its data is
    # empty — exact replica of a stale ``setTabData`` race.
    bar._tab_bar.setTabData(0, "")  # noqa: SLF001

    emitted: list[str] = []
    bar.show_folder_requested.connect(emitted.append)

    rect = bar._tab_bar.tabRect(0)  # noqa: SLF001
    if rect.isValid():
        bar._on_tab_context_menu(rect.center())  # noqa: SLF001

    assert emitted == []
