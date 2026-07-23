"""Regression tests for ``src.utils.avatar.make_avatar_pixmap``.

Pins the update2 stage-A fix: circular node avatars must render the
full 5x5 identicon inside the clip (pixel-snapped grid, no white
matte) instead of the full-bleed grid that the circular clip cropped.
"""
from __future__ import annotations

from hashlib import md5

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from src.utils import avatar


def _qapp() -> None:
    QApplication.instance() or QApplication([])


def _grid_and_fg(seed: str) -> tuple[list[list[bool]], QColor]:
    h_bytes = md5(seed.encode()).digest()  # noqa: S324
    fg = QColor(avatar._AVATAR_COLORS[h_bytes[0] % len(avatar._AVATAR_COLORS)])
    grid = [[False] * 5 for _ in range(5)]
    bits = int.from_bytes(h_bytes[3:6], "big")
    for row in range(5):
        for col in range(3):
            if bits & (1 << (row * 3 + col)):
                grid[row][col] = True
                grid[row][4 - col] = True
    return grid, fg


def test_circle_avatar_edge_cells_fully_visible() -> None:
    """Every filled cell of the circle avatar keeps its centre pixel.

    Regression: the update1 unified generator drew the grid full-bleed
    (cell = size/5 = 3.8 px at size 19), so the circular clip sliced
    the outer cells and the badge looked cropped.
    """
    _qapp()
    size = 19  # graph node avatar size (node_radius=11 -> 2*11-3)
    for seed in ("alice@example.com", "bob@example.com", "carol@example.com"):
        pix = avatar.make_avatar_pixmap(seed, size, shape="circle", inner_border=False)
        img = pix.toImage()
        grid, fg = _grid_and_fg(seed)
        grid_d = max(5, (size - 2) // 5 * 5)
        cell_px = grid_d // 5
        offset = (size - grid_d) / 2.0
        filled = 0
        for row in range(5):
            for col in range(5):
                if not grid[row][col]:
                    continue
                filled += 1
                cx = int(col * cell_px + offset + cell_px / 2)
                cy = int(row * cell_px + offset + cell_px / 2)
                assert img.pixelColor(cx, cy) == fg, (
                    f"{seed}: cell ({row},{col}) centre not fg"
                )
        assert filled > 0


def test_circle_avatar_grid_fits_inside_clip() -> None:
    """The snapped grid square must lie inside the circular clip."""
    _qapp()
    size = 19
    grid_d = max(5, (size - 2) // 5 * 5)
    offset = (size - grid_d) / 2.0
    # Corners of the inscribed square sit on the clip circle, and the
    # grid is strictly inside the margin-1 ellipse at the mid-edges.
    assert offset >= avatar._AVATAR_MARGIN
    assert offset + grid_d <= size - avatar._AVATAR_MARGIN


def test_circle_avatar_no_inner_border_by_default_for_graph() -> None:
    """With ``inner_border=False`` no white matte ring is drawn."""
    _qapp()
    size = 19
    seed = "dave@example.com"
    pix = avatar.make_avatar_pixmap(seed, size, shape="circle", inner_border=False)
    img = pix.toImage()
    white = QColor("#FFFFFF")
    # Probe the ring where the matte used to be drawn (inset ~2 px).
    inset = avatar._INNER_BORDER_INSET + avatar._INNER_BORDER_PEN_WIDTH / 2
    probes = (
        (int(size / 2), int(inset)),
        (int(size / 2), int(size - inset)),
        (int(inset), int(size / 2)),
        (int(size - inset), int(size / 2)),
    )
    for x, y in probes:
        assert img.pixelColor(x, y) != white, f"white matte pixel at ({x},{y})"


def test_square_badge_keeps_full_bleed_grid_and_border() -> None:
    """The right-panel square badge must be unchanged by the fix."""
    _qapp()
    size = 28
    seed = "erin@example.com"
    pix = avatar.make_avatar_pixmap(seed, size, shape="square")
    img = pix.toImage()
    grid, fg = _grid_and_fg(seed)
    # Full-bleed grid: filled cells away from the matte ring keep fg.
    cell = size / 5.0
    fg_hits = 0
    for row in range(1, 4):
        for col in range(1, 4):
            if grid[row][col]:
                cx = int(col * cell + cell / 2)
                cy = int(row * cell + cell / 2)
                if img.pixelColor(cx, cy) == fg:
                    fg_hits += 1
    assert fg_hits > 0
    # White matte present by default: the ring pixel is whiter than
    # the same pixel rendered without the border (AA blends the matte
    # with whatever cell colour lies underneath).
    inset = avatar._INNER_BORDER_INSET + avatar._INNER_BORDER_PEN_WIDTH / 2
    probe = (int(size / 2), int(inset))
    no_border = avatar.make_avatar_pixmap(
        seed, size, shape="square", inner_border=False,
    ).toImage()
    with_c = img.pixelColor(*probe)
    without_c = no_border.pixelColor(*probe)
    lum = lambda c: c.red() + c.green() + c.blue()  # noqa: E731
    assert lum(with_c) > lum(without_c)
