"""First-run key generation and immediate settings activation."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from src.ui.dialogs.clone_dialog import CloneDialog, SshKeyDialog
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.ui.main_window import MainWindow
from src.utils.config import load_config, save_config


@pytest.fixture
def generated_key_dialog(tmp_path, monkeypatch):
    """Drive the real generation handler, substituting only ssh-keygen itself."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("src.ui.dialogs.clone_dialog._find_ssh_keygen", lambda: "ssh-keygen")
    original_run = subprocess.run

    def run(args, **kwargs):
        if args[0] != "ssh-keygen":
            return original_run(args, **kwargs)
        key = Path(args[args.index("-f") + 1])
        assert key.parent == tmp_path / ".ssh"
        assert key.parent.is_dir()
        key.write_text("test private key", encoding="utf-8")
        Path(str(key) + ".pub").write_text("ssh-ed25519 TEST public\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    def generate(dialog):
        dialog._on_generate()
        dialog.reject()
        return 0

    monkeypatch.setattr("src.ui.dialogs.clone_dialog.subprocess.run", run)
    monkeypatch.setattr(SshKeyDialog, "exec", generate)
    return tmp_path / ".ssh" / "git-py-ed25519"


def test_clone_generation_activates_key_and_settings_show_it(
    qtbot, tmp_path, monkeypatch, generated_key_dialog,
):
    config_path = tmp_path / "settings.json"
    window = MainWindow(config_path=config_path)
    qtbot.addWidget(window)
    assert not config_path.exists()

    def generate_and_close(dialog):
        dialog._on_generate_ssh()
        dialog.reject()
        return 0

    monkeypatch.setattr(CloneDialog, "exec", generate_and_close)
    window._open_clone_dialog()

    settings = SettingsDialog(config_path=str(config_path))
    qtbot.addWidget(settings)
    assert settings._ssh_priv_edit.text() == str(generated_key_dialog)
    assert settings._ssh_pub_edit.text() == str(generated_key_dialog) + ".pub"
    assert settings._ssh_pub_view.toPlainText() == "ssh-ed25519 TEST public"
    assert not (tmp_path / ".ssh-py").exists()
    window.close()
    assert load_config(config_path)["ssh_private_key"] == str(generated_key_dialog)


def test_settings_generation_activates_key_even_on_cancel(
    qtbot, tmp_path, generated_key_dialog,
):
    config_path = tmp_path / "settings.json"
    save_config(config_path, {"author_name": "Original", "custom_setting": 1})
    settings = SettingsDialog(config_path=str(config_path))
    qtbot.addWidget(settings)
    settings._author_name_edit.setText("Unsaved edit")
    save_config(config_path, {"author_name": "Original", "custom_setting": 2})

    settings._on_generate_ssh()
    assert settings._ssh_pub_view.toPlainText() == "ssh-ed25519 TEST public"
    settings.reject()

    saved = load_config(config_path)
    assert saved["ssh_private_key"] == str(generated_key_dialog)
    assert saved["ssh_public_key"] == str(generated_key_dialog) + ".pub"
    assert saved["author_name"] == "Original"
    assert saved["custom_setting"] == 2


def test_real_keygen_creates_default_pair_in_ssh(qtbot, tmp_path, monkeypatch):
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        pytest.skip("ssh-keygen is not installed")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr("src.ui.dialogs.clone_dialog._find_ssh_keygen", lambda: keygen)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    generated = []
    dialog.key_generated.connect(lambda *args: generated.append(args))

    dialog._on_generate()

    assert not warnings
    key = tmp_path / ".ssh" / "git-py-ed25519"
    assert key.is_file()
    assert Path(str(key) + ".pub").is_file()
    assert generated[0][:2] == (str(key), str(key) + ".pub")
    assert generated[0][2].startswith("ssh-ed25519 ")
    assert not (tmp_path / ".ssh-py").exists()
