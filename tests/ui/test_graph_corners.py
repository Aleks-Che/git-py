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
"""
from __future__ import annotations

import pytest
from PySide6.QtGui import QImage, QPainter
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
