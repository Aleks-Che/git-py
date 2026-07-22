"""Stage 0: Command pattern scaffolding (the most important Stage 0 contract).

``docs/DEVELOPMENT_RULES.md`` requires every mutating Git operation to
flow through ``GitCommand`` + ``CommandProcessor``. The processor owns
the undo/redo stacks and emits ``stack_changed``; toolbar Undo/Redo
bind to it. This test pins the contract down before the real commands
arrive in Stages 3+.
"""
from __future__ import annotations

from PySide6.QtWidgets import QApplication
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
    QApplication.instance() or QApplication([])


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


# --- R1.7: non-undoable commands stay out of undo stack ------------------


def test_discard_changes_command_is_excluded_from_undo_stack(tmp_path) -> None:
    """DiscardChangesCommand is destructive and irreversible; its
    undo() is a no-op, so the processor must NOT put it on the undo
    stack — otherwise the toolbar Undo button silently "succeeds" on a
    command that does nothing.
    """
    import time

    import pygit2
    from src.core.repository import RepositoryManager
    from src.viewmodels.commands import DiscardChangesCommand

    _ensure_qapp()
    repo_path = tmp_path / "r"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path), initial_head="main")
    (repo_path / "a.txt").write_text("a\n")
    manager = RepositoryManager(str(repo_path))
    sig = pygit2.Signature("t", "t@x", int(time.time()), 0)
    manager.repo.index.add("a.txt")
    manager.repo.index.write()
    tree = manager.repo.index.write_tree()
    manager.repo.create_commit("refs/heads/main", sig, sig, "init", tree, [])

    processor = CommandProcessor()
    processor.execute(DiscardChangesCommand(manager))

    assert not processor.can_undo
    assert not processor.can_redo


def test_confirm_destructive_returns_false_on_no() -> None:
    """``_confirm_destructive`` returns False when the user picks No
    (or hits Enter — default button is No).
    """
    from PySide6.QtWidgets import QMessageBox
    from src.viewmodels.main_viewmodel import MainViewModel

    _ensure_qapp()
    vm = MainViewModel.__new__(MainViewModel)  # bypass __init__ — we only call the helper

    # Monkeypatch QMessageBox.question -> returns No
    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.StandardButton.No)
    try:
        result = vm._confirm_destructive("t", "m", default_no=True)
        assert result is False
    finally:
        QMessageBox.question = original


def test_confirm_destructive_returns_true_on_yes() -> None:
    """``_confirm_destructive`` returns True when the user explicitly
    clicks Yes.
    """
    from PySide6.QtWidgets import QMessageBox
    from src.viewmodels.main_viewmodel import MainViewModel

    _ensure_qapp()
    vm = MainViewModel.__new__(MainViewModel)

    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **kw: QMessageBox.StandardButton.Yes)
    try:
        result = vm._confirm_destructive("t", "m", default_no=True)
        assert result is True
    finally:
        QMessageBox.question = original


def test_confirm_destructive_default_is_no() -> None:
    """Verify that ``default_no=True`` (the default) makes the No
    button the default — so accidental Enter cannot destroy data.

    We can't observe the default button directly without running a real
    event loop, but we CAN verify the helper passes the right
    ``default`` argument to ``QMessageBox.question``.
    """
    from PySide6.QtWidgets import QMessageBox
    from src.viewmodels.main_viewmodel import MainViewModel

    _ensure_qapp()
    vm = MainViewModel.__new__(MainViewModel)
    captured: dict[str, object] = {}

    def capture(parent, title, message, flags, default):
        captured["default"] = default
        captured["flags"] = flags
        return QMessageBox.StandardButton.No

    original = QMessageBox.question
    QMessageBox.question = staticmethod(capture)
    try:
        vm._confirm_destructive("t", "m")
        assert captured["default"] == QMessageBox.StandardButton.No
        # Both flags set
        assert captured["flags"] & QMessageBox.StandardButton.Yes
        assert captured["flags"] & QMessageBox.StandardButton.No
    finally:
        QMessageBox.question = original
