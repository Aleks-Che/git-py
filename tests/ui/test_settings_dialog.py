"""Tests for the SettingsDialog — focus on SSH key generation UX.

Verifies update5 changes:
- ``_on_generate_ssh`` opens ``SshKeyDialog`` with ``default_path`` from the
  current private-key field (or the built-in ``~/.ssh/git-py-ed25519``).
- The generated ``key_generated`` signal fills both SSH key fields
  automatically so the user does not have to copy/paste.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from src.ui.dialogs.clone_dialog import SshKeyDialog
from src.ui.dialogs.settings_dialog import SettingsDialog


def _make_settings_dialog(qtbot, tmp_path: Path) -> SettingsDialog:
    """Build a SettingsDialog with an isolated config file."""
    config = tmp_path / "settings.json"
    dialog = SettingsDialog(config_path=str(config))
    qtbot.addWidget(dialog)
    return dialog


def test_settings_dialog_generate_passes_current_path_as_default(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_on_generate_ssh`` should pass the current private-key value as default_path."""
    dialog = _make_settings_dialog(qtbot, tmp_path)
    existing = tmp_path / "my-existing-key"
    dialog._ssh_priv_edit.setText(str(existing))  # noqa: SLF001

    captured: dict[str, object] = {}

    class _FakeSshDialog:
        def __init__(self, parent=None, default_path=None) -> None:  # noqa: ANN001
            captured["default_path"] = default_path
            captured["parent"] = parent
            self._closed = False
            self.key_generated = _SignalStub()

        def exec(self) -> int:  # noqa: D401
            self._closed = True
            return 0

    class _SignalStub:
        def connect(self, slot) -> None:  # noqa: ANN001
            self._slot = slot

    monkeypatch.setattr(
        "src.ui.dialogs.settings_dialog.SshKeyDialog", _FakeSshDialog,
    )
    dialog._on_generate_ssh()  # noqa: SLF001

    assert captured["default_path"] == str(existing)


def test_settings_dialog_generate_uses_built_in_default_when_field_empty(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If SSH private key field is empty, fall back to ``~/.ssh/git-py-ed25519``."""
    dialog = _make_settings_dialog(qtbot, tmp_path)
    dialog._ssh_priv_edit.setText("")  # noqa: SLF001

    captured: dict[str, object] = {}

    class _FakeSshDialog:
        def __init__(self, parent=None, default_path=None) -> None:  # noqa: ANN001
            captured["default_path"] = default_path
            self.key_generated = _SignalStub()

        def exec(self) -> int:
            return 0

    class _SignalStub:
        def connect(self, slot) -> None:  # noqa: ANN001
            self._slot = slot

    monkeypatch.setattr(
        "src.ui.dialogs.settings_dialog.SshKeyDialog", _FakeSshDialog,
    )
    dialog._on_generate_ssh()  # noqa: SLF001

    assert captured["default_path"] is not None
    assert str(captured["default_path"]).endswith("/.ssh/git-py-ed25519") or \
        str(captured["default_path"]).endswith(r"\.ssh\git-py-ed25519")


def test_settings_dialog_signal_fills_ssh_fields(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``SshKeyDialog.key_generated`` fires, both SSH fields get populated."""
    dialog = _make_settings_dialog(qtbot, tmp_path)

    priv = tmp_path / "id_test"
    pub = tmp_path / "id_test.pub"
    captured: dict[str, object] = {}

    class _FakeSshDialog:
        def __init__(self, parent=None, default_path=None) -> None:  # noqa: ANN001
            self.key_generated = _SignalStub()
            captured["instance"] = self

        def exec(self) -> int:
            # Simulate the user clicking "Generate" then closing the dialog.
            self.key_generated._fire(str(priv), str(pub), "ssh-ed25519 AAAA\n")
            return 0

    class _SignalStub:
        def __init__(self) -> None:
            self._slot = None

        def connect(self, slot) -> None:  # noqa: ANN001
            self._slot = slot

        def _fire(self, *args) -> None:  # noqa: ANN002
            if self._slot is not None:
                self._slot(*args)

    monkeypatch.setattr(
        "src.ui.dialogs.settings_dialog.SshKeyDialog", _FakeSshDialog,
    )
    dialog._on_generate_ssh()  # noqa: SLF001

    assert dialog._ssh_priv_edit.text() == str(priv)  # noqa: SLF001
    assert dialog._ssh_pub_edit.text() == str(pub)  # noqa: SLF001


def test_settings_dialog_save_persists_generated_ssh_paths(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clicking OK after auto-fill must persist the values to the config file."""
    config = tmp_path / "settings.json"
    dialog = SettingsDialog(config_path=str(config))
    qtbot.addWidget(dialog)

    priv = "/home/test/.ssh/id_test"
    pub = "/home/test/.ssh/id_test.pub"

    class _FakeSshDialog:
        def __init__(self, parent=None, default_path=None) -> None:  # noqa: ANN001
            self.key_generated = _SignalStub()

        def exec(self) -> int:
            self.key_generated._fire(priv, pub, "ssh-ed25519 AAAA\n")
            return 0

    class _SignalStub:
        def __init__(self) -> None:
            self._slot = None

        def connect(self, slot) -> None:  # noqa: ANN001
            self._slot = slot

        def _fire(self, *args) -> None:  # noqa: ANN002
            self._slot(*args)

    monkeypatch.setattr(
        "src.ui.dialogs.settings_dialog.SshKeyDialog", _FakeSshDialog,
    )

    dialog._on_generate_ssh()  # noqa: SLF001
    dialog._on_accept()  # noqa: SLF001

    assert config.exists()
    import json
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["ssh_private_key"] == priv
    assert saved["ssh_public_key"] == pub


# Smoke test that we still import and build cleanly (catches regressions).
def test_settings_dialog_builds(qtbot, tmp_path) -> None:
    dialog = SettingsDialog(config_path=str(tmp_path / "x.json"))
    qtbot.addWidget(dialog)
    assert dialog._ssh_priv_edit is not None  # noqa: SLF001
    assert dialog._ssh_pub_edit is not None  # noqa: SLF001


__all__ = [
    "test_settings_dialog_generate_passes_current_path_as_default",
    "test_settings_dialog_generate_uses_built_in_default_when_field_empty",
    "test_settings_dialog_signal_fills_ssh_fields",
    "test_settings_dialog_save_persists_generated_ssh_paths",
    "test_settings_dialog_builds",
]


# Avoid an unused-import lint warning for the SshKeyDialog type used implicitly.
_ = SshKeyDialog
