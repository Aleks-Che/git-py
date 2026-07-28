"""Regression tests for the graph corner-bend rendering.

When a connector corridor turns 90 degrees (merge / branch creation),
the horizontal arm of the cell feeding the bend must stop at the
bend's curve endpoint (half a lane from the corner's vertical pipe).
Previously it ran the full lane width, overlapping the curve and
making the corner look like a filled rectangle.

Covered here:

* :func:`_tee_horiz_len` — span computation for TEE cells.
* :func:`_horiz_span` — span computation for HORIZONTAL /
  HORIZONTAL_PIPE cells (explicit ``d`` trim wins over the corner
  rule).
* :func:`_draw_cell_row` — end-to-end check that the trimmed spans
  are what actually reaches the painter, by spying on
  ``_draw_horiz_line``.
* inter-row bridge colouring: palette index ``0`` (GREEN) is a
  legitimate cell colour, not a "missing" sentinel — a bridge below
  a green cell must stay green.
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QImage, QPainter
from src.core.graph_v2 import BRANCH_PALETTE
from src.ui.widgets import graph_panel as gp

LANE_W = 30.0


def _cell(t: int, c: int = 2, **extra) -> dict:
    cell = {"t": t, "c": c}
    cell.update(extra)
    return cell


# ---------------------------------------------------------------------------
# _tee_horiz_len
# ---------------------------------------------------------------------------


class TestTeeHorizLen:
    @pytest.mark.parametrize("corner", [gp._T_BRANCH_LEFT, gp._T_MERGE_LEFT])
    def test_right_arm_shortened_before_left_facing_corner(self, corner: int) -> None:
        cells = [_cell(gp._T_TEE_RIGHT), _cell(gp._T_EMPTY), _cell(corner)]
        assert gp._tee_horiz_len(cells, 0, 1, LANE_W) == LANE_W / 2

    @pytest.mark.parametrize("corner", [gp._T_BRANCH_RIGHT, gp._T_MERGE_RIGHT])
    def test_left_arm_shortened_before_right_facing_corner(self, corner: int) -> None:
        cells = [_cell(corner), _cell(gp._T_EMPTY), _cell(gp._T_TEE_LEFT)]
        assert gp._tee_horiz_len(cells, 2, -1, LANE_W) == -LANE_W / 2

    @pytest.mark.parametrize(
        "neighbour",
        [gp._T_BRANCH_RIGHT, gp._T_MERGE_RIGHT],  # facing away from the arm
    )
    def test_corner_facing_away_keeps_full_span(self, neighbour: int) -> None:
        cells = [_cell(gp._T_TEE_RIGHT), _cell(gp._T_EMPTY), _cell(neighbour)]
        assert gp._tee_horiz_len(cells, 0, 1, LANE_W) == LANE_W

    def test_no_corner_keeps_full_span(self) -> None:
        cells = [_cell(gp._T_TEE_RIGHT), _cell(gp._T_EMPTY), _cell(gp._T_PIPE)]
        assert gp._tee_horiz_len(cells, 0, 1, LANE_W) == LANE_W

    def test_corner_beyond_next_lane_keeps_full_span(self) -> None:
        cells = [
            _cell(gp._T_TEE_RIGHT),
            _cell(gp._T_EMPTY),
            _cell(gp._T_HORIZONTAL_PIPE),
            _cell(gp._T_EMPTY),
            _cell(gp._T_BRANCH_LEFT),
        ]
        assert gp._tee_horiz_len(cells, 0, 1, LANE_W) == LANE_W

    def test_out_of_range_neighbour_keeps_full_span(self) -> None:
        cells = [_cell(gp._T_TEE_RIGHT)]
        assert gp._tee_horiz_len(cells, 0, 1, LANE_W) == LANE_W
        assert gp._tee_horiz_len(cells, 0, -1, LANE_W) == -LANE_W


# ---------------------------------------------------------------------------
# _horiz_span
# ---------------------------------------------------------------------------


class TestHorizSpan:
    @pytest.mark.parametrize("t", [gp._T_HORIZONTAL, gp._T_HORIZONTAL_PIPE])
    def test_untrimmed_arm_shortened_before_corner(self, t: int) -> None:
        cells = [_cell(t), _cell(gp._T_EMPTY), _cell(gp._T_BRANCH_LEFT)]
        assert gp._horiz_span(cells, 0, cells[0], LANE_W) == LANE_W / 2

    def test_untrimmed_arm_without_corner_keeps_full_span(self) -> None:
        cells = [_cell(gp._T_HORIZONTAL), _cell(gp._T_EMPTY), _cell(gp._T_PIPE)]
        assert gp._horiz_span(cells, 0, cells[0], LANE_W) == LANE_W

    def test_explicit_trim_wins_over_corner_rule(self) -> None:
        cells = [
            _cell(gp._T_HORIZONTAL, d=-1),
            _cell(gp._T_EMPTY),
            _cell(gp._T_BRANCH_LEFT),
        ]
        assert gp._horiz_span(cells, 0, cells[0], LANE_W) == LANE_W / 2

    def test_explicit_left_trim_not_affected(self) -> None:
        cells = [
            _cell(gp._T_BRANCH_RIGHT),
            _cell(gp._T_EMPTY),
            _cell(gp._T_HORIZONTAL, d=1),
        ]
        assert gp._horiz_span(cells, 2, cells[2], LANE_W) == -LANE_W / 2

    def test_odd_cell_before_corner_draws_nothing(self) -> None:
        """Between-lanes cell whose right edge meets a corner bend: the
        bend's curve covers the whole half lane, span must be zero —
        this is the kilocode ``5c7978c2`` regression (``d == -1``
        incoming cell of an up-bend still ran flush under the curve).
        """
        cells = [
            _cell(gp._T_HORIZONTAL_PIPE, p=3),
            _cell(gp._T_HORIZONTAL, d=-1),
            _cell(gp._T_MERGE_LEFT),
        ]
        assert gp._horiz_span(cells, 1, cells[1], LANE_W) == 0.0

    def test_odd_cell_before_corner_untrimmed_draws_nothing(self) -> None:
        cells = [
            _cell(gp._T_HORIZONTAL_PIPE, p=3),
            _cell(gp._T_HORIZONTAL),
            _cell(gp._T_BRANCH_LEFT),
        ]
        assert gp._horiz_span(cells, 1, cells[1], LANE_W) == 0.0

    def test_odd_cell_without_corner_keeps_span(self) -> None:
        cells = [
            _cell(gp._T_HORIZONTAL_PIPE, p=3),
            _cell(gp._T_HORIZONTAL),
            _cell(gp._T_HORIZONTAL_PIPE, p=4),
        ]
        assert gp._horiz_span(cells, 1, cells[1], LANE_W) == LANE_W

    def test_odd_cell_trim_kept_without_corner(self) -> None:
        cells = [
            _cell(gp._T_HORIZONTAL_PIPE, p=3),
            _cell(gp._T_HORIZONTAL, d=-1),
            _cell(gp._T_PIPE),
        ]
        assert gp._horiz_span(cells, 1, cells[1], LANE_W) == LANE_W / 2

    def test_odd_cell_before_right_facing_corner_keeps_span(self) -> None:
        cells = [
            _cell(gp._T_HORIZONTAL_PIPE, p=3),
            _cell(gp._T_HORIZONTAL),
            _cell(gp._T_BRANCH_RIGHT),
        ]
        assert gp._horiz_span(cells, 1, cells[1], LANE_W) == LANE_W


# ---------------------------------------------------------------------------
# _draw_cell_row: the trimmed spans actually reach the painter
# ---------------------------------------------------------------------------


def _record_horiz_spans(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, float]]:
    recorded: list[tuple[float, float]] = []

    def fake_horiz(painter, x, y_center, lane_w, width, color) -> None:
        recorded.append((x, lane_w))

    monkeypatch.setattr(gp, "_draw_horiz_line", fake_horiz)
    return recorded


def _paint_row(cells: list[dict], qapp) -> None:  # noqa: ARG001 - app context
    img = QImage(400, 64, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    try:
        gp._draw_cell_row(
            painter, cells,
            col_left=10.0, lane_w=LANE_W, y_center=32.0,
            row_height=32.0, edge_width=3.0, node_radius=11.0,
        )
    finally:
        painter.end()


class TestDrawCellRowTrim:
    def test_tee_right_next_to_corner_draws_half_arm(
        self, qapp, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded = _record_horiz_spans(monkeypatch)
        cells = [
            _cell(gp._T_TEE_RIGHT, p=1),
            _cell(gp._T_EMPTY),
            _cell(gp._T_BRANCH_LEFT),
        ]
        _paint_row(cells, qapp)
        assert recorded == [(10.0, LANE_W / 2)]

    def test_tee_left_next_to_corner_draws_half_arm(
        self, qapp, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded = _record_horiz_spans(monkeypatch)
        cells = [
            _cell(gp._T_BRANCH_RIGHT),
            _cell(gp._T_EMPTY),
            _cell(gp._T_TEE_LEFT, p=1),
        ]
        _paint_row(cells, qapp)
        assert recorded == [(10.0 + LANE_W, -LANE_W / 2)]

    def test_merge_corridor_only_last_cell_trimmed(
        self, qapp, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A multi-lane merge corridor: every horizontal cell spans the
        full lane width except the one feeding the corner bend."""
        recorded = _record_horiz_spans(monkeypatch)
        cells = [
            _cell(gp._T_TEE_RIGHT, p=1),        # lane 0, idx 0
            _cell(gp._T_HORIZONTAL),            # idx 1
            _cell(gp._T_HORIZONTAL_PIPE, p=3),  # lane 1, idx 2
            _cell(gp._T_HORIZONTAL),            # idx 3
            _cell(gp._T_HORIZONTAL_PIPE, p=4),  # lane 2, idx 4
            _cell(gp._T_EMPTY),                 # idx 5
            _cell(gp._T_BRANCH_LEFT),           # corner at lane 3, idx 6
        ]
        _paint_row(cells, qapp)
        spans = [span for _, span in recorded]
        assert spans == [LANE_W, LANE_W, LANE_W, LANE_W, LANE_W / 2]

    def test_corridor_without_corner_keeps_full_spans(
        self, qapp, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorded = _record_horiz_spans(monkeypatch)
        cells = [
            _cell(gp._T_TEE_RIGHT, p=1),
            _cell(gp._T_HORIZONTAL),
            _cell(gp._T_HORIZONTAL_PIPE, p=3),
        ]
        _paint_row(cells, qapp)
        spans = [span for _, span in recorded]
        assert spans == [LANE_W, LANE_W, LANE_W]

    def test_up_bend_incoming_cell_draws_nothing(
        self, qapp, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exact kilocode ``5c7978c2`` row shape: the trimmed incoming
        cell (``d == -1``) of an up-bend must not emit any segment."""
        recorded = _record_horiz_spans(monkeypatch)
        cells = [
            _cell(gp._T_HORIZONTAL_PIPE, p=8),  # lane 0, idx 0
            _cell(gp._T_HORIZONTAL, d=-1),      # between lanes, idx 1
            _cell(gp._T_MERGE_LEFT),            # corner at lane 1, idx 2
        ]
        _paint_row(cells, qapp)
        spans = [span for _, span in recorded]
        assert spans == [LANE_W / 2, 0.0]


# ---------------------------------------------------------------------------
# Inter-row bridge colouring (GREEN = palette index 0 is a real colour)
# ---------------------------------------------------------------------------


def _bridge_pixel(panel, img_size=(400, 200), sample_y=None):
    img = QImage(img_size[0], img_size[1], QImage.Format.Format_ARGB32)
    img.fill(0xFF1E1E1E)
    painter = QPainter(img)
    try:
        panel._draw_cells(painter, panel._cfg.header_height)
    finally:
        painter.end()
    lane_w = panel._cfg.node_radius * 2 + 8
    x = int(panel._lane_x(0, lane_w))
    y0 = panel._row_y(0) + panel._cfg.row_height / 2
    y1 = panel._row_y(1) + panel._cfg.row_height / 2
    y = int((y0 + y1) / 2) if sample_y is None else sample_y
    return img.pixelColor(x, y)


def _mk_row(sha: str, lane: int, ci: int, cells: list[dict]) -> dict:
    return {
        "lane": lane,
        "color_index": ci,
        "commit": {
            "sha": sha,
            "short_sha": sha[:7],
            "subject": "s",
            "author_name": "t",
            "author_email": "t@e",
            "author_time": 0,
        },
        "cells": cells,
    }


def test_bridge_below_green_cell_stays_green(qapp) -> None:
    """The bridge pipe between a GREEN (palette index 0) cell above and
    a differently-coloured cell below must keep the GREEN colour.

    Regression (kilocode ``1246d989``): the bridge-colour lookup used
    ``0`` as the "not found" sentinel, so a bridge below a green cell
    was repainted with the lower row's colour — the segment right above
    a fork-merge CROSS showed the merge connector's colour instead of
    the forked branch's green.
    """
    from src.viewmodels.graph_viewmodel import GraphViewModel

    vm = GraphViewModel()
    panel = gp.GraphTableWidget(vm)
    qapp.processEvents()
    panel._rows = [
        _mk_row("a" * 40, 0, 0, [{"t": gp._T_COMMIT, "c": 0, "p": None}]),
        _mk_row("b" * 40, 0, 5, [{"t": gp._T_CROSS, "c": 5, "p": 0, "d": -1}]),
    ]
    color = _bridge_pixel(panel)
    expected = gp.QColor(BRANCH_PALETTE[0])
    assert color.rgb() == expected.rgb(), (
        f"bridge below a green cell painted {color.name()}, expected {expected.name()}"
    )


def test_bridge_below_coloured_cell_keeps_colour(qapp) -> None:
    """Control: a bridge below a non-zero-coloured cell keeps that colour."""
    from src.viewmodels.graph_viewmodel import GraphViewModel

    vm = GraphViewModel()
    panel = gp.GraphTableWidget(vm)
    qapp.processEvents()
    panel._rows = [
        _mk_row("a" * 40, 0, 3, [{"t": gp._T_COMMIT, "c": 3, "p": None}]),
        _mk_row("b" * 40, 0, 5, [{"t": gp._T_CROSS, "c": 5, "p": 0, "d": -1}]),
    ]
    color = _bridge_pixel(panel)
    expected = gp.QColor(BRANCH_PALETTE[3])
    assert color.rgb() == expected.rgb(), (
        f"bridge below a c=3 cell painted {color.name()}, expected {expected.name()}"
    )
