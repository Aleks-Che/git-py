"""Themed toolbar icons drawn programmatically with ``QPainter``.

No asset files: every glyph is a small vector drawing on a 16×16
logical grid rendered into a 2× device-pixel-ratio pixmap, so the
icons stay crisp on high-DPI screens and recolour themselves by
re-rendering (no SVG engine dependency).

Each :func:`toolbar_icon` returns a :class:`QIcon` with three mode
pixmaps so Qt picks the right colour automatically:

* **Normal** — theme text colour (``#D4D4D4``).
* **Disabled** — theme disabled text colour (``#6A6A6A``); Qt shows
  it when the owning ``QAction`` is greyed out.
* **Active** — theme hover accent (``#1F8AD2``); the default widget
  style uses the Active pixmap on mouse-over, so buttons highlight
  on hover without any extra stylesheet rules.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

_ICON_SIZE = 16
_DPR = 2
_STROKE = 1.5

_COLOR_NORMAL = "#D4D4D4"
_COLOR_DISABLED = "#6A6A6A"
_COLOR_ACTIVE = "#1F8AD2"

DrawFn = Callable[[QPainter], None]


def _head_down(p: QPainter, tip: QPointF, wing: float = 2.8) -> None:
    p.drawLine(tip, QPointF(tip.x() - wing, tip.y() - wing))
    p.drawLine(tip, QPointF(tip.x() + wing, tip.y() - wing))


def _head_up(p: QPainter, tip: QPointF, wing: float = 2.8) -> None:
    p.drawLine(tip, QPointF(tip.x() - wing, tip.y() + wing))
    p.drawLine(tip, QPointF(tip.x() + wing, tip.y() + wing))


def _tray(p: QPainter) -> None:
    """Open-top tray (U shape) used by the pull / push glyphs."""
    path = QPainterPath(QPointF(2.5, 9.5))
    path.lineTo(2.5, 13.5)
    path.lineTo(13.5, 13.5)
    path.lineTo(13.5, 9.5)
    p.drawPath(path)


def _draw_undo(p: QPainter) -> None:
    # Elbow: vertical tail up from the bottom right, bending left.
    path = QPainterPath(QPointF(13.5, 13.5))
    path.lineTo(13.5, 10.0)
    path.quadTo(QPointF(13.5, 8.0), QPointF(11.5, 8.0))
    path.lineTo(4.5, 8.0)
    p.drawPath(path)
    tip = QPointF(4.5, 8.0)
    p.drawLine(tip, QPointF(7.5, 5.0))
    p.drawLine(tip, QPointF(7.5, 11.0))


def _draw_redo(p: QPainter) -> None:
    path = QPainterPath(QPointF(2.5, 13.5))
    path.lineTo(2.5, 10.0)
    path.quadTo(QPointF(2.5, 8.0), QPointF(4.5, 8.0))
    path.lineTo(11.5, 8.0)
    p.drawPath(path)
    tip = QPointF(11.5, 8.0)
    p.drawLine(tip, QPointF(8.5, 5.0))
    p.drawLine(tip, QPointF(8.5, 11.0))


def _draw_fetch(p: QPainter) -> None:
    # Down arrow onto a detached baseline ("download").
    p.drawLine(QPointF(8.0, 2.0), QPointF(8.0, 10.5))
    _head_down(p, QPointF(8.0, 10.5))
    p.drawLine(QPointF(2.5, 14.0), QPointF(13.5, 14.0))


def _draw_pull(p: QPainter) -> None:
    _tray(p)
    p.drawLine(QPointF(8.0, 2.0), QPointF(8.0, 10.5))
    _head_down(p, QPointF(8.0, 10.5))


def _draw_push(p: QPainter) -> None:
    _tray(p)
    p.drawLine(QPointF(8.0, 11.5), QPointF(8.0, 2.5))
    _head_up(p, QPointF(8.0, 2.5))


def _stash_box(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(2.5, 5.0, 11.0, 9.0), 1.4, 1.4)


def _draw_stash_push(p: QPainter) -> None:
    _stash_box(p)
    p.drawLine(QPointF(8.0, 7.0), QPointF(8.0, 10.5))
    _head_down(p, QPointF(8.0, 11.2), wing=1.8)


def _draw_stash_pop(p: QPainter) -> None:
    _stash_box(p)
    p.drawLine(QPointF(8.0, 11.5), QPointF(8.0, 8.0))
    _head_up(p, QPointF(8.0, 7.3), wing=1.8)


def _draw_search(p: QPainter) -> None:
    p.drawEllipse(QRectF(2.5, 2.5, 8.0, 8.0))
    p.drawLine(QPointF(9.2, 9.2), QPointF(13.5, 13.5))


_DRAWERS: dict[str, DrawFn] = {
    "undo": _draw_undo,
    "redo": _draw_redo,
    "fetch": _draw_fetch,
    "pull": _draw_pull,
    "push": _draw_push,
    "stash_push": _draw_stash_push,
    "stash_pop": _draw_stash_pop,
    "search": _draw_search,
}

_CACHE: dict[str, QIcon] = {}


def _render(draw: DrawFn, color: str) -> QPixmap:
    pm = QPixmap(_ICON_SIZE * _DPR, _ICON_SIZE * _DPR)
    pm.setDevicePixelRatio(_DPR)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), _STROKE)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    draw(painter)
    painter.end()
    return pm


def toolbar_icon(name: str) -> QIcon:
    """Return the themed :class:`QIcon` for ``name`` (see ``_DRAWERS``)."""
    icon = _CACHE.get(name)
    if icon is None:
        draw = _DRAWERS[name]
        icon = QIcon()
        icon.addPixmap(_render(draw, _COLOR_NORMAL), QIcon.Mode.Normal, QIcon.State.Off)
        icon.addPixmap(_render(draw, _COLOR_DISABLED), QIcon.Mode.Disabled, QIcon.State.Off)
        icon.addPixmap(_render(draw, _COLOR_ACTIVE), QIcon.Mode.Active, QIcon.State.Off)
        _CACHE[name] = icon
    return icon


def icon_names() -> list[str]:
    """All glyph names understood by :func:`toolbar_icon`."""
    return sorted(_DRAWERS)
