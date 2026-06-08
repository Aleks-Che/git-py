"""Tests for :mod:`src.core.graph_debug`."""
from __future__ import annotations

import io

from src.core.graph_debug import check_graph_integrity, dump_graph_edges, dump_graph_rows


def _make_row(
    sha: str,
    row: int,
    lane: int,
    input_lanes: list[dict] | None = None,
    output_lanes: list[dict] | None = None,
) -> dict:
    return {
        "sha": sha,
        "row": row,
        "lane": lane,
        "display_column": lane,
        "color": "#ff0000",
        "refs": [],
        "branch_refs": [],
        "input_lanes": input_lanes or [],
        "output_lanes": output_lanes or [],
    }


def test_dump_graph_rows_with_empty_list() -> None:
    buf = io.StringIO()
    dump_graph_rows([], file=buf)
    assert "empty graph" in buf.getvalue()


def test_check_graph_integrity_empty_list() -> None:
    assert check_graph_integrity([]) == []


def test_check_graph_integrity_consistent_linear() -> None:
    rows = [
        _make_row(
            "c" * 40, 0, 0,
            input_lanes=[{"sha": "c" * 40, "color": "#f00"}],
            output_lanes=[{"sha": "b" * 40, "color": "#f00"}],
        ),
        _make_row(
            "b" * 40, 1, 0,
            input_lanes=[{"sha": "b" * 40, "color": "#f00"}],
            output_lanes=[],
        ),
    ]
    problems = check_graph_integrity(rows)
    assert problems == []


def test_check_graph_integrity_missing_sha_in_next_input() -> None:
    rows = [
        _make_row(
            "c" * 40, 0, 0,
            input_lanes=[{"sha": "c" * 40, "color": "#f00"}],
            output_lanes=[{"sha": "b" * 40, "color": "#f00"}],
        ),
        _make_row(
            "b" * 40, 1, 0,
            input_lanes=[],  # empty! b should be here
            output_lanes=[],
        ),
    ]
    problems = check_graph_integrity(rows)
    assert len(problems) >= 1
    assert any("not in next input_lanes" in p for p in problems)


def test_check_graph_integrity_duplicate_sha_in_output() -> None:
    rows = [
        _make_row(
            "c" * 40, 0, 0,
            input_lanes=[{"sha": "c" * 40, "color": "#f00"}],
            output_lanes=[
                {"sha": "b" * 40, "color": "#f00"},
                {"sha": "b" * 40, "color": "#0f0"},
            ],
        ),
        _make_row(
            "b" * 40, 1, 0,
            input_lanes=[
                {"sha": "b" * 40, "color": "#f00"},
                {"sha": "b" * 40, "color": "#0f0"},
            ],
            output_lanes=[],
        ),
    ]
    problems = check_graph_integrity(rows)
    assert len(problems) >= 1


def test_check_graph_integrity_commit_not_in_input_lanes() -> None:
    rows = [
        _make_row(
            "c" * 40, 0, 0,
            input_lanes=[{"sha": "d" * 40, "color": "#f00"}],
            output_lanes=[],
        ),
    ]
    problems = check_graph_integrity(rows)
    assert any("not found in its own input_lanes" in p for p in problems)


def test_dump_graph_edges_linear() -> None:
    buf = io.StringIO()
    rows = [
        _make_row(
            "c" * 40, 0, 0,
            input_lanes=[{"sha": "c" * 40, "color": "#f00"}],
            output_lanes=[{"sha": "b" * 40, "color": "#f00"}],
        ),
        _make_row(
            "b" * 40, 1, 0,
            input_lanes=[{"sha": "b" * 40, "color": "#f00"}],
            output_lanes=[],
        ),
    ]
    dump_graph_edges(rows, file=buf)
    output = buf.getvalue()
    assert "straight" in output
    assert "Row 0" in output


def test_dump_graph_edges_terminate() -> None:
    buf = io.StringIO()
    rows = [
        _make_row(
            "c" * 40, 0, 0,
            input_lanes=[{"sha": "c" * 40, "color": "#f00"}],
            output_lanes=[{"sha": "b" * 40, "color": "#f00"}],
        ),
        _make_row(
            "d" * 40, 1, 0,
            input_lanes=[{"sha": "d" * 40, "color": "#0f0"}],
            output_lanes=[],
        ),
    ]
    dump_graph_edges(rows, file=buf)
    output = buf.getvalue()
    assert "TERMINATES" in output
