"""Embedded terminal panel (Stage 7).

Runs a shell (:file:`cmd.exe` on Windows, ``/bin/bash`` on POSIX) via
:class:`QProcess` in the repository root so the user can interact with
Git through the CLI directly.

Structure: a read-only :class:`QTextEdit` for output on top, a
single-line :class:`QLineEdit` for input on the bottom.

ANSI escape codes (SGR sequences) are parsed into HTML spans so the
terminal output uses the theme's text / accent colours rather than
plain monochrome text. The theme is passed when the widget is
constructed and reused for each process launch — no hot-reloading.

Lifecycle: the process is started when :meth:`set_repo_path` is
called with a non-``None`` path and stopped when it is set to
``None`` or the widget is hidden/destroyed.
"""
from __future__ import annotations

import os
import re
import sys

from PySide6.QtCore import QProcess, Qt
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.utils.theme import Theme

# SGR code → CSS style fragment.  Only the subset emitted by common
# CLI tools (``git diff``, ``grep --color``, etc.) is mapped.
_SGR_MAP: dict[int, str] = {
    0: "</span>",
    1: "font-weight: bold;",
    30: "color: {{text}};",
    31: "color: #E8685A;",
    32: "color: #3FB950;",
    33: "color: #F5B947;",
    34: "color: #5B8FF9;",
    35: "color: #A371F7;",
    36: "color: #43BCCD;",
    37: "color: {{text}};",
    90: "color: #8B8B8B;",
    91: "color: #FF6B6B;",
    92: "color: #56D364;",
    93: "color: #F0883E;",
    94: "color: #79C0FF;",
    95: "color: #D2A8FF;",
    96: "color: #56D4DD;",
    97: "color: #D4D4D4;",
    40: "background-color: {{text}};",
    41: "background-color: #E8685A;",
    42: "background-color: #3FB950;",
    43: "background-color: #F5B947;",
    44: "background-color: #5B8FF9;",
    45: "background-color: #A371F7;",
    46: "background-color: #43BCCD;",
    47: "background-color: #D4D4D4;",
}


_SGR_RE = re.compile(r"\x1b\[([\d;]*)m")


def _decode_terminal_output(raw: bytes) -> str:
    """Decode shell output, falling back to the platform filesystem encoding."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(sys.getfilesystemencoding(), errors="replace")


def _encode_terminal_input(text: str) -> bytes:
    """Encode shell input using the platform filesystem encoding."""
    return text.encode(sys.getfilesystemencoding(), errors="replace")


def _ansi_to_html(text: str, theme: Theme) -> str:
    """Convert ``text`` containing ANSI SGR sequences to HTML.

    Non-SGR escape sequences (cursor movement, clear screen, etc.)
    are stripped without replacing — the widget already supports
    scrolling so those codes are just noise.
    """
    text = text.replace("\x1b[H", "").replace("\x1b[2J", "").replace("\x1b[K", "")
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    result: list[str] = []
    last_end = 0
    open_spans: list[str] = []

    for match in _SGR_RE.finditer(text):
        result.append(text[last_end : match.start()])
        last_end = match.end()
        codes = match.group(1)
        if not codes:
            codes = "0"
        for code_s in codes.split(";"):
            try:
                code = int(code_s)
            except ValueError:
                continue
            if code == 0:
                for _ in open_spans:
                    result.append("</span>")
                open_spans.clear()
            elif code in _SGR_MAP:
                style = _SGR_MAP[code].replace("{{text}}", theme.text)
                result.append(f"<span style='{style}'>")
                open_spans.append(code)

    result.append(text[last_end:])

    for _ in open_spans:
        result.append("</span>")

    return "".join(result)


class TerminalWidget(QWidget):
    """Embedded terminal using :class:`QProcess`."""

    def __init__(
        self,
        theme: Theme | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        from src.utils.theme import DARK_THEME

        self._theme: Theme = theme or DARK_THEME
        self._process: QProcess | None = None
        self._repo_path: str | None = None

        self._output = QTextEdit(self)
        self._output.setReadOnly(True)
        self._output.setFont(self._mono_font())
        self._output.setStyleSheet(
            f"QTextEdit {{ background-color: {self._theme.bg}; "
            f"color: {self._theme.text}; border: none; }}"
        )
        self._output.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self._output.document().setMaximumBlockCount(2000)

        self._input = QLineEdit(self)
        self._input.setFont(self._mono_font())
        self._input.setStyleSheet(
            f"QLineEdit {{ background-color: {self._theme.bg}; "
            f"color: {self._theme.text}; border: 1px solid #3F3F46; "
            f"padding: 4px 6px; }}"
        )
        self._input.setPlaceholderText("$ type a command and press Enter…")
        self._input.returnPressed.connect(self._on_input)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._output, stretch=1)
        layout.addWidget(self._input)

    # ----- lifecycle ---------------------------------------------------

    def set_repo_path(self, path: str | None) -> None:
        """(Re-)start the shell in ``path``, or stop if ``None``.

        Called by :class:`MainWindow` when a repository is opened
        (:attr:`MainViewModel.repository_changed` signal).
        """
        if path == self._repo_path and self._process is not None:
            return
        self._stop_shell()
        self._repo_path = path
        if path is not None:
            # Defer startup by one event-loop tick so the window
            # rendering completes first (the QProcess does not block,
            # but its output instantly hits the text widget).
            from PySide6.QtCore import QTimer
            QTimer.singleShot(
                0, lambda p=path: self._start_shell(p) if self._repo_path == p else None,
            )

    def close(self) -> None:
        """Stop the shell and clear the widget."""
        self._stop_shell()
        self._output.clear()

    # ----- internals ---------------------------------------------------

    def _start_shell(self, cwd: str) -> None:
        shell = "cmd.exe" if sys.platform == "win32" else os.environ.get("SHELL", "/bin/bash")
        args = ["/Q", "/K", "chcp 65001 >nul"] if sys.platform == "win32" else ["-i"]
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.setWorkingDirectory(str(cwd))
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        proc.start(shell, args)
        self._process = proc

    def _stop_shell(self) -> None:
        if self._process is None:
            return
        proc = self._process
        self._process = None
        # Disconnect signals *before* killing so the "process
        # crashed / finished" messages are not appended to the
        # terminal while we tear down the old shell.
        try:
            proc.finished.disconnect(self._on_finished)
            proc.errorOccurred.disconnect(self._on_error)
        except (RuntimeError, TypeError):
            pass
        if proc.state() != QProcess.ProcessState.NotRunning:
            proc.kill()
        # deleteLater queues the QProcess for deferred deletion
        # without blocking the UI thread — waitForFinished() was
        # the culprit that froze the window for up to 2s on every
        # repository switch.
        proc.deleteLater()

    def _on_input(self) -> None:
        text = self._input.text()
        if not text.strip() and not text:
            return
        self._input.clear()
        if self._process is None or self._process.state() != QProcess.ProcessState.Running:
            self._append_html(
                f"<span style='color: #8B8B8B'>"
                f"$ {text} [no shell running]</span><br>"
            )
            return
        self._append_html(
            f"<span style='color: {self._theme.accent}; font-weight: bold'>"
            f"> {text}</span><br>"
        )
        self._process.write(_encode_terminal_input(text + "\r\n"))

    def _on_stdout(self) -> None:
        if self._process is None:
            return
        data = self._process.readAllStandardOutput().data()
        text = _decode_terminal_output(data)
        html = _ansi_to_html(text, self._theme).replace("\n", "<br>").replace("\r", "")
        self._append_html(html)

    def _on_stderr(self) -> None:
        if self._process is None:
            return
        data = self._process.readAllStandardError().data()
        text = _decode_terminal_output(data)
        html = (
            f"<span style='color: #E8685A'>"
            f"{_ansi_to_html(text, self._theme)}</span>"
        )
        html = html.replace("\n", "<br>").replace("\r", "")
        self._append_html(html)

    def _on_finished(self, exit_code: int, _exit_status: QProcess.ExitStatus) -> None:
        if self._process is None:
            return
        self._append_html(
            f"<span style='color: #8B8B8B'>"
            f"[process exited with code {exit_code}]</span><br>"
        )
        self._process = None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        msgs = {
            QProcess.ProcessError.FailedToStart: "Failed to start shell",
            QProcess.ProcessError.Crashed: "Shell process crashed",
            QProcess.ProcessError.Timedout: "Shell process timed out",
            QProcess.ProcessError.WriteError: "Write to shell failed",
            QProcess.ProcessError.ReadError: "Read from shell failed",
            QProcess.ProcessError.UnknownError: "Unknown shell error",
        }
        msg = msgs.get(error, f"Shell error: {error}")
        self._append_html(
            f"<span style='color: #E8685A; font-weight: bold'>"
            f"[{msg}]</span><br>"
        )

    def _append_html(self, html: str) -> None:
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    @staticmethod
    def _mono_font() -> QFont:
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        return font
