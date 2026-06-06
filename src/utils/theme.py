"""Application theming: dark/light colour palettes + Qt stylesheets.

Per ``docs/DEVELOPMENT_RULES.md`` (section 7), theme parameters live
in JSON config and are persisted on exit / restored on launch. The
default in :mod:`src.utils.config` is ``"theme": "dark"``; this module
is the single source of truth for what that means in pixels.

Architecture
------------
* :class:`Theme` — a frozen dataclass holding every colour the app
  uses. Adding a new colour means adding a field here **and** to
  :data:`DARK_THEME` (and any future ``LIGHT_THEME``).
* :func:`stylesheet_for_theme` — pure function ``Theme -> str`` that
  produces a Qt stylesheet. Keeping the generator pure makes it
  trivial to unit-test (the test feeds a synthetic theme and asserts
  on the output).
* :func:`apply_theme` — wires the stylesheet into a running
  :class:`QApplication`. Safe to call multiple times; it just replaces
  the current stylesheet.
* :func:`get_theme` — config-string lookup. Unknown values fall back
  to :data:`DARK_THEME` and emit a ``UserWarning`` so a misconfigured
  config file does not crash the app.

Scope
-----
* The graph widget's :class:`RenderConfig` reads its background /
  text / edge / selection colours from :data:`DARK_THEME` so the
  scene background matches the surrounding QGraphicsView and the
  rest of the app.
* The QSS covers the standard Qt widget catalogue used by the
  project: ``QMainWindow`` / ``QMenuBar`` / ``QMenu`` /
  ``QToolBar`` / ``QStatusBar`` / ``QSplitter`` / ``QTabWidget`` /
  ``QTreeWidget`` / ``QListWidget`` / ``QTextEdit`` /
  ``QPlainTextEdit`` / ``QLineEdit`` / ``QPushButton`` /
``QComboBox`` / ``QLabel`` / ``QDialog`` / ``QDialogButtonBox`` /
``QHeaderView`` / ``QTableWidget`` / ``QProgressBar`` / ``QToolTip``
/ ``QScrollBar`` / ``QGraphicsView``. Anything not styled falls
back to the platform default inside the dark surface (acceptable
for the messenger dialogs we do not own).

Light theme is scaffolded by :data:`_LIGHT_THEME_PLACEHOLDER`; the
config layer already supports ``"theme": "light"`` and :func:`get_theme`
will resolve it once a real palette is filled in (deferred to a later
sprint — the current ask is "apply the dark theme to the entire app").
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Theme:
    """A complete colour palette.

    Every field is a hex string (``"#RRGGBB"``). Field names are
    stable identifiers — they map 1-to-1 to the ``{name}`` tokens in
    :func:`stylesheet_for_theme`. Do not rename a field without
    updating both sides.
    """

    name: str

    # --- surfaces ---
    bg: str
    bg_panel: str
    bg_elevated: str
    bg_input: str
    bg_hover: str
    bg_selected: str
    bg_selected_inactive: str
    bg_alternate: str
    border: str
    border_focus: str

    # --- text ---
    text: str
    text_dim: str
    text_disabled: str
    text_on_accent: str

    # --- accent (links, focus rings, primary buttons) ---
    accent: str
    accent_hover: str
    accent_pressed: str

    # --- graph-widget specific (consumed by :class:`RenderConfig`) ---
    graph_selection: str
    graph_edge: str
    graph_wip: str
    graph_stash: str


# VS Code Dark+ inspired palette, extended with a few GitKraken-flavoured
# accents. The graph widget was using ``#1E1E1E`` / ``#D4D4D4`` /
# ``#8B8B8B`` / ``#FFFFFF`` / ``#5A5A5A`` already — we keep those exact
# values and add neighbours so the surrounding widgets blend in.
DARK_THEME = Theme(
    name="dark",

    bg="#1E1E1E",
    bg_panel="#252526",
    bg_elevated="#2D2D30",
    bg_input="#1E1E1E",
    bg_hover="#2A2D2E",
    bg_selected="#094771",
    bg_selected_inactive="#37373D",
    bg_alternate="#2A2A2A",
    border="#3F3F46",
    border_focus="#007ACC",

    text="#D4D4D4",
    text_dim="#8B8B8B",
    text_disabled="#6A6A6A",
    text_on_accent="#FFFFFF",

    accent="#007ACC",
    accent_hover="#1F8AD2",
    accent_pressed="#005A9E",

    graph_selection="#FFFFFF",
    graph_edge="#343434",
    graph_wip="#505050",
    graph_stash="#D4A259",
)


# Placeholder for future light theme. Kept here so :func:`get_theme`
# can resolve ``"light"`` without raising; the real palette will
# replace this once ``Stage 9`` adds a "Theme" menu.
_LIGHT_THEME_PLACEHOLDER = Theme(
    name="light",
    bg="#FFFFFF",
    bg_panel="#F3F3F3",
    bg_elevated="#FAFAFA",
    bg_input="#FFFFFF",
    bg_hover="#E5E5E5",
    bg_selected="#0078D7",
    bg_selected_inactive="#E5E5E5",
    bg_alternate="#FAFAFA",
    border="#D4D4D4",
    border_focus="#0078D7",
    text="#1F1F1F",
    text_dim="#6A6A6A",
    text_disabled="#A0A0A0",
    text_on_accent="#FFFFFF",
    accent="#0078D7",
    accent_hover="#106EBE",
    accent_pressed="#005A9E",
    graph_selection="#0078D7",
    graph_edge="#A0A0A0",
    graph_wip="#8A8A8A",
    graph_stash="#D4A259",
)


# Registry: a config name (``"theme"`` value) → :class:`Theme`. The
# placeholder light theme is intentionally not registered yet — we
# want callers to get a clear warning if they try to use it.
_THEMES: dict[str, Theme] = {
    "dark": DARK_THEME,
}


def get_theme(name: str) -> Theme:
    """Return the theme registered under ``name``, falling back to dark.

    Unknown names (including the not-yet-implemented ``"light"``) emit
    a :class:`UserWarning` and return :data:`DARK_THEME`. This makes
    the app degrade gracefully when a config file references a theme
    the current build does not know about.
    """
    theme = _THEMES.get(name)
    if theme is None:
        warnings.warn(
            f"Unknown theme {name!r}; falling back to 'dark'.",
            UserWarning,
            stacklevel=2,
        )
        return DARK_THEME
    return theme


# ---------------------------------------------------------------------------
# QSS generation
# ---------------------------------------------------------------------------


def stylesheet_for_theme(theme: Theme) -> str:
    """Return a Qt stylesheet that paints every widget in ``theme``.

    The template uses simple ``{name}`` substitution so a test can
    feed a synthetic theme and assert the right colours land in the
    right selectors.
    """
    return _QSS_TEMPLATE.format(**theme.__dict__)


_QSS_TEMPLATE = """\
/* === git-py dark theme === */

QMainWindow,
QDialog {{
    background-color: {bg};
    color: {text};
}}

QWidget {{
    color: {text};
}}

/* --- repo tab bar --- */

QToolBar#repo-tab-toolbar {{
    background-color: {bg_elevated};
    border-bottom: none;
    padding: 0;
    spacing: 0;
    min-height: 28;
    max-height: 32;
}}

QToolBar#repo-tab-toolbar::separator {{
    background: transparent;
    width: 0;
    margin: 0;
}}

QWidget#repo-bar {{
    background-color: {bg_elevated};
}}

QWidget#repo-bar > QTabBar {{
    background-color: {bg_elevated};
    qproperty-drawBase: 0;
    font-size: 12px;
}}

QWidget#repo-bar > QTabBar::tab {{
    background-color: {bg_panel};
    color: {text_dim};
    padding: 1px 7px 6px 10px;
    border: 1px solid transparent;
    border-right: 1px solid {border};
    margin: 0;
    border-top-left-radius: 2px;
    border-top-right-radius: 2px;
}}

QWidget#repo-bar > QTabBar::tab:hover {{
    background-color: {bg_hover};
    color: {text};
}}

QWidget#repo-bar > QTabBar::tab:selected {{
    background-color: {bg};
    color: {text};
    border-color: {border};
    border-bottom-color: {bg};
}}

/* --- floating close button for repo tabs --- */
QPushButton#tab-close-btn {{
    background: transparent;
    color: {text_dim};
    border: none;
    font-size: 14px;
    font-weight: normal;
    padding: 0;
    margin: 0;
}}

QPushButton#tab-close-btn:hover {{
    background: transparent;
    color: {text};
}}

QPushButton#repo-add-btn {{
    background-color: transparent;
    color: {text_dim};
    font-size: 16px;
    font-weight: bold;
    border: none;
    border-radius: 2px;
    padding: 0;
    margin: 2px 4px 2px 2px;
}}

QPushButton#repo-add-btn:hover {{
    background-color: {bg_hover};
    color: {text};
}}

/* --- menus / toolbars / status bar --- */

QMenuBar {{
    background-color: {bg_elevated};
    color: {text};
    border-bottom: 1px solid {border};
    padding: 2px 4px;
}}

QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
    border-radius: 2px;
}}

QMenuBar::item:selected {{
    background-color: {bg_hover};
}}

QMenu {{
    background-color: {bg_elevated};
    color: {text};
    border: 1px solid {border};
    padding: 4px 0;
}}

QMenu::item {{
    padding: 5px 24px 5px 24px;
    border: none;
}}

QMenu::item:selected {{
    background-color: {bg_selected};
    color: {text_on_accent};
}}

QMenu::separator {{
    height: 1px;
    background: {border};
    margin: 4px 8px;
}}

QToolBar {{
    background-color: {bg_elevated};
    border-bottom: 1px solid {border};
    padding: 2px;
    spacing: 4px;
}}

QToolBar::separator {{
    background: {border};
    width: 1px;
    margin: 4px 4px;
}}

QToolButton {{
    background: transparent;
    color: {text};
    padding: 4px 8px;
    border: 1px solid transparent;
    border-radius: 2px;
}}

QToolButton:hover {{
    background-color: {bg_hover};
    border-color: {border};
}}

QToolButton:pressed {{
    background-color: {bg_selected};
}}

QToolButton:disabled {{
    color: {text_disabled};
}}

QStatusBar {{
    background-color: {bg_elevated};
    color: {text};
    border-top: 1px solid {border};
}}

QStatusBar::item {{
    border: none;
}}

/* --- splitters / tabs --- */

QSplitter::handle {{
    background-color: {border};
}}

QSplitter::handle:horizontal {{
    width: 1px;
}}

QSplitter::handle:vertical {{
    height: 1px;
}}

QTabWidget::pane {{
    border: 1px solid {border};
    background-color: {bg_panel};
    top: -1px;
}}

QTabBar {{
    background-color: {bg_elevated};
    qproperty-drawBase: 0;
}}

QTabBar::tab {{
    background-color: {bg_elevated};
    color: {text_dim};
    padding: 6px 14px;
    border: 1px solid transparent;
    border-bottom: none;
    margin-right: 1px;
}}

QTabBar::tab:hover {{
    color: {text};
}}

QTabBar::tab:selected {{
    background-color: {bg_panel};
    color: {text};
    border-color: {border};
    border-bottom-color: {bg_panel};
}}

/* --- inputs --- */

QLineEdit,
QPlainTextEdit,
QTextEdit {{
    background-color: {bg_input};
    color: {text};
    border: 1px solid {border};
    border-radius: 2px;
    padding: 4px;
    selection-background-color: {bg_selected};
    selection-color: {text_on_accent};
}}

QLineEdit:focus,
QPlainTextEdit:focus,
QTextEdit:focus {{
    border-color: {border_focus};
}}

QLineEdit:disabled,
QPlainTextEdit:disabled,
QTextEdit:disabled {{
    color: {text_disabled};
    background-color: {bg_panel};
}}

QPlainTextEdit,
QTextEdit {{
    font-family: Consolas, "Courier New", monospace;
}}

QComboBox {{
    background-color: {bg_input};
    color: {text};
    border: 1px solid {border};
    border-radius: 2px;
    padding: 4px 24px 4px 8px;
    selection-background-color: {bg_selected};
}}

QComboBox:hover {{
    border-color: {border_focus};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid {border};
}}

QComboBox QAbstractItemView {{
    background-color: {bg_elevated};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {bg_selected};
    selection-color: {text_on_accent};
    outline: 0;
}}

/* --- buttons --- */

QPushButton {{
    background-color: {bg_elevated};
    color: {text};
    border: 1px solid {border};
    border-radius: 2px;
    padding: 5px 14px;
    min-width: 60px;
}}

QPushButton:hover {{
    background-color: {bg_hover};
    border-color: {border_focus};
}}

QPushButton:pressed {{
    background-color: {bg_selected};
    color: {text_on_accent};
}}

QPushButton:disabled {{
    color: {text_disabled};
    background-color: {bg_panel};
    border-color: {border};
}}

QPushButton:default {{
    background-color: {accent};
    color: {text_on_accent};
    border-color: {accent};
}}

QPushButton:default:hover {{
    background-color: {accent_hover};
    border-color: {accent_hover};
}}

QPushButton:default:pressed {{
    background-color: {accent_pressed};
    border-color: {accent_pressed};
}}

QDialogButtonBox {{
    background: transparent;
}}

/* --- lists / trees / tables --- */

QListWidget,
QTreeWidget,
QTableWidget {{
    background-color: {bg_panel};
    color: {text};
    border: 1px solid {border};
    alternate-background-color: {bg_alternate};
    selection-background-color: {bg_selected};
    selection-color: {text_on_accent};
    outline: 0;
}}

QListWidget::item:hover,
QTreeWidget::item:hover {{
    background-color: {bg_hover};
}}

QListWidget::item:selected,
QTreeWidget::item:selected {{
    background-color: {bg_selected};
    color: {text_on_accent};
}}

QTreeWidget::branch {{
    background: transparent;
}}

QHeaderView::section {{
    background-color: {bg_elevated};
    color: {text_dim};
    padding: 4px 8px;
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
}}

QTableWidget {{
    gridline-color: {border};
}}

/* --- progress bar / scrollbars / tooltips --- */

QProgressBar {{
    background-color: {bg_panel};
    color: {text};
    border: 1px solid {border};
    border-radius: 2px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {accent};
    width: 12px;
}}

QToolTip {{
    background-color: {bg_elevated};
    color: {text};
    border: 1px solid {border};
    padding: 4px;
}}

QScrollBar:vertical {{
    background: {bg_panel};
    width: 12px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {bg_hover};
    min-height: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:vertical:hover {{
    background: {border_focus};
}}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {{
    height: 0;
    background: none;
}}

QScrollBar:horizontal {{
    background: {bg_panel};
    height: 12px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {bg_hover};
    min-width: 24px;
    border-radius: 3px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {border_focus};
}}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {{
    width: 0;
    background: none;
}}

/* --- graph widget --- */

QGraphicsView {{
    background-color: {bg};
    border: none;
}}

/* --- labels --- */

QLabel {{
    background: transparent;
    color: {text};
}}

QLabel:disabled {{
    color: {text_disabled};
}}
"""


def apply_theme(app: QApplication, theme: Theme) -> None:
    """Apply ``theme`` to ``app`` by setting a Qt stylesheet.

    Idempotent: calling it twice with the same theme is a no-op
    beyond re-installing the same stylesheet. Calling it with a
    different theme swaps the live stylesheet atomically — every
    widget re-styles on the next event-loop tick.
    """
    app.setStyleSheet(stylesheet_for_theme(theme))


__all__ = [
    "DARK_THEME",
    "Theme",
    "apply_theme",
    "get_theme",
    "stylesheet_for_theme",
]
