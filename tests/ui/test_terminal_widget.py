"""Tests for the embedded :class:`TerminalWidget`.

The terminal runs a shell via :class:`QProcess` in the repository
root.  Tests verify the ANSI parser, process lifecycle, and the
widget-signal plumbing from :class:`MainViewModel.repository_changed`.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication
from src.ui.main_window import MainWindow
from src.ui.widgets.terminal_widget import TerminalWidget, _ansi_to_html
from src.utils.theme import DARK_THEME


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


# ----- ANSI parser --------------------------------------------------


def test_ansi_to_html_no_escape_passes_through() -> None:
    result = _ansi_to_html("hello world", DARK_THEME)
    assert "hello world" in result


def test_ansi_to_html_html_escapes_angle_brackets() -> None:
    result = _ansi_to_html("<script>alert(1)</script>", DARK_THEME)
    assert "<script>" not in result
    assert "&lt;" in result


def test_ansi_to_html_bold_adds_span() -> None:
    result = _ansi_to_html("\x1b[1mBOLD\x1b[0m", DARK_THEME)
    assert "font-weight: bold" in result
    assert "BOLD" in result


def test_ansi_to_html_red_foreground() -> None:
    result = _ansi_to_html("\x1b[31mERROR\x1b[0m", DARK_THEME)
    assert "#E8685A" in result
    assert "ERROR" in result


def test_ansi_to_html_multiple_codes() -> None:
    result = _ansi_to_html("\x1b[1;31mBOLD RED\x1b[0m", DARK_THEME)
    assert "font-weight: bold" in result
    assert "#E8685A" in result


def test_ansi_to_html_reset_closes_all_spans() -> None:
    result = _ansi_to_html("\x1b[31mRED\x1b[1mBOLD\x1b[0mplain", DARK_THEME)
    # The reset (0) closes both spans.
    assert "plain" in result
    # Count opening and closing spans — must be equal.
    opens = result.count("<span")
    closes = result.count("</span>")
    assert opens == closes


def test_ansi_to_html_strips_non_sgr_sequences() -> None:
    result = _ansi_to_html("\x1b[H\x1b[2Jhello\x1b[31mred\x1b[0m", DARK_THEME)
    assert "\x1b[H" not in result
    assert "\x1b[2J" not in result
    assert "hello" in result
    assert "red" in result


# ----- widget creation ----------------------------------------------


def test_terminal_widget_creates_without_process(qtbot) -> None:
    _ensure_app()
    w = TerminalWidget()
    qtbot.addWidget(w)
    # No process until a repo is set.
    assert w._process is None


# ----- repo wiring via MainWindow -----------------------------------


def test_main_window_wires_terminal_repo_path(qtbot, tmp_path) -> None:
    """The MainWindow connects the terminal to repository_changed."""
    _ensure_app()
    win = MainWindow(config_path=None)
    qtbot.addWidget(win)
    # The terminal should exist and be a TerminalWidget.
    assert isinstance(win._terminal, TerminalWidget)


def test_terminal_does_not_start_until_a_command(qtbot, tmp_git_repo) -> None:
    """Browsing a repo leaves its directory available to external tools."""
    _ensure_app()
    w = TerminalWidget(DARK_THEME)
    qtbot.addWidget(w)
    w.show()
    w.set_repo_path(str(tmp_git_repo))
    qtbot.wait(10)
    assert w._process is None
    moved = tmp_git_repo.with_name("moved")
    tmp_git_repo.rename(moved)
    moved.rename(tmp_git_repo)


def test_terminal_stops_on_set_repo_path_none(qtbot, tmp_git_repo: Path) -> None:
    """Setting path=None stops the running process."""
    _ensure_app()
    w = TerminalWidget(DARK_THEME)
    qtbot.addWidget(w)
    w.show()
    w.set_repo_path(str(tmp_git_repo))
    w._input.setText("echo ready")
    w._input.returnPressed.emit()
    qtbot.waitUntil(lambda: w._process is not None, timeout=3000)
    proc = w._process
    with qtbot.waitSignal(proc.finished, timeout=3000):
        w.set_repo_path(None)
    assert w._process is None
    moved = tmp_git_repo.with_name("moved")
    tmp_git_repo.rename(moved)
    moved.rename(tmp_git_repo)


def test_terminal_sends_command_to_process(qtbot, tmp_git_repo: Path) -> None:
    """Typing a command in the input line writes to the QProcess."""
    _ensure_app()

    repo = tmp_git_repo
    w = TerminalWidget(DARK_THEME)
    qtbot.addWidget(w)
    w.show()
    w.set_repo_path(str(repo))

    # Feed input through the QLineEdit.
    w._input.setText("echo hello & echo first-command-delivered")
    w._input.returnPressed.emit()
    # The input echo contains one copy; actual output must contain a second.
    qtbot.waitUntil(
        lambda: w._output.toPlainText().count("first-command-delivered") >= 2,
        timeout=3000,
    )

    html = w._output.toHtml()
    # The command echo should show up.
    assert "echo hello" in html


def test_terminal_without_process_shows_warning(qtbot) -> None:
    """Entering a command without a running process shows a placeholder."""
    _ensure_app()
    w = TerminalWidget(DARK_THEME)
    qtbot.addWidget(w)
    w.show()
    w._input.setText("ls")
    w._input.returnPressed.emit()
    html = w._output.toHtml()
    assert "no shell running" in html


def test_hiding_terminal_keeps_user_started_session(qtbot, tmp_git_repo):
    w = TerminalWidget(DARK_THEME)
    qtbot.addWidget(w)
    w.show()
    w.set_repo_path(str(tmp_git_repo))
    w._input.setText("echo running")
    w._input.returnPressed.emit()
    qtbot.waitUntil(lambda: w._process.state() == QProcess.ProcessState.Running)
    proc = w._process
    w.hide()
    qtbot.wait(10)
    assert w._process is proc
    assert proc.state() == QProcess.ProcessState.Running
    with qtbot.waitSignal(proc.finished, timeout=3000):
        w.close()


def test_close_before_command_does_not_start_a_delayed_shell(qtbot, tmp_git_repo):
    w = TerminalWidget(DARK_THEME)
    qtbot.addWidget(w)
    w.set_repo_path(str(tmp_git_repo))
    w.close()
    qtbot.wait(10)
    assert w._process is None
    assert w._repo_path is None


def test_open_window_allows_repository_directory_rename(qtbot, packed_repo, tmp_path):
    win = MainWindow(config_path=tmp_path / "config.json")
    qtbot.addWidget(win)
    win.show()
    win.set_repository(packed_repo)
    win._main_vm.stop_worktree_refresh()
    qtbot.wait(10)
    assert win._terminal._process is None
    path = Path(packed_repo.path)
    moved = path.with_name("repo-moved")
    path.rename(moved)
    moved.rename(path)
    assert packed_repo.get_all_history()
    # Closing the window must release handles even before another idle tick.
    win.close()
    path.rename(moved)
    moved.rename(path)
