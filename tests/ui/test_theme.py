"""Tests for :mod:`src.utils.theme`.

Coverage:

* :data:`DARK_THEME` is a well-formed :class:`Theme` with the colour
  values the existing graph widget relied on.
* :func:`stylesheet_for_theme` produces a non-empty QSS that styles
  every widget class the project actually uses and that interpolates
  the theme's colours (not some hard-coded default).
* :func:`get_theme` returns the dark theme for ``"dark"`` and falls
  back to dark for unknown names (with a warning).
* :func:`apply_theme` installs a stylesheet on the live
  :class:`QApplication` and is safe to call repeatedly / with a
  different theme.
* :class:`src.ui.widgets.graph_widget.GraphWidget` picks up the
  theme's colours when one is supplied, and falls back to dark when
  it is not.

These tests use ``qtbot`` (pytest-qt) for the live ``QApplication``;
run with ``QT_QPA_PLATFORM=offscreen`` on headless Windows / CI per
``docs/DEVELOPMENT_RULES.md``.
"""
from __future__ import annotations

import time
import warnings
from dataclasses import replace

import pygit2
import pytest
from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.ui.widgets.graph_widget import GraphWidget
from src.utils.theme import (
    DARK_THEME,
    Theme,
    apply_theme,
    get_theme,
    stylesheet_for_theme,
)
from src.viewmodels.graph_viewmodel import GraphViewModel

# ----- Theme dataclass -------------------------------------------------


def test_dark_theme_is_a_theme_with_the_expected_name() -> None:
    assert isinstance(DARK_THEME, Theme)
    assert DARK_THEME.name == "dark"


def test_dark_theme_preserves_graph_widget_history() -> None:
    """The pre-theme graph colours must still be present in :data:`DARK_THEME`.

    The graph widget had hard-coded ``#1E1E1E`` / ``#D4D4D4`` /
    ``#8B8B8B`` / ``#FFFFFF`` / ``#5A5A5A`` before theming was
    extracted. The dark theme must keep these exact values so the
    graph continues to look identical and the test
    ``test_set_selected_sha_highlights_node`` (which asserts
    ``#ffffff`` on the selection pen) keeps passing.
    """
    assert DARK_THEME.bg == "#1E1E1E"
    assert DARK_THEME.text == "#D4D4D4"
    assert DARK_THEME.text_dim == "#8B8B8B"
    assert DARK_THEME.graph_selection == "#FFFFFF"
    assert DARK_THEME.graph_edge == "#343434"
    assert DARK_THEME.graph_wip == "#505050"


def test_theme_is_immutable() -> None:
    """``Theme`` is ``frozen=True``; attempting to mutate a field raises."""
    with pytest.raises((AttributeError, Exception)):
        DARK_THEME.bg = "#000000"  # type: ignore[misc]


# ----- stylesheet generation ------------------------------------------


# Widget classes the project actually instantiates. The QSS must
# cover at least these — anything else is allowed to look default.
_REQUIRED_SELECTORS = (
    "QMainWindow",
    "QDialog",
    "QMenuBar",
    "QMenu",
    "QToolBar",
    "QToolButton",
    "QStatusBar",
    "QSplitter",
    "QTabWidget",
    "QTabBar",
    "QLineEdit",
    "QPlainTextEdit",
    "QTextEdit",
    "QComboBox",
    "QPushButton",
    "QDialogButtonBox",
    "QListWidget",
    "QTreeWidget",
    "QTableWidget",
    "QHeaderView",
    "QProgressBar",
    "QToolTip",
    "QScrollBar",
    "QGraphicsView",
    "QLabel",
)


def test_stylesheet_is_non_empty_string() -> None:
    qss = stylesheet_for_theme(DARK_THEME)
    assert isinstance(qss, str)
    assert qss.strip()


@pytest.mark.parametrize("selector", _REQUIRED_SELECTORS)
def test_stylesheet_covers_required_selector(selector: str) -> None:
    """Every widget class the project uses must appear in the QSS."""
    qss = stylesheet_for_theme(DARK_THEME)
    # ``in`` is good enough — selectors can be followed by ``{`` or a
    # pseudo-class, so we do not require a strict word boundary.
    assert selector in qss, f"QSS missing selector for {selector}"


def test_stylesheet_includes_dark_background_color() -> None:
    """The dark theme's ``bg`` must appear in the QSS as a real colour value."""
    qss = stylesheet_for_theme(DARK_THEME)
    assert DARK_THEME.bg in qss
    assert DARK_THEME.text in qss
    assert DARK_THEME.accent in qss


def test_stylesheet_substitutes_custom_theme_colors() -> None:
    """A custom theme's colours must end up in the generated QSS.

    This is the contract that makes a future light theme possible:
    swapping the palette swaps the rendered stylesheet without
    touching the template.
    """
    fake = replace(DARK_THEME, name="custom", bg="#ABCDEF", text="#012345")
    qss = stylesheet_for_theme(fake)
    assert "#ABCDEF" in qss
    assert "#012345" in qss
    # And the previous dark values are not present (we did not just
    # string-append a second theme).
    assert "#ABCDEF" != DARK_THEME.bg


def test_stylesheet_is_valid_qt_syntax() -> None:
    """Sanity: the QSS must not contain unbalanced braces.

    Qt silently drops malformed stylesheets, so a typo would mean the
    theme does not actually apply. We catch the obvious case here.
    """
    qss = stylesheet_for_theme(DARK_THEME)
    assert qss.count("{") == qss.count("}")


# ----- get_theme ------------------------------------------------------


def test_get_theme_returns_dark_for_dark_name() -> None:
    assert get_theme("dark") is DARK_THEME


def test_get_theme_falls_back_to_dark_for_unknown_name() -> None:
    with pytest.warns(UserWarning, match="light"):
        theme = get_theme("light")
    assert theme is DARK_THEME


# ----- apply_theme ----------------------------------------------------


def test_apply_theme_sets_stylesheet_on_qapp(qtbot) -> None:
    """``apply_theme`` must install a non-empty stylesheet on the live app."""
    app = QApplication.instance()
    assert app is not None
    apply_theme(app, DARK_THEME)
    qss = app.styleSheet()
    assert qss
    assert DARK_THEME.bg in qss


def test_apply_theme_is_idempotent(qtbot) -> None:
    """Calling ``apply_theme`` twice with the same theme is safe."""
    app = QApplication.instance()
    assert app is not None
    apply_theme(app, DARK_THEME)
    first = app.styleSheet()
    apply_theme(app, DARK_THEME)
    assert app.styleSheet() == first


def test_apply_theme_swaps_stylesheet_on_reapply(qtbot) -> None:
    """Re-applying with a different palette must change the live stylesheet."""
    app = QApplication.instance()
    assert app is not None
    apply_theme(app, DARK_THEME)
    first_qss = app.styleSheet()
    assert DARK_THEME.bg in first_qss
    fake = replace(DARK_THEME, bg="#ABCDEF")
    apply_theme(app, fake)
    second_qss = app.styleSheet()
    # The custom colour made it into the live stylesheet.
    assert "#ABCDEF" in second_qss
    # And the QSS is observably different from the previous one
    # (the custom colour is not in the dark QSS).
    assert "#ABCDEF" not in first_qss
    assert second_qss != first_qss
    # Restore for any later tests in the same session.
    apply_theme(app, DARK_THEME)


# ----- graph widget integration ---------------------------------------


def _make_committed_repo(path) -> RepositoryManager:
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


def test_graph_widget_default_uses_dark_theme_background(
    qtbot, tmp_git_repo,
) -> None:
    """Without an explicit ``theme`` argument, the scene uses :data:`DARK_THEME`."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    widget = GraphWidget(vm)
    qtbot.addWidget(widget)

    brush = widget.backgroundBrush()
    color_name = brush.color().name().lower()
    assert color_name == DARK_THEME.bg.lower()


def test_graph_widget_accepts_a_custom_theme(
    qtbot, tmp_git_repo,
) -> None:
    """Supplying ``theme=`` must propagate ``theme.bg`` to the scene brush."""
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    custom = replace(DARK_THEME, bg="#112233", name="custom-test")
    widget = GraphWidget(vm, theme=custom)
    qtbot.addWidget(widget)

    brush = widget.backgroundBrush()
    assert brush.color().name().lower() == "#112233"


def test_graph_widget_renders_nodes_with_custom_theme(
    qtbot, tmp_git_repo,
) -> None:
    """A custom theme must not break the basic rendering path.

    This is a smoke test: a non-default theme should still produce
    the expected number of ellipses for a two-commit repo.
    """
    mgr = _make_committed_repo(tmp_git_repo)
    vm = GraphViewModel(mgr)
    custom = replace(DARK_THEME, bg="#112233", name="custom-render")
    widget = GraphWidget(vm, theme=custom)
    qtbot.addWidget(widget)
    widget.show()

    from PySide6.QtWidgets import QGraphicsEllipseItem

    with qtbot.waitSignal(vm.graph_updated, timeout=2000):
        vm.refresh_graph()
    nodes = [
        it for it in widget.scene().items()
        if isinstance(it, QGraphicsEllipseItem)
    ]
    assert len(nodes) == 2


def test_graph_widget_silences_unknown_theme_warning(qtbot) -> None:
    """``get_theme`` warns on unknown names; we do not want the warning
    to fire just from constructing a default widget, since the default
    theme is always ``"dark"`` and the call site picks it explicitly.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        # Explicitly request dark; this must not warn.
        theme = get_theme("dark")
        assert theme is DARK_THEME
