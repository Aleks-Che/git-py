"""Stage 8: tests for the action-history panel widget.

Ensures the history tree correctly displays undo/redo stack contents
and stays in sync with :class:`CommandProcessor` state changes.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from src.ui.widgets.action_history_widget import ActionHistoryWidget
from src.viewmodels.commands import CommandProcessor, GitCommand


class _LabelCommand(GitCommand):
    """Minimal command that stores a label as its name."""

    def __init__(self, label: str) -> None:
        self._label = label

    @property
    def name(self) -> str:
        return self._label

    def execute(self) -> None:
        pass

    def undo(self) -> None:
        pass


def _ensure_qapp() -> None:
    QApplication.instance() or QApplication([])


def _collect_leaves(root: QTreeWidgetItem) -> list[str]:
    """Return the text of every leaf item under *root*."""
    return [root.child(i).text(0) for i in range(root.childCount())]


def test_history_initial_state(qtbot) -> None:
    """Widget shows empty Applied / Undone sections when not bound."""
    _ensure_qapp()
    widget = ActionHistoryWidget()
    qtbot.addWidget(widget)
    assert _collect_leaves(widget._applied_root) == []
    assert _collect_leaves(widget._undone_root) == []


def test_history_shows_executed_commands(qtbot) -> None:
    """Executed commands appear under the Applied section."""
    _ensure_qapp()
    processor = CommandProcessor()
    processor.execute(_LabelCommand("a"))
    processor.execute(_LabelCommand("b"))

    widget = ActionHistoryWidget()
    qtbot.addWidget(widget)
    widget.set_processor(processor)

    assert _collect_leaves(widget._applied_root) == ["a", "b"]


def test_history_moves_to_undone_on_undo(qtbot) -> None:
    """Undone commands move from Applied to Undone section."""
    _ensure_qapp()
    processor = CommandProcessor()
    processor.execute(_LabelCommand("x"))
    processor.execute(_LabelCommand("y"))

    widget = ActionHistoryWidget()
    qtbot.addWidget(widget)
    widget.set_processor(processor)

    processor.undo()
    assert _collect_leaves(widget._applied_root) == ["x"]
    assert _collect_leaves(widget._undone_root) == ["y"]


def test_history_moves_back_on_redo(qtbot) -> None:
    """Redone commands return from Undone to Applied."""
    _ensure_qapp()
    processor = CommandProcessor()
    processor.execute(_LabelCommand("x"))
    processor.execute(_LabelCommand("y"))
    processor.undo()

    widget = ActionHistoryWidget()
    qtbot.addWidget(widget)
    widget.set_processor(processor)
    assert _collect_leaves(widget._undone_root) == ["y"]

    processor.redo()
    assert _collect_leaves(widget._applied_root) == ["x", "y"]
    assert _collect_leaves(widget._undone_root) == []


def test_history_clears_on_clear(qtbot) -> None:
    """Processor.clear() empties both sections."""
    _ensure_qapp()
    processor = CommandProcessor()
    processor.execute(_LabelCommand("a"))
    processor.execute(_LabelCommand("b"))
    processor.undo()  # "b" moves to redo

    widget = ActionHistoryWidget()
    qtbot.addWidget(widget)
    widget.set_processor(processor)
    assert _collect_leaves(widget._applied_root) == ["a"]
    assert _collect_leaves(widget._undone_root) == ["b"]

    processor.clear()
    assert _collect_leaves(widget._applied_root) == []
    assert _collect_leaves(widget._undone_root) == []


def test_history_empty_when_no_processor(qtbot) -> None:
    """Sections are empty after set_processor(None)."""
    _ensure_qapp()
    widget = ActionHistoryWidget()
    qtbot.addWidget(widget)
    widget.set_processor(None)
    assert _collect_leaves(widget._applied_root) == []
    assert _collect_leaves(widget._undone_root) == []
