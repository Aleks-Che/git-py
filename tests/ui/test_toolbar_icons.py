"""Tests for the icon-only main toolbar redesign.

The Edit / Remote / Stash toolbars render their actions as icons
(``ToolButtonIconOnly``); the original action text moves to the
tooltip together with the keyboard shortcut. The commit search bar
sits at the right edge of the row, pushed there by an expanding
spacer, and keeps a fixed compact width instead of stretching
across the whole window.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import QApplication, QSizePolicy, QToolBar
from src.ui.main_window import MainWindow


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _make_window(qtbot) -> MainWindow:
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    return win


def _find_toolbar(window: MainWindow, object_name: str) -> QToolBar:
    for tb in window.findChildren(QToolBar):
        if tb.objectName() == object_name:
            return tb
    raise AssertionError(f"Toolbar {object_name!r} not found")


_ICON_ACTIONS = (
    ("_action_undo", "Undo"),
    ("_action_redo", "Redo"),
    ("_action_fetch", "Fetch from origin"),
    ("_action_pull", "Pull from origin"),
    ("_action_push", "Push to origin"),
    ("_action_stash_push", "Stash Changes"),
    ("_action_stash_pop", "Stash Pop"),
)


def test_toolbar_actions_have_icons(qtbot) -> None:
    win = _make_window(qtbot)
    for attr, _text in _ICON_ACTIONS:
        action = getattr(win, attr)
        assert not action.icon().isNull(), attr
        assert not action.icon().pixmap(QSize(18, 18)).isNull(), attr


def test_tooltips_carry_text_and_shortcut(qtbot) -> None:
    win = _make_window(qtbot)
    for attr, text in _ICON_ACTIONS:
        action = getattr(win, attr)
        tip = action.toolTip()
        assert text in tip, attr
        assert "&" not in tip, attr
        shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        if shortcut:
            assert shortcut in tip, attr


def test_toolbars_render_icons_only(qtbot) -> None:
    win = _make_window(qtbot)
    for object_name in ("edit-toolbar", "remote-toolbar", "stash-toolbar"):
        tb = _find_toolbar(win, object_name)
        assert tb.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly, object_name


def test_icon_has_distinct_disabled_pixmap(qtbot) -> None:
    win = _make_window(qtbot)
    icon = win._action_push.icon()  # noqa: SLF001
    normal = icon.pixmap(QSize(18, 18), QIcon.Mode.Normal).toImage()
    disabled = icon.pixmap(QSize(18, 18), QIcon.Mode.Disabled).toImage()
    assert normal != disabled


def test_search_bar_pushed_right_by_spacer(qtbot) -> None:
    win = _make_window(qtbot)
    tb = _find_toolbar(win, "search-toolbar")
    widgets = [tb.widgetForAction(a) for a in tb.actions()]
    assert len(widgets) == 2
    spacer, search = widgets
    assert spacer is not None and spacer is not win._search_bar  # noqa: SLF001
    assert spacer.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    assert search is win._search_bar  # noqa: SLF001


def test_search_bar_has_compact_fixed_width(qtbot) -> None:
    win = _make_window(qtbot)
    bar = win._search_bar  # noqa: SLF001
    # Fixed width: min == max, and small enough to never fill the row.
    assert bar.minimumWidth() == bar.maximumWidth()
    assert 0 < bar.maximumWidth() <= 400
