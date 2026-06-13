"""UI tests for :class:`DiffViewWidget`.

The widget is the read-only, colour-coded diff pane shown in place of
the graph when the user clicks a file in the commit panel. These tests
focus on two contracts the per-file diff viewer must honour:

1. File-header lines (``diff --git`` / ``---`` / ``+++`` / ``index``
   / mode lines) are **not** displayed — they're noise in a per-file
   view.
2. The gutter paints the **file** line number for each change
   (additions, deletions, context) — not a sequential 1, 2, 3 …
   diff index.

We also verify the highlight palette still applies for additions,
deletions, context, and the hunk separator.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from src.core.diff_parser import DiffLineType
from src.ui.widgets.diff_view_widget import (
    ADDITION_BG,
    DELETION_BG,
    HUNK_BG,
    DiffViewWidget,
)

# A realistic single-file unified diff covering every line type the
# parser knows about. ``@@ -10,3 +10,4 @@`` means "old file: 3 lines
# starting at line 10, new file: 4 lines starting at line 10".
_SAMPLE_DIFF = (
    "diff --git a/foo.txt b/foo.txt\n"
    "index 1234567..89abcde 100644\n"
    "--- a/foo.txt\n"
    "+++ b/foo.txt\n"
    "@@ -10,3 +10,4 @@\n"
    " keep-a\n"
    "-keep-b\n"
    "+keep-b-new\n"
    "+inserted\n"
    " keep-c\n"
    "@@ -50,2 +51,3 @@\n"
    " next\n"
    "-gone\n"
    "+replacement\n"
    "+extra\n"
)


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


# ----- structure: HEADER lines are stripped -------------------------


def test_set_diff_strips_file_header_lines(qtbot) -> None:
    """``diff --git`` / ``---`` / ``+++`` / ``index`` lines are noise
    in a per-file view and must not appear in the editor."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    text = view.toPlainText()
    assert "diff --git" not in text
    assert "index " not in text
    assert "--- a/foo.txt" not in text
    assert "+++ b/foo.txt" not in text
    # Hunk markers, additions, deletions, and context stay.
    assert "@@ -10,3 +10,4 @@" in text
    assert " keep-a" in text
    assert "-keep-b" in text
    assert "+keep-b-new" in text
    assert "+inserted" in text


def test_set_diff_preserves_hunk_markers(qtbot) -> None:
    """Hunk markers stay — they show the range of each change group."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    text = view.toPlainText()
    assert "@@ -10,3 +10,4 @@" in text
    assert "@@ -50,2 +51,3 @@" in text


def test_line_info_drops_headers_too(qtbot) -> None:
    """The internal line-info list is filtered in lockstep with the text."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    line_info = view._editor._line_info
    assert all(info.line_type != DiffLineType.HEADER for info in line_info)
    # 2 hunks + 5 body lines in hunk 1 + 4 body lines in hunk 2.
    assert len(line_info) == 11


# ----- gutter: real file line numbers -------------------------------


def test_line_info_records_file_line_numbers(qtbot) -> None:
    """Additions and context use the new counter, deletions the old one."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    line_info = view._editor._line_info
    # Hunk 1: @@ -10,3 +10,4 @@ → old=10, new=10. " keep-a" advances
    # both to 11/11. "-keep-b" sits on old=11, "+keep-b-new" on
    # new=11, "+inserted" on new=12, " keep-c" on new=13.
    # Hunk 2: @@ -50,2 +51,3 @@ → old=50, new=51. " next" (ctx)
    # new=51, advances to 52/51. "-gone" on old=51, "+replacement"
    # on new=52, "+extra" on new=53.
    assert [(info.line_type, info.line_number) for info in line_info] == [
        (DiffLineType.HUNK, 10),
        (DiffLineType.CONTEXT, 10),
        (DiffLineType.DELETION, 11),
        (DiffLineType.ADDITION, 11),
        (DiffLineType.ADDITION, 12),
        (DiffLineType.CONTEXT, 13),
        (DiffLineType.HUNK, 51),
        (DiffLineType.CONTEXT, 51),
        (DiffLineType.DELETION, 51),
        (DiffLineType.ADDITION, 52),
        (DiffLineType.ADDITION, 53),
    ]


def test_gutter_width_grows_with_max_line_number(qtbot) -> None:
    """A change at file line 999 needs a 4-digit gutter, not a 1-digit one."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    small_diff = "@@ -1,1 +999,1 @@\n-line\n+line\n"
    view.set_diff(small_diff)
    # The editor has 3 blocks (hunk + deletion + addition). The max
    # file line is 999. The gutter must be sized for 3 digits, not 1.
    wide = view._editor.gutter_width()
    view.set_diff("@@ -1,1 +1,1 @@\n-line\n+line\n")
    narrow = view._editor.gutter_width()
    assert wide > narrow


# ----- highlighting: colours still applied --------------------------


def test_highlighting_still_paints_additions_and_deletions(qtbot) -> None:
    """The colour-coding contract from earlier stages is preserved."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    selections = view._editor.extraSelections()
    backgrounds = {
        sel.cursor.blockNumber(): sel.format.background().color()
        for sel in selections
    }
    line_info = view._editor._line_info
    # Every ADDITION/DELETION/HUNK block must have its expected
    # background colour painted.
    for idx, info in enumerate(line_info):
        if info.line_type == DiffLineType.ADDITION:
            assert backgrounds.get(idx) == ADDITION_BG, (
                f"addition at block {idx} missing green background"
            )
        elif info.line_type == DiffLineType.DELETION:
            assert backgrounds.get(idx) == DELETION_BG, (
                f"deletion at block {idx} missing red background"
            )
        elif info.line_type == DiffLineType.HUNK:
            assert backgrounds.get(idx) == HUNK_BG, (
                f"hunk at block {idx} missing hunk background"
            )


# ----- clear / empty input -------------------------------------------


def test_clear_resets_text_and_line_info(qtbot) -> None:
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    assert view.toPlainText() != ""
    view.clear()
    assert view.toPlainText() == ""
    assert view._editor._line_info == []


def test_set_diff_with_empty_text_clears_the_view(qtbot) -> None:
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    view.set_diff("")
    assert view.toPlainText() == ""
    assert view._editor._line_info == []


# ----- real-world diff from pygit2 ----------------------------------


def test_set_diff_against_pygit2_patch_works(qtbot, tmp_git_repo) -> None:
    """End-to-end: a real ``pygit2.Diff.patch`` feeds cleanly into the widget."""
    from src.core.diff_parser import diff_to_text

    repo = tmp_git_repo
    (repo / "hello.txt").write_text("a\nb\nc\n")
    import pygit2

    sig = pygit2.Signature("t", "t@e", 1_700_000_000, 0)
    r = pygit2.Repository(str(repo))
    r.index.add("hello.txt")
    r.index.write()
    tree = r.index.write_tree()
    r.create_commit("refs/heads/main", sig, sig, "init", tree, [])
    (repo / "hello.txt").write_text("a\nB-new\nc\nd\n")
    r.index.add("hello.txt")
    r.index.write()
    tree2 = r.index.write_tree()
    head_tree = r[r.head.target].tree
    diff = r.diff(head_tree, tree2)
    patch = diff_to_text(diff)
    assert patch  # sanity

    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(patch)
    # No header lines leaked into the editor.
    assert "diff --git" not in view.toPlainText()
    # The first addition (B-new) sits on line 2 of the new file.
    additions = [
        i for i, info in enumerate(view._editor._line_info)
        if info.line_type == DiffLineType.ADDITION
    ]
    assert additions, "expected at least one addition in the diff"
    assert view._editor._line_info[additions[0]].line_number == 2
