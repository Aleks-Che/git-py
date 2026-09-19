"""User ordering, row identities and async AI cancellation."""
import threading

import pytest
from src.core.conflict_resolution import ConflictSnapshot
from src.utils.ai_config import AISettings
from src.utils.config import load_config, save_config
from src.viewmodels.conflict_editor_viewmodel import ConflictEditorViewModel


@pytest.fixture
def vm(qapp, tmp_path):
    vm = ConflictEditorViewModel(config_path=tmp_path / "config.json")
    vm.set_snapshot(ConflictSnapshot("f", b"head\nold\ntail\n",
                                     b"head\nL1\nL2\ntail\n", b"head\nR1\nR2\ntail\n"))
    return vm


def test_order_remove_readd_without_duplicate_context(vm):
    left = [row.token for row in vm.source_rows("ours") if row.token]
    right = [row.token for row in vm.source_rows("theirs") if row.token]
    assert vm.result_text() == "head\ntail\n"
    assert not vm.can_resolve
    vm.add_line(right[1])
    vm.add_line(left[0])
    vm.add_line(right[0])
    vm.add_line(left[0])  # duplicate click is a no-op
    assert vm.result_text() == "head\nR2\nL1\nR1\ntail\n"
    vm.remove_line(left[0])
    assert not next(row for row in vm.source_rows("ours") if row.token == left[0]).selected
    vm.add_line(left[0])
    assert vm.result_text() == "head\nR2\nR1\nL1\ntail\n"
    assert vm.can_resolve


@pytest.mark.parametrize("first,second,payload", [
    ("ours", "theirs", "L1\nL2\nR1\nR2\n"),
    ("theirs", "ours", "R1\nR2\nL1\nL2\n"),
])
def test_side_checkboxes_accumulate(vm, first, second, payload):
    vm.select_side(first, True)
    vm.select_side(second, True)
    vm.select_side(first, True)
    assert vm.result_text() == "head\n" + payload + "tail\n"
    assert vm.side_state(first) == vm.side_state(second) == 2
    vm.select_side(second, False)
    assert vm.side_state(second) == 0
    assert vm.side_state(first) == 2


def test_checkbox_after_partial_selection(vm):
    token = [r.token for r in vm.source_rows("ours") if r.token][1]
    vm.add_line(token)
    assert vm.side_state("ours") == 1
    vm.select_side("ours", True)
    assert vm.result_text() == "head\nL2\nL1\ntail\n"


def test_manual_edit_preserves_unmodified_row_removal(vm):
    vm.select_side("ours", True)
    vm.edit_result("edited header\nL1\nL2\ntail\n")
    token = next(row.token for row in vm.result_rows() if row.text == "L1")
    vm.remove_line(token)
    assert vm.result_text() == "edited header\nL2\ntail\n"


def test_editing_context_does_not_silently_resolve_untouched_conflicts(vm):
    vm.edit_result("edited header\ntail\n")
    assert not vm.can_resolve
    assert vm.remaining == 1
    vm.edit_result("edited header\nmanual resolution\ntail\n")
    assert vm.can_resolve
    assert vm.remaining == 0


def test_separate_conflicts_keep_document_position(vm):
    vm.set_snapshot(ConflictSnapshot("f", b"a\ngap\nb", b"L\ngap\nM", b"R\ngap\nN"))
    tokens = [r.token for r in vm.source_rows("ours") if r.token]
    vm.add_line(tokens[1])
    vm.add_line(tokens[0])
    assert vm.result_text() == "L\ngap\nM"


def test_empty_side_is_explicit_deletion(vm):
    vm.set_snapshot(ConflictSnapshot("f", b"old\n", b"", b"modified\n"))
    assert not vm.can_resolve
    vm.select_side("ours", True)
    assert vm.can_resolve and vm.result_bytes() == b""
    assert vm.side_state("ours") == 2 and vm.side_state("theirs") == 0
    vm.select_side("ours", False)
    vm.select_side("theirs", True)
    assert vm.side_state("ours") == 0 and vm.side_state("theirs") == 2


def test_no_eof_newline_does_not_glue_selected_lines(vm):
    vm.set_snapshot(ConflictSnapshot("f", b"old", b"left", b"right"))
    vm.select_side("ours", True)
    assert vm.result_bytes() == b"left"
    vm.select_side("theirs", True)
    assert vm.result_bytes() == b"left\nright"


def _configure(vm):
    save_config(vm.config_path, {"ai": AISettings(base_url="http://localhost:1234/v1",
                                                model="test-model").to_dict()})


def test_ai_draft_uses_saved_settings(qtbot, vm, monkeypatch):
    _configure(vm)
    calls = []

    def resolve(snapshot, settings):
        calls.append((snapshot, settings, threading.get_ident()))
        return "head\nAI result\ntail\n"

    monkeypatch.setattr("src.viewmodels.conflict_editor_viewmodel.resolve_with_ai", resolve)
    with qtbot.waitSignal(vm.ai_finished):
        vm.resolve_using_ai("shared prompt", "keep both branches")
        vm.resolve_using_ai("duplicate", "")
    assert len(calls) == 1
    assert calls[0][1].model == "test-model"
    assert calls[0][1].conflict_prompt == "shared prompt"
    assert calls[0][1].conflict_context == "keep both branches"
    assert calls[0][2] != threading.get_ident()
    assert vm.result_text() == "head\nAI result\ntail\n" and vm.can_resolve
    assert load_config(vm.config_path)["ai"]["conflict_prompt"] == "shared prompt"
    qtbot.waitUntil(lambda: not vm._ai._workers)


@pytest.mark.parametrize("action", ["cancel", "reload"])
def test_late_ai_is_ignored(qtbot, vm, monkeypatch, action):
    _configure(vm)
    ready, release = threading.Event(), threading.Event()

    def resolve(*args):
        ready.set()
        release.wait(5)
        return "late AI answer"

    monkeypatch.setattr("src.viewmodels.conflict_editor_viewmodel.resolve_with_ai", resolve)
    vm.resolve_using_ai("prompt", "context")
    qtbot.waitUntil(ready.is_set)
    try:
        assert vm.is_busy
        if action == "cancel":
            vm.cancel_ai()
            vm.edit_result("manual after cancellation")
        else:
            vm.set_snapshot(ConflictSnapshot("new", b"base", b"new ours", b"new theirs"))
        expected = vm.result_text()
    finally:
        release.set()
    qtbot.waitUntil(lambda: not vm._ai._workers)
    assert vm.result_text() == expected
    assert not vm.is_busy


def test_ai_error_keeps_draft_and_allows_retry(qtbot, vm, monkeypatch):
    from src.utils.ai_client import AIError

    _configure(vm)
    vm.select_side("ours", True)
    expected = vm.result_text()

    def fail(*args):
        raise AIError("timeout")

    monkeypatch.setattr("src.viewmodels.conflict_editor_viewmodel.resolve_with_ai", fail)
    with qtbot.waitSignal(vm.error_occurred) as error:
        vm.resolve_using_ai("prompt", "context")
    assert error.args == ["timeout"]
    assert vm.result_text() == expected
    qtbot.waitUntil(lambda: not vm._ai._workers)
    assert not vm.is_busy
