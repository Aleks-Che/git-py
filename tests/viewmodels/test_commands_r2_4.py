"""Stage R2.4 — undo-семантика: deque-bounded undo, config defaults."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication
from src.viewmodels.commands import CommandProcessor, GitCommand


class _IncrementCommand(GitCommand):
    def __init__(self, counter: list[int], label: str = "x") -> None:
        self._counter = counter
        self._label = label

    @property
    def name(self) -> str:
        return f"increment-{self._label}"

    def execute(self) -> None:
        self._counter[0] += 1

    def undo(self) -> None:
        self._counter[0] -= 1


def _ensure_qapp() -> None:
    QApplication.instance() or QApplication([])


def test_command_processor_undo_stack_is_bounded_deque() -> None:
    """``CommandProcessor(max_undo=3)`` keeps only the most recent 3 commands
    on the undo stack; older ones are silently dropped when the deque shifts.
    """
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor(max_undo=3)

    fired: list[int] = []
    processor.stack_changed.connect(lambda: fired.append(1))

    # Push 5 — only the last 3 remain
    for _ in range(5):
        processor.execute(_IncrementCommand(counter))
    # Stack should be limited to 3
    assert len(processor._undo_stack) == 3  # noqa: SLF001
    # All 5 commands ran
    assert counter[0] == 5
    # Undo only goes back 3
    processor.undo()
    processor.undo()
    processor.undo()
    assert counter[0] == 2
    # No more to undo
    assert not processor.can_undo


def test_command_processor_default_max_undo_is_100() -> None:
    """Default ``CommandProcessor()`` uses max_undo=100 from config."""
    _ensure_qapp()
    processor = CommandProcessor()
    assert processor.max_undo == 100  # noqa: SLF001


def test_command_processor_redo_stack_also_bounded() -> None:
    """Undo then redo — redo stack should be bounded too (same maxlen)."""
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor(max_undo=2)
    processor.execute(_IncrementCommand(counter))
    processor.execute(_IncrementCommand(counter))
    processor.execute(_IncrementCommand(counter))
    # undo 3 — but only 2 exist in undo stack; the third undo is a no-op
    processor.undo()
    processor.undo()
    processor.undo()  # extra undo doesn't raise
    assert counter[0] == 1
    # redo both
    processor.redo()
    processor.redo()
    assert counter[0] == 3


def test_set_max_undo_rebuilds_stacks() -> None:
    """``set_max_undo(n)`` rebuilds both deques with the new maxlen."""
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor(max_undo=10)
    for _ in range(8):
        processor.execute(_IncrementCommand(counter))
    processor.set_max_undo(3)
    assert processor.max_undo == 3  # noqa: SLF001
    # Now the oldest 5 should be dropped
    assert len(processor._undo_stack) == 3  # noqa: SLF001


def test_config_has_command_processor_history_size_key() -> None:
    """src/utils/config.py declares command_processor_history_size = 100."""
    from src.utils import config
    default = config._DEFAULT_CONFIG  # noqa: SLF001
    assert "command_processor_history_size" in default
    assert default["command_processor_history_size"] == 100


def test_config_has_discard_file_max_backup_bytes_key() -> None:
    """src/utils/config.py declares discard_file_max_backup_bytes = 1 MiB."""
    from src.utils import config
    default = config._DEFAULT_CONFIG  # noqa: SLF001
    assert "discard_file_max_backup_bytes" in default
    assert default["discard_file_max_backup_bytes"] == 1024 * 1024


def test_command_processor_peek_undo_returns_next_command() -> None:
    """peek_undo_command / peek_redo_command don't pop the stack."""
    _ensure_qapp()
    counter = [0]
    processor = CommandProcessor()
    cmd = _IncrementCommand(counter)
    processor.execute(cmd)
    # Peek twice — stack should not change
    a = processor.peek_undo_command()
    b = processor.peek_undo_command()
    assert a is cmd and b is cmd
    assert len(processor._undo_stack) == 1  # noqa: SLF001
