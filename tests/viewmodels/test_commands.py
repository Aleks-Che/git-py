"""Stage 0: Command pattern scaffolding (the most important Stage 0 contract).

``docs/DEVELOPMENT_RULES.md`` requires every mutating Git operation to
flow through ``GitCommand`` + ``CommandProcessor``. The processor owns
the undo/redo stacks and emits ``stack_changed``; toolbar Undo/Redo
bind to it. This test pins the contract down before the real commands
arrive in Stages 3+.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from src.viewmodels.commands import CommandProcessor, GitCommand


class _IncrementCommand(GitCommand):
    """Toy command used to observe the processor's stack behaviour."""

    def __init__(self, counter: list[int]) -> None:
        self._counter = counter

    @property
    def name(self) -> str:
        return "increment"

    def execute(self) -> None:
        self._counter[0] += 1

    def undo(self) -> None:
        self._counter[0] -= 1


def _ensure_qapp() -> None:
    """``CommandProcessor`` is a ``QObject``; its signals need an app."""
    QCoreApplication.instance() or QCoreApplication([])


def test_command_processor_starts_empty() -> None:
    _ensure_qapp()
    processor = CommandProcessor()
    assert not processor.can_undo
    assert not processor.can_redo


def test_execute_pushes_to_undo_stack() -> None:
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor()
    processor.execute(_IncrementCommand(counter))

    assert counter[0] == 1
    assert processor.can_undo
    assert not processor.can_redo


def test_undo_and_redo_move_between_stacks() -> None:
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor()
    processor.execute(_IncrementCommand(counter))

    processor.undo()
    assert counter[0] == 0
    assert not processor.can_undo
    assert processor.can_redo

    processor.redo()
    assert counter[0] == 1
    assert processor.can_undo
    assert not processor.can_redo


def test_new_execute_clears_redo_stack() -> None:
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor()
    processor.execute(_IncrementCommand(counter))
    processor.undo()
    assert processor.can_redo

    processor.execute(_IncrementCommand(counter))
    assert counter[0] == 1
    assert not processor.can_redo


def test_undo_and_redo_are_noops_when_empty() -> None:
    _ensure_qapp()
    processor = CommandProcessor()
    processor.undo()  # must not raise
    processor.redo()  # must not raise
    assert not processor.can_undo
    assert not processor.can_redo


def test_stack_changed_signal_fires() -> None:
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor()
    events: list[int] = []
    processor.stack_changed.connect(lambda: events.append(1))

    processor.execute(_IncrementCommand(counter))
    processor.undo()
    processor.redo()

    assert len(events) == 3


def test_clear_drops_both_stacks() -> None:
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor()
    processor.execute(_IncrementCommand(counter))
    processor.undo()
    assert processor.can_redo

    processor.clear()
    assert not processor.can_undo
    assert not processor.can_redo
