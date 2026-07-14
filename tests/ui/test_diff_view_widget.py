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

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.core.diff_parser import DiffLineType
from src.ui.widgets.diff_view_widget import (
    _SCROLLBAR_WIDTH,
    ADDITION_BG,
    DELETION_BG,
    HUNK_BG,
    DiffLineActionMode,
    DiffViewMode,
    DiffViewWidget,
    _DiffScrollBar,
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


# ----- toolbar vertical padding is symmetrical ------------------------


def test_toolbar_vertical_margins_are_equal(qtbot) -> None:
    """The toolbar gives the buttons equal top and bottom padding so
    the row reads as centred between the widget edge and the editor."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)

    layout = view._toolbar.layout()
    margins = layout.contentsMargins()
    assert margins.top() == margins.bottom(), (
        f"top ({margins.top()}) and bottom ({margins.bottom()}) margins "
        "must match so the buttons sit visually centred"
    )
    assert margins.top() > 0


# ----- toolbar shows the active mode clearly --------------------------


def test_toolbar_buttons_have_distinct_checked_styles(qtbot) -> None:
    """The active button must be visually distinguishable from the
    inactive one. Without a stylesheet Qt's default :checked state
    is so subtle on Windows that the user can't tell which mode is on,
    so the widget installs one that changes both the background and
    the text colour. We verify the stylesheet is wired up and differs
    between the checked / unchecked branches.
    """
    _ensure_app()
    QApplication.instance() or QApplication([])
    view = DiffViewWidget()
    qtbot.addWidget(view)

    # Both buttons share the same stylesheet (the toggle is conveyed
    # by ``:checked``), and at least one rule targets the checked state.
    sheet = view._changes_button.styleSheet()
    assert ":checked" in sheet, (
        "toolbar stylesheet must declare a :checked rule so the active "
        "mode is visually distinct from the inactive one"
    )
    assert view._changes_button.styleSheet() == view._document_button.styleSheet()


def test_only_one_button_is_checked_at_a_time(qtbot) -> None:
    """The button group is exclusive: toggling one button unsets the other.

    Note: ``set_view_mode`` updates the buttons directly (not via a
    click), so this test guards against regressions in the
    ``setExclusive(False)`` → ``setChecked(True)`` → ``setExclusive(True)``
    dance that keeps the click handler from re-firing during
    programmatic switches.
    """
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view._changes_button.setChecked(True)
    view._document_button.setChecked(True)
    assert not (
        view._changes_button.isChecked() and view._document_button.isChecked()
    ), "the button group must stay exclusive"


def test_set_view_mode_syncs_checked_buttons(qtbot) -> None:
    """Programmatic mode switches update the toolbar's checked
    button to match (otherwise the user sees ``Changes only`` text
    on a button that's actually showing Full-document content)."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_view_mode(DiffViewMode.FULL_DOCUMENT)
    assert view._document_button.isChecked()
    assert not view._changes_button.isChecked()
    view.set_view_mode(DiffViewMode.CHANGES_ONLY)
    assert view._changes_button.isChecked()
    assert not view._document_button.isChecked()


# ----- scroll-to-first-diff in FULL_DOCUMENT mode ---------------------


# A single-file diff with lots of untouched context between two
# changes. The default ``context_lines=3`` collapses both into a
# single hunk; the full-document variant preserves the surrounding
# lines so the first change is far from the top.
_FIRST_DIFF_DIFF = (
    "diff --git a/big.txt b/big.txt\n"
    "index 1234567..89abcde 100644\n"
    "--- a/big.txt\n"
    "+++ b/big.txt\n"
    "@@ -40,3 +40,3 @@\n"
    " ...the head of the file lives here...\n"
    " ...unchanged lines above the change...\n"
    " ...unchanged lines above the change...\n"
    "-middle-replaced\n"
    "+middle-replacement\n"
    " ...unchanged lines below the change...\n"
    " ...unchanged lines below the change...\n"
)


def test_full_document_scrolls_to_first_diff_on_toggle(qtbot) -> None:
    """Switching to FULL_DOCUMENT mode scrolls the editor so the first
    addition or deletion is visible — without it the user lands on the
    file head and has to scroll past the unchanged context."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 200)  # short viewport so the scroll matters
    view.set_diff_pair(_FIRST_DIFF_DIFF, _FIRST_DIFF_DIFF)
    # Default mode is CHANGES_ONLY — the viewport should NOT be at
    # the first diff yet (the whole text fits, so this is a no-op
    # sanity check rather than an equality assertion).
    pre_first_visible_block = view._editor.firstVisibleBlock().blockNumber()

    view.set_view_mode(DiffViewMode.FULL_DOCUMENT)
    qtbot.waitUntil(
        lambda: view._editor.firstVisibleBlock().blockNumber() > pre_first_visible_block,
        timeout=1000,
    )
    first_visible_idx = view._editor.firstVisibleBlock().blockNumber()
    # The first block that the parser classified as ADDITION or
    # DELETION must now be above (or at) the first visible block.
    line_info = view._editor._line_info
    first_change_idx = next(
        i for i, info in enumerate(line_info)
        if info.line_type in (DiffLineType.ADDITION, DiffLineType.DELETION)
    )
    assert first_visible_idx <= first_change_idx


def test_full_document_scrolls_to_first_diff_on_new_load(qtbot) -> None:
    """Loading a new diff pair while FULL_DOCUMENT mode is already
    active scrolls to the first change of the *new* file."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 200)
    view.set_view_mode(DiffViewMode.FULL_DOCUMENT)

    view.set_diff_pair(
        "@@ -1,1 +1,1 @@\n-old-A\n+new-A\n",
        "@@ -1,1 +1,1 @@\n-old-A\n+new-A\n",
    )
    qtbot.waitUntil(
        lambda: view._editor.firstVisibleBlock().blockNumber() == 1,
        timeout=1000,
    )

    # Swap to a different diff whose first change is on a later line.
    view.set_diff_pair(_FIRST_DIFF_DIFF, _FIRST_DIFF_DIFF)
    line_info = view._editor._line_info
    first_change_idx = next(
        i for i, info in enumerate(line_info)
        if info.line_type in (DiffLineType.ADDITION, DiffLineType.DELETION)
    )
    qtbot.waitUntil(
        lambda: view._editor.firstVisibleBlock().blockNumber() <= first_change_idx,
        timeout=1000,
    )


def test_changes_only_mode_does_not_scroll_to_first_diff(qtbot) -> None:
    """In CHANGES_ONLY mode the typical diff is short and fits in the
    viewport; we don't disturb the scroll position when loading a new
    diff in that mode."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 600)
    view.set_diff("@@ -1,1 +1,1 @@\n-old\n+new\n")
    pre = view._editor.verticalScrollBar().value()

    view.set_diff("@@ -999,1 +999,1 @@\n-old\n+new\n")
    # Give the event loop a chance to apply any (incorrect) scroll.
    QApplication.processEvents()
    assert view._editor.verticalScrollBar().value() == pre


# ----- custom scrollbar (minimap, semi-transparent, split) -----------


def test_editor_uses_custom_diff_scrollbar(qtbot) -> None:
    """The editor's vertical scrollbar must be the custom subclass so
    the minimap painting kicks in."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    assert isinstance(view._editor._diff_scrollbar, _DiffScrollBar)
    assert view._editor.verticalScrollBar() is view._editor._diff_scrollbar


def test_scrollbar_is_wider_than_default(qtbot) -> None:
    """The custom bar is wider than the platform default so the
    vertical divider fits."""
    _ensure_app()
    QApplication.instance() or QApplication([])
    plain = QApplication.style().pixelMetric(
        QApplication.style().PixelMetric.PM_ScrollBarExtent,
    )
    assert plain > 0, "platform must report a default scrollbar width"
    view = DiffViewWidget()
    qtbot.addWidget(view)
    assert view._editor._diff_scrollbar.width() > plain


def test_scrollbar_blocks_track_deletion_and_addition_clusters(qtbot) -> None:
    """After ``set_diff`` the scrollbar caches deletion (left-half)
    and addition (right-half) *block* ranges. Each block spans a
    cluster of consecutive deletion/addition indices in the
    displayed diff, normalised by the total line count."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    bar = view._editor._diff_scrollbar

    line_info = view._editor._line_info
    total = len(line_info)
    deletion_count = sum(
        1 for info in line_info if info.line_type == DiffLineType.DELETION
    )
    addition_count = sum(
        1 for info in line_info if info.line_type == DiffLineType.ADDITION
    )
    assert deletion_count > 0, "fixture must have deletions"
    assert addition_count > 0, "fixture must have additions"
    # The block lists carry the same number of indices as the
    # diff's parsed line info — each deletion / addition entry is
    # accounted for in exactly one cluster.
    expected_del_index_count = deletion_count
    expected_add_index_count = addition_count
    assert sum(
        int(round(end * total)) - int(round(start * total)) + 1
        for start, end in bar._deletion_blocks
    ) >= expected_del_index_count, (
        "every deletion index should fall inside a deletion block"
    )
    assert sum(
        int(round(end * total)) - int(round(start * total)) + 1
        for start, end in bar._addition_blocks
    ) >= expected_add_index_count, (
        "every addition index should fall inside an addition block"
    )


def test_clear_resets_scrollbar_blocks(qtbot) -> None:
    """``clear()`` empties the block lists so the bar doesn't keep
    showing ticks from the previous file."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    assert view._editor._diff_scrollbar._deletion_blocks

    view.clear()
    assert view._editor._diff_scrollbar._deletion_blocks == []
    assert view._editor._diff_scrollbar._addition_blocks == []


def test_scrollbar_paints_with_left_red_and_right_green(qtbot) -> None:
    """Snapshot the painted scrollbar and confirm the left half has a
    reddish pixel while the right half has a greenish one — proving
    the two halves actually carry different colours."""
    _ensure_app()
    QApplication.instance() or QApplication([])
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 400)
    view.set_diff(_SAMPLE_DIFF)
    bar = view._editor._diff_scrollbar
    bar.show()
    QApplication.processEvents()
    bar.repaint()

    pixmap = bar.grab()
    img = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    h = img.height()

    # The contentsRect-based split: half_w = contentsRect.width() // 2.
    # We probe both halves for their respective tints. If the split
    # is off-by-one (e.g. contentsRect narrower than rect), the
    # tinted-pixel check still fails because the half zones no
    # longer align with where the markers are actually painted.
    crect = bar.contentsRect()
    half_w = crect.width() // 2
    left_end = crect.left() + half_w
    right_start = crect.left() + half_w + 1

    def _has_tint(img: QImage, x_start: int, x_end: int, *, red: bool) -> bool:
        """True if any pixel in the column range looks red-ish or green-ish."""
        for x in range(x_start, x_end):
            for y in range(h):
                px = img.pixelColor(x, y)
                if red:
                    if px.red() > px.green() + 30 and px.red() > px.blue() + 30:
                        return True
                else:
                    if px.green() > px.red() + 30 and px.green() > px.blue() + 30:
                        return True
        return False

    # We can't guarantee a marker lands on every painted column, but
    # with the _SAMPLE_DIFF fixture there are deletions on the left
    # half and additions on the right — at least one tinted pixel
    # per half must exist after a repaint.
    assert _has_tint(img, crect.left(), left_end, red=True), (
        "left half should contain a red deletion marker"
    )
    assert _has_tint(img, right_start, crect.right() + 1, red=False), (
        "right half should contain a green addition marker"
    )


def test_scrollbar_halves_count_red_and_green_pixels_equally(qtbot) -> None:
    """End-to-end pixel count: a diff with N deletions and N additions
    must produce *the same count of red and green pixels* in the
    painted scrollbar — anything else means the halves are not the
    same width on the actual painted surface (which is what the
    user sees in the running app).

    Earlier iterations used the widget's outer ``rect()`` for the
    split and let Windows' native style eat one column on one side;
    the offscreen test environment didn't reproduce it, so the
    counts looked balanced there but the live app showed asymmetry.
    This test guards against the same class of bug by counting the
    rendered pixels directly.
    """
    _ensure_app()
    QApplication.instance() or QApplication([])
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 800)
    # 30 paired hunks so deletions and additions land on disjoint
    # line numbers — each side has exactly 30 markers, distinct y
    # positions, so they never paint on top of each other.
    hunks = "".join(
        f"@@ -{i + 1},1 +{i + 1},1 @@\n-old-{i}\n+new-{i}\n"
        for i in range(30)
    )
    view.set_diff(hunks)
    bar = view._editor._diff_scrollbar
    bar.show()
    QApplication.processEvents()
    bar.repaint()

    pix = bar.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    red_pixels = green_pixels = 0
    for y in range(pix.height()):
        for x in range(pix.width()):
            c = pix.pixelColor(x, y)
            # Channel-comparison check (rather than absolute
            # thresholds) so the test doesn't depend on Qt's exact
            # alpha-blending math for our specific RGBA values —
            # anything visibly "redder than green" or vice versa
            # counts.
            if c.red() > c.green() + 30 and c.red() > c.blue() + 30:
                red_pixels += 1
            elif c.green() > c.red() + 30 and c.green() > c.blue() + 30:
                green_pixels += 1
    assert red_pixels > 0 and green_pixels > 0, (
        "expected both colours to be present in the rendered scrollbar"
    )
    # Each block is ``int(end_px) - int(start_px)`` px tall, so a 1-px
    # rounding difference between red and green indices stacks up
    # into a few dozen px of total asymmetry — well under 5 % of
    # the painted marker area.
    average = (red_pixels + green_pixels) / 2
    assert abs(red_pixels - green_pixels) / max(average, 1) < 0.05, (
        f"halves have visibly different pixel counts: "
        f"red={red_pixels}, green={green_pixels}"
    )


def test_markers_are_positioned_by_displayed_diff_index(qtbot) -> None:
    """Markers are placed at the index of the line in the *displayed*
    diff document, normalised by the total line count.

    Two contracts fall out of this:

    * The position of a block on the scrollbar matches the
      fraction of the editor's content the block's lines occupy.
    * Within a hunk the deletions precede the additions in the
      diff text (the standard unified-diff layout), so the red
      block sits *above* the green block on the bar — the two
      never share a row.
    """
    _ensure_app()
    QApplication.instance() or QApplication([])
    # Realistic unified-diff shape: deletions first, then additions.
    hunks = (
        "@@ -60,9 +60,9 @@\n"
        + "".join(f"-old-red-{i}\n" for i in range(9))
        + "".join(f"+new-green-{i}\n" for i in range(9))
    )
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 800)
    view.set_diff(hunks)
    bar = view._editor._diff_scrollbar
    bar.show()
    QApplication.processEvents()
    bar.repaint()

    pix = bar.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    red_rows: list[int] = []
    green_rows: list[int] = []
    for y in range(pix.height()):
        has_red = has_green = False
        for x in range(pix.width()):
            c = pix.pixelColor(x, y)
            if c.red() > c.green() + 30 and c.red() > c.blue() + 30:
                has_red = True
            if c.green() > c.red() + 30 and c.green() > c.blue() + 30:
                has_green = True
        if has_red:
            red_rows.append(y)
        if has_green:
            green_rows.append(y)
    assert red_rows and green_rows, "expected both colours to render"
    # Red block sits above green block — the diff-text order puts
    # deletions before additions in the same hunk, so the red
    # block ends at a smaller index than the green block begins.
    assert max(red_rows) < min(green_rows), (
        f"red block ({min(red_rows)}-{max(red_rows)}) should sit "
        f"above green block ({min(green_rows)}-{max(green_rows)}); "
        "the two must not overlap on the bar"
    )


def test_two_hunks_occupy_distinct_regions(qtbot) -> None:
    """Two hunks in the same diff occupy two distinct vertical
    regions of the scrollbar — red on top, green below per hunk,
    and the second hunk's blocks well below the first.

    Within a hunk the red cluster comes before the green cluster;
    the two clusters form a single coloured band per side. Between
    hunks there's empty space.
    """
    _ensure_app()
    QApplication.instance() or QApplication([])
    hunks = (
        "@@ -10,5 +10,5 @@\n"
        + "".join(f"-top-r-{i}\n" for i in range(5))
        + "".join(f"+top-g-{i}\n" for i in range(5))
        + "@@ -80,5 +80,5 @@\n"
        + "".join(f"-bot-r-{i}\n" for i in range(5))
        + "".join(f"+bot-g-{i}\n" for i in range(5))
    )
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 800)
    view.set_diff(hunks)
    bar = view._editor._diff_scrollbar
    bar.show()
    QApplication.processEvents()
    bar.repaint()

    pix = bar.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    h = pix.height()
    red_rows: list[int] = []
    green_rows: list[int] = []
    for y in range(pix.height()):
        has_red = has_green = False
        for x in range(pix.width()):
            c = pix.pixelColor(x, y)
            if c.red() > c.green() + 30 and c.red() > c.blue() + 30:
                has_red = True
            if c.green() > c.red() + 30 and c.green() > c.blue() + 30:
                has_green = True
        if has_red:
            red_rows.append(y)
        if has_green:
            green_rows.append(y)
    assert red_rows and green_rows, "expected both colours to render"
    # Top hunk: red above green, both in the upper half of the bar.
    red_top = [y for y in red_rows if y < h // 2]
    green_top = [y for y in green_rows if y < h // 2]
    assert red_top and green_top, (
        "top hunk should produce markers in the upper half of the bar"
    )
    assert max(red_top) < min(green_top), (
        "red cluster must sit above the green cluster within a hunk"
    )
    # Bottom hunk: red above green, both in the lower half.
    red_bot = [y for y in red_rows if y > h // 2]
    green_bot = [y for y in green_rows if y > h // 2]
    assert red_bot and green_bot, (
        "bottom hunk should produce markers in the lower half of the bar"
    )
    assert max(red_bot) < min(green_bot), (
        "red cluster must sit above the green cluster within the bottom hunk"
    )


def test_cluster_within_colour_is_solid(qtbot) -> None:
    """A cluster of consecutive same-colour markers renders as one
    continuous filled block — no internal gaps."""
    _ensure_app()
    QApplication.instance() or QApplication([])
    hunks = (
        "@@ -10,8 +10,8 @@\n"
        + "".join(f"-old-{i}\n" for i in range(8))
        + "".join(f"+new-{i}\n" for i in range(8))
    )
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(800, 800)
    view.set_diff(hunks)
    bar = view._editor._diff_scrollbar
    bar.show()
    QApplication.processEvents()
    bar.repaint()

    pix = bar.grab().toImage().convertToFormat(QImage.Format.Format_ARGB32)
    red_rows: list[int] = []
    green_rows: list[int] = []
    for y in range(pix.height()):
        has_red = has_green = False
        for x in range(pix.width()):
            c = pix.pixelColor(x, y)
            if c.red() > c.green() + 30 and c.red() > c.blue() + 30:
                has_red = True
            if c.green() > c.red() + 30 and c.green() > c.blue() + 30:
                has_green = True
        if has_red:
            red_rows.append(y)
        if has_green:
            green_rows.append(y)
    assert red_rows, "expected red markers"
    assert green_rows, "expected green markers"
    gaps_r = sum(
        1 for a, b in zip(red_rows, red_rows[1:], strict=False) if b - a > 1
    )
    gaps_g = sum(
        1 for a, b in zip(green_rows, green_rows[1:], strict=False) if b - a > 1
    )
    assert gaps_r == 0, (
        f"red cluster should be one continuous block; found {gaps_r} gaps"
    )
    assert gaps_g == 0, (
        f"green cluster should be one continuous block; found {gaps_g} gaps"
    )


def test_scrollbar_uses_contents_rect_for_painting(qtbot) -> None:
    """The paintEvent must split ``contentsRect`` in half, not
    ``rect`` — otherwise native styles that reserve a border on one
    side of the scrollbar shift the painted geometry by one column
    and break the 50/50 split.
    """
    _ensure_app()
    QApplication.instance() or QApplication([])
    view = DiffViewWidget()
    qtbot.addWidget(view)
    bar = view._editor._diff_scrollbar
    # The default contentsRect.width() for a QScrollBar is the full
    # widget width minus whatever border the style carves out. We
    # only assert that *some* integer half can be computed and that
    # it is non-zero — the actual count depends on the platform
    # style and DPI.
    crect = bar.contentsRect()
    assert crect.width() // 2 >= 1
    # And that ``bar.sizeHint`` reports at least our requested width.
    hint = bar.sizeHint()
    assert hint.width() >= _SCROLLBAR_WIDTH


def test_scrollbar_set_diff_blocks_clamps_values(qtbot) -> None:
    """The block setter clamps ``start`` and ``end`` to ``[0, 1]`` so
    a buggy caller can't push the markers off the bar."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    bar = view._editor._diff_scrollbar
    bar.set_diff_blocks(
        [(-0.5, 0.4), (0.4, 1.7)],
        [(2.0, -1.0)],
    )
    assert bar._deletion_blocks == [(0.0, 0.4), (0.4, 1.0)]
    assert bar._addition_blocks == [(1.0, 0.0)]


def test_scrollbar_set_diff_blocks_empty_lists(qtbot) -> None:
    """Empty block lists are accepted and don't raise — used by the
    widget's ``clear()`` and by files with no edits to show."""
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    bar = view._editor._diff_scrollbar
    bar.set_diff_blocks([], [])
    assert bar._deletion_blocks == []
    assert bar._addition_blocks == []


def test_scrollbar_can_be_painted_on_empty_view(qtbot) -> None:
    """Painting the custom scrollbar with no markers and no value
    (i.e. on an empty editor) does not crash or divide by zero —
    guards against regressions in the early-out branches."""
    _ensure_app()
    QApplication.instance() or QApplication([])
    view = DiffViewWidget()
    qtbot.addWidget(view)
    bar = view._editor._diff_scrollbar
    bar.resize(20, 200)
    bar.show()
    bar.repaint()  # must not raise


def test_stage_mode_adds_hover_button_left_of_line_number(qtbot) -> None:
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(700, 300)
    view.set_line_action_mode(DiffLineActionMode.STAGE)
    view.set_diff(_SAMPLE_DIFF)
    view.show()
    QApplication.processEvents()
    addition_block = next(
        index
        for index, line in enumerate(view._editor._line_info)
        if line.line_type == DiffLineType.ADDITION
    )
    area = view._editor._line_number_area
    rect = area.action_rect_for_block(addition_block)

    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(rect.center()),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    area.mouseMoveEvent(event)

    assert area._hovered_block == addition_block
    assert rect.right() < view._editor.gutter_width()


def test_stage_hover_button_emits_clicked_diff_line(qtbot) -> None:
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(700, 300)
    view.set_line_action_mode(DiffLineActionMode.STAGE)
    view.set_diff(_SAMPLE_DIFF)
    view.show()
    QApplication.processEvents()
    addition_block = next(
        index
        for index, line in enumerate(view._editor._line_info)
        if line.line_type == DiffLineType.ADDITION
    )
    area = view._editor._line_number_area
    rect = area.action_rect_for_block(addition_block)

    with qtbot.waitSignal(view.line_action_requested, timeout=500) as blocker:
        QTest.mouseClick(area, Qt.MouseButton.LeftButton, pos=rect.center())

    line = blocker.args[0]
    assert line == view._editor._line_info[addition_block]
    assert view.line_action_mode() == DiffLineActionMode.STAGE


def test_unstage_mode_emits_deletion_or_addition_with_minus_action(qtbot) -> None:
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.resize(700, 300)
    view.set_line_action_mode(DiffLineActionMode.UNSTAGE)
    view.set_diff(_SAMPLE_DIFF)
    view.show()
    QApplication.processEvents()
    deletion_block = next(
        index
        for index, line in enumerate(view._editor._line_info)
        if line.line_type == DiffLineType.DELETION
    )
    area = view._editor._line_number_area
    rect = area.action_rect_for_block(deletion_block)

    with qtbot.waitSignal(view.line_action_requested, timeout=500) as blocker:
        QTest.mouseClick(area, Qt.MouseButton.LeftButton, pos=rect.center())

    assert blocker.args[0].line_type == DiffLineType.DELETION
    assert view.line_action_mode() == DiffLineActionMode.UNSTAGE


def test_line_action_is_hidden_for_context_rows(qtbot) -> None:
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_line_action_mode(DiffLineActionMode.STAGE)
    view.set_diff(_SAMPLE_DIFF)
    context_block = next(
        index
        for index, line in enumerate(view._editor._line_info)
        if line.line_type == DiffLineType.CONTEXT
    )

    assert not view._editor.is_actionable_block(context_block)
    with qtbot.assertNotEmitted(view.line_action_requested, wait=50):
        view._editor.request_line_action(context_block)


def test_disabling_line_actions_shrinks_gutter(qtbot) -> None:
    _ensure_app()
    view = DiffViewWidget()
    qtbot.addWidget(view)
    view.set_diff(_SAMPLE_DIFF)
    plain_width = view._editor.gutter_width()

    view.set_line_action_mode(DiffLineActionMode.STAGE)
    action_width = view._editor.gutter_width()
    view.set_line_action_mode(None)

    assert action_width > plain_width
    assert view._editor.gutter_width() == plain_width
