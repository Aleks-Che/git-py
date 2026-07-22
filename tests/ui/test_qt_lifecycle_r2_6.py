"""Stage R2.6 — Qt lifecycle regression tests (H13/H14/H15/H16/M13/M14/M23)."""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_branch_popup_uses_wa_delete_on_close_true() -> None:
    """BranchStackPopup is configured with ``Qt.WA_DeleteOnClose = True``."""
    src = (_REPO_ROOT / "src" / "ui" / "widgets" / "graph_panel.py").read_text()
    # Match: any setAttribute(...WA_DeleteOnClose, True...) inside
    # BranchStackPopup class body. The flag may be referenced as
    # ``Qt.WA_DeleteOnClose`` or ``Qt.WidgetAttribute.WA_DeleteOnClose``.
    class_match = re.search(r"class\s+BranchStackPopup\b.*?\n(?=\S)", src, re.DOTALL)
    assert class_match, "Could not find class BranchStackPopup"
    body = class_match.group(0)
    pattern = re.compile(
        r"setAttribute\(\s*(?:Qt\.WA_DeleteOnClose|Qt\.WidgetAttribute\.WA_DeleteOnClose)\s*,\s*True\s*\)"
    )
    assert pattern.search(body), (
        f"BranchStackPopup should setAttribute(WA_DeleteOnClose, True) (H13). Body: {body[:400]}"
    )


def test_left_panel_drop_uses_exec_with_viewport() -> None:
    """Drop-menu in left_panel.py uses ``QMenu.exec`` (not popup) and
    routes through ``viewport().mapToGlobal`` (H15)."""
    src = (_REPO_ROOT / "src" / "ui" / "widgets" / "left_panel.py").read_text()
    # Find a QMenu.exec(...) call with viewport().mapToGlobal(...)
    pattern_exec = re.compile(
        r"\.exec\(\s*[^)]*viewport\(\)\.mapToGlobal\(", re.DOTALL,
    )
    pattern_popup = re.compile(r"\.popup\(", re.DOTALL)
    assert pattern_exec.search(src), (
        "left_panel should call menu.exec(viewport().mapToGlobal(...)) (H15)."
    )
    # The original raw `.popup(` should NOT be used in the drop path.
    # (We allow `.popup(` if it's used for `.setPopupMode(...)`; the
    # `popup` call form (no parens to setPopupMode) must be absent.
    matches = pattern_popup.findall(src)
    bad = [m for m in matches if "setPopupMode" not in m]
    assert not bad, f"left_panel still has QMenu.popup() calls: {bad}"


def test_repo_bar_widget_no_saved_tab_index_attribute() -> None:
    """repo_bar_widget.py must not store tab_index in _CloseTabButton
    (H16 — close handler must resolve index via tabAt).
    """
    src = (_REPO_ROOT / "src" / "ui" / "widgets" / "repo_bar_widget.py").read_text()
    pattern = re.compile(r"_tab_index", re.DOTALL)
    matches = pattern.findall(src)
    assert not matches, (
        f"repo_bar_widget should not reference _tab_index anywhere "
        f"(must use tabAt on click): {matches}"
    )


def test_diff_view_widget_do_scroll_rederives_cursor_per_tick() -> None:
    """diff_view_widget._do_scroll should re-derive the cursor inside the
    singleShot callback, NOT capture it in a closure (M23 — stale-cursor
    fix).
    """
    src = (_REPO_ROOT / "src" / "ui" / "widgets" / "diff_view_widget.py").read_text()
    # Look for the _do_scroll definition. Must NOT have a captured cursor.
    pattern_fn = re.compile(r"def _do_scroll\(\)\s*->.*?\n(?:[ \t].*?\n)+", re.DOTALL)
    fn_match = pattern_fn.search(src)
    assert fn_match, "Could not locate _do_scroll in diff_view_widget.py."
    fn_body = fn_match.group(0)
    # Must contain doc = self._editor.document() — the re-derivation mark.
    assert "doc.findBlockByLineNumber" in fn_body or "findBlock" in fn_body, (
        f"_do_scroll must re-derive the block on tick. Got: {fn_body}"
    )
