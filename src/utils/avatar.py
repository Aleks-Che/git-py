"""Identicon author-avatar pixmaps shared across widgets.

Produces a 5×5 horizontally-mirrored block pattern derived from
the MD5 of a seed (typically an author email). The same algorithm is
used by the graph node chips and the commit-detail header so that
the same person is rendered identically everywhere.

Shape options:
* ``"square"`` — slightly-rounded square (corner radius 1 px).
* ``"circle"`` — fully circular clip, used inside commit-node dots.

Both shapes get a thin white matte inset near the edge so the avatar
reads as a "framed" badge against the dark panel background.

Callers are expected to cache the returned pixmap keyed by
``(seed, size, shape)`` — the function performs no caching of its
own. The pixmap is transparent except where the foreground pattern
sits, so it composites cleanly over any background.
"""
from __future__ import annotations

from hashlib import md5

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPixmap

# A small palette of solid colours for the avatar foreground. Picked
# to look distinct from the branch palette colours used elsewhere.
_AVATAR_COLORS: tuple[str, ...] = (
    "#C44A2B", "#B85C8C", "#9A6E3A", "#5B7FA5",
    "#8B5CF6", "#3B82A0", "#D97706", "#6D8EA0",
)
_AVATAR_BG = "#F4F4F4"

# Visual constants. Captured as module-level so tests / callers can
# read them if needed without re-deriving the magic numbers.
# ``_RECT_RADIUS`` is intentionally small (1 px): the avatar reads as
# a near-square with just a hint of softening so it stays distinct
# from the panel chrome.
_RECT_RADIUS = 1.0
_AVATAR_MARGIN = 1.0
_INNER_BORDER_INSET = 2.0
_INNER_BORDER_PEN_WIDTH = 1.5
_INNER_BORDER_COLOR = "#FFFFFF"


def make_avatar_pixmap(
    seed: str, size: int, *, shape: str = "square",
) -> QPixmap:
    """Return a ``size``×``size`` identicon pixmap for ``seed``.

    ``shape`` is ``"square"`` (default, slightly rounded square) or
    ``"circle"`` (circular clip). The seed is normally the author's
    email; fall back to the author's name, then to ``"?"`` when
    neither is available.
    """
    if not seed:
        seed = "?"
    h_bytes = md5(seed.encode()).digest()  # noqa: S324
    fg = QColor(_AVATAR_COLORS[h_bytes[0] % len(_AVATAR_COLORS)])
    bg = QColor(_AVATAR_BG)

    # 5×5 symmetric grid — left 3 columns from hash, right 2 are
    # the horizontal mirror.
    grid = [[False] * 5 for _ in range(5)]
    bits = int.from_bytes(h_bytes[3:6], "big")
    for row in range(5):
        for col in range(3):
            if bits & (1 << (row * 3 + col)):
                grid[row][col] = True
                grid[row][4 - col] = True

    cell = size / 5.0
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Clip to the requested shape.
    clip = QPainterPath()
    margin = _AVATAR_MARGIN
    d = size - margin * 2
    if shape == "circle":
        clip.addEllipse(QRectF(margin, margin, d, d))
    else:
        clip.addRoundedRect(
            QRectF(margin, margin, d, d), _RECT_RADIUS, _RECT_RADIUS,
        )
    painter.setClipPath(clip)

    # Background.
    painter.setBrush(QBrush(bg))
    painter.setPen(QPen(Qt.PenStyle.NoPen))
    painter.drawRect(QRectF(0, 0, size, size))

    # Filled cells.
    painter.setBrush(QBrush(fg))
    for row in range(5):
        for col in range(5):
            if grid[row][col]:
                painter.drawRect(QRectF(col * cell, row * cell, cell, cell))

    # White inner matte — a thin white ring inset from the avatar
    # edge so the badge reads as a framed square against the dark
    # panel background. Drawn on top of the cells so the ring stays
    # continuous regardless of which cells are filled.
    painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
    painter.setPen(QPen(QColor(_INNER_BORDER_COLOR), _INNER_BORDER_PEN_WIDTH))
    inset = _INNER_BORDER_INSET
    inner = size - inset * 2
    if shape == "circle":
        painter.drawEllipse(QRectF(inset, inset, inner, inner))
    else:
        painter.drawRoundedRect(
            QRectF(inset, inset, inner, inner),
            max(0.0, _RECT_RADIUS - 1.0),
            max(0.0, _RECT_RADIUS - 1.0),
        )

    painter.end()
    return pix


__all__ = ["make_avatar_pixmap"]
