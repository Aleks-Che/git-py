"""Three-way merging preserves context, independent edits and file endings."""
import pytest
from src.core.conflict_resolution import ConflictSnapshot, compare_three_way


@pytest.mark.parametrize(("base", "ours", "theirs", "expected"), [
    ("a\nb\nc\n", "A\nb\nc\n", "a\nb\nC\n", "A\nb\nC\n"),
    ("a\nb\n", "A\nb\n", "a\nB\n", "A\nB\n"),
    ("a\nb\n", "a\nx\nb\n", "a\nb\ny\n", "a\nx\nb\ny\n"),
    ("a\nb\n", "b\n", "a\nb\nc", "b\nc"),
    ("old", "new", "new", "new"),
    ("", "new", "", "new"),
    ("old", "", "old", ""),
])
def test_independent_changes(base, ours, theirs, expected):
    regions = compare_three_way(base, ours, theirs)
    assert all(region.automatic is not None for region in regions)
    assert "".join(line for region in regions for line in region.automatic) == expected
    assert "".join(line for region in regions for line in region.ours) == ours
    assert "".join(line for region in regions for line in region.theirs) == theirs


@pytest.mark.parametrize(("base", "ours", "theirs"), [
    ("old", "left", "right"),
    ("", "left\n", "right\n"),
    ("old\n", "", "changed\n"),
    ("a\nb\nc\n", "a\nL1\nL2\nc\n", "a\nR\nc\n"),
])
def test_conflict_sides_roundtrip(base, ours, theirs):
    regions = compare_three_way(base, ours, theirs)
    assert any(region.automatic is None for region in regions)
    assert "".join(line for r in regions for line in r.ours) == ours
    assert "".join(line for r in regions for line in r.theirs) == theirs


def test_encoding_and_line_endings():
    snapshot = ConflictSnapshot("f", b"", "привет\r\n".encode("cp1251"), b"")
    assert snapshot.encode("мир\n") == "мир\r\n".encode("cp1251")
    assert snapshot.encode("мир") == "мир".encode("cp1251")
