"""Settings dialog: author identity and SSH key paths.

Opened from ``File > Settings…``. Reads current values from the app
config JSON, lets the user edit them, and saves back on accept.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.ui.dialogs.clone_dialog import SshKeyDialog, _find_ssh_keygen
from src.ui.icons import toolbar_icon
from src.utils.config import load_config, save_config


class SettingsDialog(QDialog):
    """Modal dialog for editing app-wide settings.

    Reads the config on construction, populates the form, and writes
    back on ``OK``.  Fields:

    * **Author Name / Author Email** — used for commit signatures.
    * **Use default Git Credentials** — when checked, the app reads
      author info from ``git config`` instead of the fields above.
    * **SSH Private Key / SSH Public Key** — file paths for SSH auth.
    * **Generate SSH Key…** — opens ``ssh-keygen`` to create a new
      ed25519 key pair and pre-fills the path fields.
    """

    def __init__(self, config_path: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(640, 480)

        self._config_path = config_path
        self._config = load_config(config_path) if config_path else {}

        self._refresh_pubkey_timer = QTimer(self)
        self._refresh_pubkey_timer.setSingleShot(True)
        self._refresh_pubkey_timer.setInterval(150)
        self._refresh_pubkey_timer.timeout.connect(self._refresh_public_key_view)

        self._build_ui()
        self._load_from_config()

    # ----- UI construction ------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()

        # -- Author section --
        self._author_name_edit = QLineEdit()
        self._author_name_edit.setPlaceholderText("Your Name")
        form.addRow("Author Name:", self._author_name_edit)

        self._author_email_edit = QLineEdit()
        self._author_email_edit.setPlaceholderText("you@example.com")
        form.addRow("Author Email:", self._author_email_edit)

        self._use_default_cred_cb = QCheckBox("Use default Git Credentials")
        self._use_default_cred_cb.toggled.connect(self._on_default_cred_toggled)
        form.addRow(self._use_default_cred_cb)

        form.addRow(QLabel())  # vertical spacer row

        # -- SSH section --
        self._ssh_priv_edit = QLineEdit()
        self._ssh_priv_edit.setPlaceholderText(str(Path.home() / ".ssh" / "id_rsa"))
        priv_row = QHBoxLayout()
        priv_row.addWidget(self._ssh_priv_edit, stretch=1)
        priv_browse = QPushButton("Browse…")
        priv_browse.clicked.connect(lambda: self._on_browse(self._ssh_priv_edit))
        priv_row.addWidget(priv_browse)
        priv_widget = QWidget()
        priv_widget.setLayout(priv_row)
        form.addRow("SSH Private Key:", priv_widget)

        self._ssh_pub_edit = QLineEdit()
        self._ssh_pub_edit.setPlaceholderText(str(Path.home() / ".ssh" / "id_rsa.pub"))
        pub_row = QHBoxLayout()
        pub_row.addWidget(self._ssh_pub_edit, stretch=1)
        pub_browse = QPushButton("Browse…")
        pub_browse.clicked.connect(lambda: self._on_browse(self._ssh_pub_edit))
        pub_row.addWidget(pub_browse)
        pub_widget = QWidget()
        pub_widget.setLayout(pub_row)
        form.addRow("SSH Public Key:", pub_widget)

        gen_btn = QPushButton("Generate SSH Key…")
        gen_btn.clicked.connect(self._on_generate_ssh)
        form.addRow(gen_btn)

        # -- Public key preview + copy --
        self._ssh_pub_view = QPlainTextEdit()
        self._ssh_pub_view.setReadOnly(True)
        self._ssh_pub_view.setMinimumHeight(70)
        self._ssh_pub_view.setMaximumHeight(110)
        # Mono-space font for SSH key content (long base64 strings).
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        self._ssh_pub_view.setFont(mono)
        self._ssh_pub_view.setPlaceholderText(
            "No public key file found at the configured path."
        )

        self._copy_btn = QToolButton()
        self._copy_btn.setIcon(toolbar_icon("copy"))
        self._copy_btn.setToolTip("Copy public key to clipboard")
        self._copy_btn.setText("Copy")
        self._copy_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._copy_btn.clicked.connect(self._on_copy_public_key)

        pub_view_row = QHBoxLayout()
        pub_view_row.addWidget(self._ssh_pub_view, stretch=1)
        pub_view_row.addWidget(self._copy_btn)
        pub_view_widget = QWidget()
        pub_view_widget.setLayout(pub_view_row)
        form.addRow("Public Key:", pub_view_widget)

        # React to path changes → debounced refresh.
        self._ssh_priv_edit.textChanged.connect(self._on_path_changed)
        self._ssh_pub_edit.textChanged.connect(self._on_path_changed)

        layout.addLayout(form)
        layout.addStretch(1)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    # ----- load / save ----------------------------------------------------

    def _load_from_config(self) -> None:
        c = self._config
        self._author_name_edit.setText(c.get("author_name", ""))
        self._author_email_edit.setText(c.get("author_email", ""))
        self._use_default_cred_cb.setChecked(
            c.get("use_default_git_credentials", True),
        )
        self._ssh_priv_edit.setText(c.get("ssh_private_key", ""))
        self._ssh_pub_edit.setText(c.get("ssh_public_key", ""))
        # Load public key content synchronously at startup so the user
        # sees it immediately when they open Settings.
        self._refresh_public_key_view()

    def _on_accept(self) -> None:
        c = self._config
        c["author_name"] = self._author_name_edit.text().strip()
        c["author_email"] = self._author_email_edit.text().strip()
        c["use_default_git_credentials"] = self._use_default_cred_cb.isChecked()
        c["ssh_private_key"] = self._ssh_priv_edit.text().strip()
        c["ssh_public_key"] = self._ssh_pub_edit.text().strip()
        if self._config_path:
            save_config(self._config_path, c)
        self.accept()

    # ----- internal helpers -----------------------------------------------

    def _on_default_cred_toggled(self, checked: bool) -> None:
        self._author_name_edit.setEnabled(not checked)
        self._author_email_edit.setEnabled(not checked)

    def _on_browse(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select file", edit.text() or str(Path.home() / ".ssh"),
        )
        if path:
            edit.setText(path)

    def _on_generate_ssh(self) -> None:
        """Open a small dialog to generate an ed25519 key pair."""
        default = self._ssh_priv_edit.text().strip() or str(
            Path.home() / ".ssh" / "git-py-ed25519",
        )
        dialog = SshKeyDialog(self, default_path=default)
        dialog.key_generated.connect(self._on_ssh_key_generated)
        dialog.exec()

    def _on_ssh_key_generated(self, priv: str, pub: str, _contents: str) -> None:
        self._ssh_priv_edit.setText(priv)
        self._ssh_pub_edit.setText(pub)

    # ----- public-key view & copy -----------------------------------------

    def _on_path_changed(self, _text: str) -> None:
        """Debounced refresh of the public key preview when paths change."""
        self._refresh_pubkey_timer.start()

    def _refresh_public_key_view(self) -> None:
        """Read the configured public key file and show it in the preview.

        Priority: explicit public-key path if set, else derive from the
        private-key path (``priv`` → ``priv + ".pub"``).
        """
        pub_path_text = self._ssh_pub_edit.text().strip()
        if pub_path_text:
            candidate = Path(pub_path_text)
        else:
            priv_text = self._ssh_priv_edit.text().strip()
            if not priv_text:
                self._ssh_pub_view.clear()
                return
            candidate = Path(priv_text + ".pub")
        try:
            content = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            # File missing / unreadable — clear and show placeholder.
            self._ssh_pub_view.clear()
            return
        self._ssh_pub_view.setPlainText(content)

    def _on_copy_public_key(self) -> None:
        """Copy the currently displayed public key to the clipboard."""
        text = self._ssh_pub_view.toPlainText().strip()
        if not text:
            QMessageBox.information(
                self,
                "Copy Public Key",
                "No public key to copy. Generate one or set a valid path first.",
            )
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        # Brief tooltip near the button as feedback (2s).
        QToolTip.showText(
            self._copy_btn.mapToGlobal(self._copy_btn.rect().bottomLeft()),
            "Copied!",
            self._copy_btn,
            self._copy_btn.rect(),
            2000,
        )

    def _do_generate_ssh(self) -> None:
        priv_default = self._ssh_priv_edit.text().strip() or str(
            Path.home() / ".ssh" / "id_ed25519",
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save SSH Private Key",
            priv_default,
            "All Files (*)",
        )
        if not path:
            return

        priv_path = Path(path)
        if priv_path.exists():
            QMessageBox.warning(
                self,
                "Generate SSH Key",
                f"File {priv_path} already exists. Choose a different path.",
            )
            return

        pub_path = priv_path.with_suffix(priv_path.suffix + ".pub")
        ssh_keygen = _find_ssh_keygen()
        if ssh_keygen is None:
            QMessageBox.warning(
                self,
                "Generate SSH Key",
                "`ssh-keygen` was not found on PATH. Install OpenSSH and retry.",
            )
            return

        try:
            completed = subprocess.run(
                [ssh_keygen, "-t", "ed25519", "-f", str(priv_path), "-N", "", "-C", ""],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            QMessageBox.warning(
                self, "Generate SSH Key", f"Failed to run ssh-keygen: {exc}",
            )
            return

        if completed.returncode != 0:
            QMessageBox.warning(
                self,
                "Generate SSH Key",
                f"ssh-keygen failed:\n{completed.stderr.strip() or completed.stdout.strip()}",
            )
            return

        self._ssh_priv_edit.setText(str(priv_path))
        if pub_path.exists():
            self._ssh_pub_edit.setText(str(pub_path))

        QMessageBox.information(
            self,
            "SSH Key Generated",
            f"Key pair created:\nPrivate: {priv_path}\nPublic:  {pub_path}",
        )


__all__ = ["SettingsDialog"]
