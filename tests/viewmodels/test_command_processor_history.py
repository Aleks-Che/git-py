"""Stage 8: CommandProcessor metadata (timestamp) and stack snapshots.

Verifies that :class:`CommandProcessor` sets timestamps on executed
commands and exposes ``undo_stack_snapshot`` / ``redo_stack_snapshot``
for the action-history panel.
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from src.viewmodels.commands import CommandProcessor, GitCommand


class _IncrementCommand(GitCommand):
    """Minimal command used to probe snapshot behaviour."""

    def __init__(self, label: str = "increment") -> None:
        self._counter = 0
        self._label = label

    @property
    def name(self) -> str:
        return self._label

    def execute(self) -> None:
        self._counter += 1

    def undo(self) -> None:
        self._counter -= 1


def _ensure_qapp() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def test_timestamp_null_before_execute() -> None:
    """A freshly constructed command has no timestamp."""
    cmd = _IncrementCommand()
    assert cmd.timestamp is None


def test_timestamp_set_on_execute() -> None:
    """After ``processor.execute()``, the command has a non-None timestamp."""
    _ensure_qapp()
    cmd = _IncrementCommand()
    processor = CommandProcessor()
    processor.execute(cmd)
    assert cmd.timestamp is not None
    assert isinstance(cmd.timestamp, float)


def test_timestamp_updated_on_redo() -> None:
    """A re-executed command gets a fresh timestamp."""
    _ensure_qapp()
    cmd = _IncrementCommand()
    processor = CommandProcessor()
    processor.execute(cmd)
    first_ts = cmd.timestamp

    processor.undo()
    processor.redo()
    assert cmd.timestamp is not None
    assert cmd.timestamp >= first_ts


def test_undo_stack_snapshot_after_execute() -> None:
    """Snapshot contains one entry with the correct name and timestamp."""
    _ensure_qapp()
    cmd = _IncrementCommand("test-cmd")
    processor = CommandProcessor()
    processor.execute(cmd)

    snapshot = processor.undo_stack_snapshot()
    assert len(snapshot) == 1
    assert snapshot[0]["name"] == "test-cmd"
    assert snapshot[0]["timestamp"] == cmd.timestamp


def test_undo_stack_snapshot_order() -> None:
    """Snapshot is ordered oldest-first."""
    _ensure_qapp()
    processor = CommandProcessor()
    processor.execute(_IncrementCommand("alpha"))
    processor.execute(_IncrementCommand("beta"))
    processor.execute(_IncrementCommand("gamma"))

    snapshot = processor.undo_stack_snapshot()
    assert [e["name"] for e in snapshot] == ["alpha", "beta", "gamma"]


def test_redo_stack_snapshot_after_undo() -> None:
    """After undo, the command moves from undo-snapshot to redo-snapshot."""
    _ensure_qapp()
    cmd = _IncrementCommand("moved")
    processor = CommandProcessor()
    processor.execute(cmd)
    processor.undo()

    assert len(processor.undo_stack_snapshot()) == 0
    redo = processor.redo_stack_snapshot()
    assert len(redo) == 1
    assert redo[0]["name"] == "moved"
    assert redo[0]["timestamp"] == cmd.timestamp


def test_snapshot_after_clear() -> None:
    """Clearing the processor empties both snapshots."""
    _ensure_qapp()
    processor = CommandProcessor()
    processor.execute(_IncrementCommand("a"))
    processor.execute(_IncrementCommand("b"))
    processor.undo()
    processor.clear()

    assert len(processor.undo_stack_snapshot()) == 0
    assert len(processor.redo_stack_snapshot()) == 0


def test_snapshot_same_timestamp_object() -> None:
    """The timestamp in the snapshot is the same object as cmd.timestamp."""
    _ensure_qapp()
    cmd = _IncrementCommand()
    processor = CommandProcessor()
    processor.execute(cmd)

    snapshot = processor.undo_stack_snapshot()
    assert snapshot[0]["timestamp"] is cmd.timestamp
