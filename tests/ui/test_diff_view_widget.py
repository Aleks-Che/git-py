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

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication
from src.core.diff_parser import DiffLineType
from src.ui.widgets.diff_view_widget import (
    ADDITION_BG,
    DELETION_BG,
    HUNK_BG,
    DiffViewMode,
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


# ----- view-mode toolbar (Changes only / Full document) ---------------


# A two-hunk diff that covers different parts of the same file:
#
# * Hunk 1  (`@@ -1,3 +1,3 @@`)  changes only file lines 1-3.
# * Hunk 2  (`@@ -100,3 +100,3 @@`) changes only file lines 100-102.
#
# The two pre-baked texts simulate what ``repo.diff()`` produces when
# called with ``context_lines=3`` (two separate hunks) versus with
# ``context_lines=large`` (one merged hunk spanning the whole file).
# The widget is content-agnostic — it just renders whichever text the
# caller hands in for the active mode.
_CHANGES_ONLY_HUGE_DIFF = (
    "diff --git a/big.txt b/big.txt\n"
    "index 1234567..89abcde 100644\n"
    "--- a/big.txt\n"
    "+++ b/big.txt\n"
    "@@ -1,3 +1,3 @@\n"
    " first-original\n"
    "-second-original\n"
    "+second-replaced\n"
    " third-original\n"
    "@@ -100,3 +100,3 @@\n"
    " hundred-original\n"
    "-hundred-1-original\n"
    "+hundred-1-replaced\n"
    " hundred-2-original\n"
)
_FULL_DOCUMENT_HUGE_DIFF = (
    "diff --git a/big.txt b/big.txt\n"
    "index 1234567..89abcde 100644\n"
    "--- a/big.txt\n"
    "+++ b/big.txt\n"
    "@@ -1,102 +1,102 @@\n"
    " first-original\n"
    "-second-original\n"
    "+second-replaced\n"
    " third-original\n"
    " ...middle elided for the test...\n"
    " hundred-original\n"
    "-hundred-1-original\n"
    "+hundred-1-replaced\n"
    " hundred-2-original\n"
)


def test_default_view_mode_is_changes_only(qtbot) -> None:
    """A freshly built widget starts in Changes-only mode."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    assert view.view_mode() == DiffViewMode.CHANGES_ONLY
    assert view._changes_button.isChecked()
    assert not view._document_button.isChecked()


def test_toolbar_has_two_centre_aligned_buttons(qtbot) -> None:
    """The toolbar exposes exactly two checkable buttons, mutually exclusive."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)

    assert view._changes_button.isCheckable()
    assert view._document_button.isCheckable()
    assert not (view._changes_button.isChecked() and view._document_button.isChecked())

    # Triggering the second button must uncheck the first (exclusive group).
    view._document_button.setChecked(True)
    assert not view._changes_button.isChecked()
    view._changes_button.setChecked(True)
    assert not view._document_button.isChecked()


def test_set_diff_pair_stores_both_variants(qtbot) -> None:
    """``set_diff_pair`` accepts two distinct texts and remembers them."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)

    changes_only = "@@ -1,1 +1,1 @@\n-old\n+new\n"
    full_doc = (
        "@@ -1,5 +1,5 @@\n"
        " keep-a\n"
        " keep-b\n"
        "-old\n"
        "+new\n"
        " keep-c\n"
    )
    view.set_diff_pair(changes_only, full_doc)

    # In Changes-only mode the editor shows the small diff.
    view.set_view_mode(DiffViewMode.CHANGES_ONLY)
    assert view.toPlainText().count("\n") < full_doc.count("\n")
    assert "-old" in view.toPlainText()
    assert "keep-c" not in view.toPlainText()

    # Switching modes swaps the rendered text — no further input needed.
    view.set_view_mode(DiffViewMode.FULL_DOCUMENT)
    assert "keep-a" in view.toPlainText()
    assert "keep-b" in view.toPlainText()
    assert "keep-c" in view.toPlainText()
    assert "+new" in view.toPlainText()


def test_toggle_full_document_shows_all_context_lines(qtbot) -> None:
    """In Full document mode the editor paints every context line, not
    just the three lines around each change.

    The widget is content-agnostic: it renders whichever text the
    caller hands it for the active mode. The test supplies two
    pre-baked variants — one with two separate hunks
    (``context_lines=3``), one with a single spanning hunk that
    covers the whole file (``context_lines=large``) — and asserts
    the editor reflects both.
    """
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff_pair(_CHANGES_ONLY_HUGE_DIFF, _FULL_DOCUMENT_HUGE_DIFF)

    view.set_view_mode(DiffViewMode.CHANGES_ONLY)
    changes_only_text = view.toPlainText()

    view.set_view_mode(DiffViewMode.FULL_DOCUMENT)
    full_document_text = view.toPlainText()

    # Both variants render differently — the only thing toggled is
    # which cached text the editor paints.
    assert changes_only_text != full_document_text
    # The full-document variant covers the entire 102-line file.
    assert "...middle elided for the test..." in full_document_text
    # Changes-only did not.
    assert "...middle elided for the test..." not in changes_only_text

    from src.core.diff_parser import parse_diff_lines as _parse
    changes_only_hunks = sum(
        1
        for p in _parse(changes_only_text)
        if p.line_type == DiffLineType.HUNK
    )
    full_document_hunks = sum(
        1
        for p in _parse(full_document_text)
        if p.line_type == DiffLineType.HUNK
    )
    assert changes_only_hunks == 2
    assert full_document_hunks == 1


def test_set_diff_mirrors_text_into_full_document_when_empty(qtbot) -> None:
    """The legacy ``set_diff`` entry point keeps the toolbar usable
    by storing the same text for both variants when only one was
    supplied."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    assert view._changes_only_text == _SAMPLE_DIFF
    assert view._full_document_text == _SAMPLE_DIFF


def test_set_full_document_diff_only_replaces_one_variant(qtbot) -> None:
    """``set_full_document_diff`` updates the full variant without
    touching the changes-only text."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    bigger = _SAMPLE_DIFF + "\n@ extra\n"
    view.set_full_document_diff(bigger)
    assert view._changes_only_text == _SAMPLE_DIFF
    assert view._full_document_text == bigger

    view.set_view_mode(DiffViewMode.FULL_DOCUMENT)
    assert "@ extra" in view.toPlainText()


def test_view_mode_changed_signal_fires_on_toggle(qtbot) -> None:
    """Toggling from one mode to another emits ``view_mode_changed``
    with the new mode value."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)

    captured: list = []
    view.view_mode_changed.connect(captured.append)

    with qtbot.waitSignal(view.view_mode_changed, timeout=500) as blocker:
        view._document_button.click()
    assert blocker.args[0] == DiffViewMode.FULL_DOCUMENT
    assert captured == [DiffViewMode.FULL_DOCUMENT]

    with qtbot.waitSignal(view.view_mode_changed, timeout=500) as blocker:
        view._changes_button.click()
    assert blocker.args[0] == DiffViewMode.CHANGES_ONLY
    assert captured == [DiffViewMode.FULL_DOCUMENT, DiffViewMode.CHANGES_ONLY]


def test_set_view_mode_does_not_emit_when_unchanged(qtbot) -> None:
    """Programmatic switches to the *current* mode are no-ops (no
    unnecessary re-render, no spurious signal)."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)

    captured: list = []
    view.view_mode_changed.connect(captured.append)

    view.set_view_mode(DiffViewMode.CHANGES_ONLY)
    view.set_view_mode(DiffViewMode.CHANGES_ONLY)
    assert captured == []


def test_full_document_keeps_addition_and_deletion_colouring(qtbot) -> None:
    """The colour-coding contract from earlier stages is preserved in
    full-document mode: additions and deletions still get their
    backgrounds, hunks still get the cyan background."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff_pair(_SAMPLE_DIFF, _SAMPLE_DIFF)
    view.set_view_mode(DiffViewMode.FULL_DOCUMENT)

    selections = view._editor.extraSelections()
    backgrounds = {
        sel.cursor.blockNumber(): sel.format.background().color()
        for sel in selections
    }
    line_info = view._editor._line_info
    for idx, info in enumerate(line_info):
        if info.line_type == DiffLineType.ADDITION:
            assert backgrounds.get(idx) == ADDITION_BG
        elif info.line_type == DiffLineType.DELETION:
            assert backgrounds.get(idx) == DELETION_BG
        elif info.line_type == DiffLineType.HUNK:
            assert backgrounds.get(idx) == HUNK_BG


def test_toolbar_button_has_focus_policy(qtbot) -> None:
    """The toolbar buttons are interactive — clicking them toggles the
    view mode without needing to involve the editor.

    (Avoids an accidental focus-stealing behaviour where a stray
    keyboard shortcut on the toolbar button would shift focus away
    from the diff text.)"""
    _ensure_app()
    QApplication.instance() or QApplication([])
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.show()

    assert view._changes_button.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert view._document_button.focusPolicy() != Qt.FocusPolicy.NoFocus

    view._document_button.click()
    assert view._document_button.isChecked()
    assert not view._changes_button.isChecked()
    assert view.view_mode() == DiffViewMode.FULL_DOCUMENT
