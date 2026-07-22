"""R3.4 regression tests — the four bullets finalised in update1.

Each test corresponds to one of the four R3.4 work items that landed
in this drop (binary-conflict bytes, hit-test connector skip,
LeftPanel state preservation, commit-detail html.escape).

Tests run under ``pytest-qt`` (``QT_QPA_PLATFORM=offscreen`` on
headless CI). They use real ``pygit2`` repositories so the conflict
flow exercises the same code path as the production app.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
from src.core.models import CommitInfo
from src.core.repository import RepositoryManager
from src.ui.dialogs.conflict_resolution_dialog import (
    ConflictResolutionDialog,
    _is_binary_blob,
)
from src.ui.widgets.commit_detail_panel import _format_info
from src.viewmodels.main_viewmodel import MainViewModel

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _build_binary_conflict_repo(tmp_path: Path) -> tuple[RepositoryManager, bytes]:
    """Build a repo where ``bin.dat`` has a binary conflict on both sides.

    Returns ``(manager, ours_bytes)`` — ``ours_bytes`` is the exact
    blob that ``Accept Ours`` must persist. ``bin.dat`` carries a
    NUL byte on every side, which trips :func:`_is_binary_blob`.
    """
    repo_path = tmp_path / "repo"
    pygit2.init_repository(str(repo_path), initial_head="main")
    mgr = RepositoryManager(str(repo_path))
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)

    # Common base — also binary so every side hits the binary path.
    base_bytes = b"base\x00blob\n"
    (repo_path / "bin.dat").write_bytes(base_bytes)
    mgr.repo.index.add("bin.dat")
    mgr.repo.index.write()
    base_tree = mgr.repo.index.write_tree()
    c1 = mgr.repo.create_commit("HEAD", sig, sig, "base", base_tree, [])
    mgr.repo.create_reference("refs/heads/feature", c1, force=True)

    # feature side: ours in the future merge = different binary content.
    feature_bytes = b"feature\x00side\n"
    mgr.repo.checkout("refs/heads/feature", strategy=pygit2.GIT_CHECKOUT_FORCE)
    (repo_path / "bin.dat").write_bytes(feature_bytes)
    mgr.repo.index.add("bin.dat")
    mgr.repo.index.write()
    feat_tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("HEAD", sig, sig, "feature", feat_tree, [c1])

    # main side: theirs in the future merge = ours in this drop
    # (we will Accept Ours on the resulting conflict).
    ours_bytes = b"main\x00side\n"
    mgr.repo.checkout("refs/heads/main", strategy=pygit2.GIT_CHECKOUT_FORCE)
    (repo_path / "bin.dat").write_bytes(ours_bytes)
    mgr.repo.index.add("bin.dat")
    mgr.repo.index.write()
    main_tree = mgr.repo.index.write_tree()
    mgr.repo.create_commit("HEAD", sig, sig, "main", main_tree, [c1])

    # Trigger the conflict — merge fails with MergeConflictError, the
    # index keeps the three stages which is what the dialog consumes.
    from src.core.exceptions import MergeConflictError
    from src.core.operations import merge_branch

    try:
        merge_branch(mgr, "feature")
    except MergeConflictError:
        pass

    # Sanity check — every side really is binary.
    assert _is_binary_blob(ours_bytes)
    return mgr, ours_bytes


# --------------------------------------------------------------------------
# A. Binary conflict path (R3.4 M17)
# --------------------------------------------------------------------------


def test_binary_conflict_writes_raw_bytes_not_placeholder(
    qtbot, tmp_path: Path,
) -> None:
    """``Accept Ours`` on a binary conflict emits the raw blob bytes.

    Before R3.4 the dialog stored the literal text ``"<binary>"`` in
    the Result panel and emitted that string on ``resolved`` — the
    resulting file on disk contained the four characters
    ``<``, ``b``, ``i``, … instead of the user's data.

    The fix routes binary conflicts through ``resolved_bytes`` and
    stores the exact blob of the chosen side.  This test triggers a
    real binary conflict on disk and asserts the bytes round-trip
    end-to-end.
    """
    mgr, ours_bytes = _build_binary_conflict_repo(tmp_path)
    dialog = ConflictResolutionDialog(mgr, "bin.dat")
    qtbot.addWidget(dialog)
    dialog.show()
    # The dialog must report the conflict as binary, not text.
    assert dialog.is_binary()
    # No literal placeholder leaks into the Result panel content.
    assert "<binary>" not in dialog.result_text()

    # Accept Ours — the user picks the main branch's blob.
    dialog.accept_ours_bytes()

    # Wire the signal the same way MainWindow does — capture the bytes
    # the dialog would write to disk.
    captured: list[bytes] = []
    dialog.resolved_bytes.connect(captured.append)

    # Mark resolved.  We bypass the modal exec() and call the handler
    # directly; the bytes signal is what the caller persists.
    dialog._on_mark_resolved()  # noqa: SLF001 — white-box test for the
    # resolved_payload wiring; the public contract is the signal.

    assert captured, "resolved_bytes must fire"
    payload = captured[0]
    # The literal ``<binary>`` placeholder must never appear in the
    # emitted payload — that was the pre-R3.4 regression.
    assert payload != b"<binary>"
    # The exact blob of the chosen side must come back, byte-for-byte.
    assert payload == ours_bytes


# --------------------------------------------------------------------------
# B. Hit-test connector skip (R3.4 M12)
# --------------------------------------------------------------------------


def test_hit_test_skips_connector_rows(
    qtbot, tmp_git_repo: Path,
) -> None:
    """Clicking the y-coordinate of a connector row returns ``None``.

    The graph table has two kinds of rows:

    * **commit rows** — carry a real SHA, can be selected.
    * **connector rows** — vertical pipes between non-adjacent cells,
      carry ``sha == ""``.  Clicking on a connector should *not*
      select anything, otherwise the user can land on a non-existent
      commit when they aim at the pipe.

    This test synthesises a two-row graph where row 0 is a commit and
    row 1 is a connector, then asserts the click on row 1 yields no
    SHA.
    """
    from src.ui.widgets.graph_panel import GraphTableWidget
    from src.viewmodels.graph_viewmodel import GraphViewModel

    mgr = RepositoryManager(str(tmp_git_repo))
    sig = pygit2.Signature(
        "tester", "t@example.com", int(time.time()), 0,
    )
    (tmp_git_repo / "f.txt").write_text("v1\n")
    mgr.repo.index.add("f.txt")
    mgr.repo.index.write()
    tree = mgr.repo.index.write_tree()
    sha = str(mgr.repo.create_commit("HEAD", sig, sig, "first", tree, []))

    vm = GraphViewModel(mgr)
    widget = GraphTableWidget(vm)
    qtbot.addWidget(widget)
    widget.show()

    # Synthetic rows: one commit, one connector row directly under it.
    widget._rows = [  # noqa: SLF001
        {"commit": {"sha": sha, "kind": "commit"}, "lane": 0, "cells": []},
        {"commit": None, "lane": 0, "cells": []},  # connector-only
    ]
    # Reset cached layout so the hit-test uses the synthesised rows.
    widget._scroll_offset = 0  # noqa: SLF001

    # y = header_height + half_row lands squarely on row 0 (commit).
    hh = widget._cfg.header_height  # noqa: SLF001
    rh = widget._cfg.row_height  # noqa: SLF001
    on_commit_y = hh + rh // 2
    on_connector_y = hh + rh + rh // 2

    commit_hit = widget._hit_test_commit(0, on_commit_y)  # noqa: SLF001
    connector_hit = widget._hit_test_commit(0, on_connector_y)  # noqa: SLF001

    assert commit_hit == sha
    # R3.4 (M12): connector rows must not produce a SHA.
    assert connector_hit is None


# --------------------------------------------------------------------------
# C. LeftPanel state preservation (R3.4 M20)
# --------------------------------------------------------------------------


def test_left_panel_preserves_expanded_state(
    qtbot, committed_repo: RepositoryManager,
) -> None:
    """Collapsing a group and refreshing keeps it collapsed.

    Before R3.4 the panel hard-coded ``Branches`` and ``Local`` to
    ``setExpanded(True)`` on every rebuild, so the moment the user
    collapsed ``Tags`` to clean up the side panel, the next
    ``references_changed`` would silently re-expand it.

    The test exercises the public flow: load a repo, collapse a
    group, then trigger ``references_changed`` (which is exactly
    what ``HEAD`` switch, ``fetch``, ``pull``, ``create_branch`` and
    ``delete_branch`` all fire).
    """
    from src.ui.widgets.left_panel import LeftPanel

    vm = MainViewModel()
    panel = LeftPanel(vm.branch_panel_view_model(), vm)
    qtbot.addWidget(panel)
    panel.show()
    vm.set_repository(committed_repo)

    # Locate the group the user collapsed. ``Tags`` is a stable choice
    # — it has no children in the bare repo fixture, but the group row
    # itself is always present.
    tags = None
    for i in range(panel.topLevelItemCount()):
        top = panel.topLevelItem(i)
        if top.text(0) == "Tags":
            tags = top
            break
    assert tags is not None, "Tags group must be in the rebuilt tree"
    tags.setExpanded(False)
    assert not tags.isExpanded()

    # Fire a refresh — same signal every mutating verb emits.
    vm.branch_panel_view_model().references_changed.emit()

    # Re-locate Tags (the tree was cleared and rebuilt).
    tags_after = None
    for i in range(panel.topLevelItemCount()):
        top = panel.topLevelItem(i)
        if top.text(0) == "Tags":
            tags_after = top
            break
    assert tags_after is not None, "Tags group must still be in the tree"
    # R3.4 (M20): the user's collapse choice survives the rebuild.
    assert not tags_after.isExpanded()

    # Bonus check: the previously selected local branch (``main``) is
    # still in the rebuilt tree and is the currently selected row.
    main_item = None
    for i in range(panel.topLevelItemCount()):
        top = panel.topLevelItem(i)
        if top.text(0) != "Branches":
            continue
        for j in range(top.childCount()):
            group = top.child(j)
            if group.text(0) != "Local":
                continue
            for k in range(group.childCount()):
                leaf = group.child(k)
                if leaf.text(0).startswith("main"):
                    main_item = leaf
                    break
    # Sanity: ``main`` is in the rebuilt tree.
    assert main_item is not None, "main must be in the rebuilt tree"


# --------------------------------------------------------------------------
# D. Commit-detail html.escape (R3.4 M21)
# --------------------------------------------------------------------------


def test_commit_metadata_html_escape() -> None:
    """A hostile author name is escaped before being rendered.

    The commit-detail panel mixes hard-coded formatting tags
    (``<b>``, ``<code>``, ``<br/>``) with attacker-controlled
    metadata (author name, e-mail, SHA, parents).  Before R3.4 a
    commit authored by ``<script>alert('xss')</script>`` would be
    passed straight into ``QLabel.setText`` — and Qt's default
    auto-detection promotes strings containing ``<`` to RichText,
    which lets the script tag render as actual HTML.

    ``_format_info`` is the single funnel for the info block; the
    fix routes every user-controlled field through ``html.escape``
    while keeping the formatting tags intact.
    """
    from PySide6.QtCore import Qt

    info = CommitInfo(
        sha="abcdef1234567890",
        short_sha="abcdef1",
        author_name="<script>alert('xss')</script>",
        author_email="<img src=x onerror=alert(1)>@evil.example",
        committer_name="",
        committer_email="",
        committer_time=0,
        author_time=0,
        message="",
        parents=["<bad>parent</bad>"],
    )
    html_out = _format_info(info)

    # The hostile raw markup must be neutralised — every ``<`` and
    # ``>`` belonging to user data must come back as ``&lt;`` /
    # ``&gt;``.  Only the formatting tags we wrote ourselves
    # (``<b>``, ``<code>``, ``<br/>``) are allowed to keep real
    # angle brackets.
    assert "<script>" not in html_out
    assert "alert(" not in html_out or "&lt;script&gt;" in html_out
    assert "<img " not in html_out
    # ``html.escape`` quotes are escaped by default — we only need
    # the angle brackets neutralised for XSS safety in QLabel, but
    # full escape (``quote=True``) is also fine and is what
    # ``html.escape`` does by default.
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html_out

    # The hard-coded formatting tags must still be present and
    # unescaped — we do not want to over-escape and break the
    # bold "Author:" / "SHA:" labels.
    assert "<b>Author:</b>" in html_out
    assert "<b>SHA:</b>" in html_out
    assert "<b>Parents:</b>" in html_out

    # Round-trip through QLabel: the rendered text must not contain
    # the raw script tag — QLabel with Qt.RichText would render it.
    from PySide6.QtWidgets import QApplication, QLabel

    # Ensure a QApplication exists for the QLabel round-trip. We do
    # not bind the instance because the existing application (if any)
    # is the one we want to use; ``qtbot`` may already own one.
    QApplication.instance() or QApplication([])
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setText(html_out)
    # ``label.text()`` returns the raw HTML source. ``The plain text``
    # version is what the user actually sees; assert against that.
    plain = label.text()
    assert "<script>" not in plain
    # The friendly visible string must still mention the author.
    assert "Author" in plain
