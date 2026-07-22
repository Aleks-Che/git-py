"""Regression tests for terminal input and output encodings."""
from __future__ import annotations

import sys

from src.ui.widgets import terminal_widget


def test_terminal_decodes_cp866_output(monkeypatch) -> None:
    """Invalid UTF-8 shell output falls back to the platform encoding."""
    monkeypatch.setattr(terminal_widget.sys, "getfilesystemencoding", lambda: "cp866")

    output = terminal_widget._decode_terminal_output("Привет мир".encode("cp866"))

    assert output == "Привет мир"


def test_terminal_encodes_input_with_filesystem_encoding() -> None:
    """Shell input is returned as bytes in the platform filesystem encoding."""
    encoded = terminal_widget._encode_terminal_input("test")

    assert isinstance(encoded, bytes)
    assert encoded == "test".encode(sys.getfilesystemencoding(), errors="replace")
