"""Real hover-gutter actions, checkboxes, alignment and AI prompt UI."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QDialog
from src.core.conflict_resolution import ConflictSnapshot
from src.ui.dialogs.ai_conflict_dialog import AIConflictDialog
from src.ui.dialogs.conflict_resolution_dialog import ConflictResolutionDialog
from src.utils.ai_config import AISettings


def _dialog(qtbot, tmp_path):
    dialog = ConflictResolutionDialog(config_path=tmp_path / "config.json")
    qtbot.addWidget(dialog)
    dialog.viewmodel.set_snapshot(ConflictSnapshot(
        "example.py", b"header\nold\nfooter\n",
        b"header\nleft one\nleft two\nfooter\n", b"header\nright one\nfooter\n",
        "main · abc12345 · target", "feature · def67890 · incoming",
    ))
    dialog.show()
    return dialog


def _click_gutter(qtbot, editor, block):
    editor.setTextCursor(QTextCursor(editor.document().findBlockByNumber(block)))
    editor.ensureCursorVisible()
    cursor_rect = editor.cursorRect()
    qtbot.mouseMove(editor.viewport(), QPoint(cursor_rect.x() + 50, cursor_rect.center().y()))
    assert editor._line_number_area._hovered_block == block
    rect = editor._line_number_area.action_rect_for_block(block)
    qtbot.mouseClick(editor._line_number_area, Qt.MouseButton.LeftButton, pos=rect.center())


def test_hover_plus_minus_and_readd(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path)
    assert dialog._splitter.orientation() == Qt.Orientation.Vertical
    assert dialog._sources.count() == 2
    assert not dialog._button_box.button(dialog._button_box.StandardButton.Ok).isEnabled()
    _click_gutter(qtbot, dialog.theirs_view, 1)
    _click_gutter(qtbot, dialog.ours_view, 2)
    assert dialog.result_text() == "header\nright one\nleft two\nfooter\n"
    assert not dialog.theirs_view.is_actionable_block(1)
    _click_gutter(qtbot, dialog._result_view, 1)
    assert dialog.result_text() == "header\nleft two\nfooter\n"
    assert dialog.theirs_view.is_actionable_block(1)
    _click_gutter(qtbot, dialog.theirs_view, 1)
    assert dialog.result_text() == "header\nleft two\nright one\nfooter\n"


def test_alignment_emphasis_padding_and_checkboxes(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path)
    left = dialog.viewmodel.source_rows("ours")
    right = dialog.viewmodel.source_rows("theirs")
    assert len(left) == len(right)
    assert left[-1].text == right[-1].text == "footer"
    assert right[2].number is None and not dialog.theirs_view.is_actionable_block(2)
    assert left[1].emphasis and right[1].emphasis
    assert dialog.ours_view.extraSelections()
    dialog.theirs_check.click()
    dialog.ours_check.click()
    assert dialog.result_text() == "header\nright one\nleft one\nleft two\nfooter\n"
    dialog.theirs_check.click()
    assert dialog.result_text() == "header\nleft one\nleft two\nfooter\n"


def test_result_edit_and_failed_save_retains_dialog(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path)
    dialog.ours_check.click()
    dialog._result_view.moveCursor(QTextCursor.MoveOperation.End)
    qtbot.keyClicks(dialog._result_view, "manual")
    assert dialog.result_text().endswith("manual")
    dialog.save_result = lambda data: False
    dialog._on_mark_resolved()
    assert dialog.isVisible()
    assert dialog.result_text().endswith("manual")
    assert not dialog._message.isHidden()


def test_prompt_editor_passes_user_changes(qtbot, tmp_path, monkeypatch):
    dialog = _dialog(qtbot, tmp_path)
    calls = []

    def edit(prompt_dialog):
        assert prompt_dialog.prompt_edit.toPlainText() == AISettings().conflict_prompt
        prompt_dialog.prompt_edit.setPlainText("custom shared instruction")
        prompt_dialog.context_edit.setPlainText("keep backend and frontend records")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(AIConflictDialog, "exec", edit)
    monkeypatch.setattr(dialog.viewmodel, "resolve_using_ai",
                        lambda *args: calls.append(args))
    dialog._ai_btn.click()
    assert calls == [("custom shared instruction", "keep backend and frontend records")]


def test_scrollbars_remain_aligned(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path)
    base = "".join(f"line {i}\n" for i in range(150))
    dialog.viewmodel.set_snapshot(ConflictSnapshot(
        "long.txt", base.encode(), (base + "left\n").encode(), (base + "right\n").encode(),
    ))
    dialog.ours_view.verticalScrollBar().setValue(90)
    assert dialog.theirs_view.verticalScrollBar().value() == 90
    dialog.theirs_view.verticalScrollBar().setValue(15)
    assert dialog.ours_view.verticalScrollBar().value() == 15


def test_layout_persists_without_overwriting_ai_settings(qtbot, tmp_path):
    from src.utils.config import load_config, save_config

    path = tmp_path / "config.json"
    save_config(path, {"ai": {"model": "my-model", "conflict_prompt": "custom prompt"}})
    dialog = _dialog(qtbot, tmp_path)
    dialog.resize(1300, 900)
    dialog._sources.setSizes([500, 750])
    dialog.reject()
    saved = load_config(path)
    assert saved["ai"]["model"] == "my-model"
    assert saved["ai"]["conflict_prompt"] == "custom prompt"
    assert saved["conflict_editor_layout"]["window_size"] == [1300, 900]
    restored = ConflictResolutionDialog(config_path=path)
    qtbot.addWidget(restored)
    assert restored.width() == 1300 and restored.height() == 900
