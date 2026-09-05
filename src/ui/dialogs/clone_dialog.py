"""``Clone`` dialog: choose a provider, enter a URL, pick a local path.

The dialog is a passive view — it captures user input and emits
:meth:`accepted` only when the user has picked a valid URL and a
local path and clicked ``Clone``. The actual clone is performed by
:class:`MainViewModel.clone_repository` on the calling site; the
dialog does not talk to ``pygit2`` directly.

Fields
------
* **Provider** (``QComboBox``) — presets that pre-fill the URL field:
  ``GitHub``, ``GitLab``, ``Bitbucket``, ``Custom URL``.
* **URL** (``QLineEdit``) — fully editable; provider changes overwrite
  the field only if the user has not typed anything yet (we track a
  ``_user_typed`` flag so an existing paste is never clobbered).
* **Local path** (``QLineEdit`` + ``Browse…`` button) — absolute
  directory where the clone will land. The path is validated as
  non-empty; we do not check writability here — pygit2 will fail
  later with a sensible error.
* **Generate SSH Key** (``QPushButton``) — opens :class:`SshKeyDialog`
  to generate an ``ed25519`` key pair via ``ssh-keygen`` and surface
  the public key in a read-only field. The private key is *not*
  shown to the user (it stays on disk).

Signals
-------
accepted(str, str)
    Emitted on ``Clone`` click. Payload is ``(url, local_path)``.
key_generated(str, str, str)
    Forwards the generated private/public paths and public contents so the
    caller can activate the key before cloning, even if this dialog is cancelled.

The dialog is a :class:`QDialog` so it can be ``exec()``ed modally.
For tests we expose :meth:`set_provider` / :meth:`set_url` /
:meth:`set_local_path` so the widget can be driven without entering
input into the line edits.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.utils.config import default_ssh_key_path

# Provider presets. ``url_template`` uses ``{user}`` / ``{repo}``
# placeholders; the dialog does not ask for them (it would clutter
# the form), the user is expected to paste a full URL once.
_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "GitHub": {
        "ssh": "git@github.com:{user}/{repo}.git",
        "https": "https://github.com/{user}/{repo}.git",
    },
    "GitLab": {
        "ssh": "git@gitlab.com:{user}/{repo}.git",
        "https": "https://gitlab.com/{user}/{repo}.git",
    },
    "Bitbucket": {
        "ssh": "git@bitbucket.org:{user}/{repo}.git",
        "https": "https://bitbucket.org/{user}/{repo}.git",
    },
}


class SshKeyDialog(QDialog):
    """Small dialog that shells out to ``ssh-keygen`` to create a key pair.

    The user picks a file path (e.g. ``~/.ssh/git-py-ed25519``) and
    an optional comment. We run ``ssh-keygen -t ed25519 -f PATH -N
    "" -C "COMMENT"`` and display the public key on success.

    Signals
    -------
    key_generated(str, str, str)
        Emitted on success with ``(private_key_path, public_key_path,
        public_key_contents)``.
    """

    key_generated = Signal(str, str, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        default_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Generate SSH Key")
        self.resize(640, 360)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._path_edit = QLineEdit()
        resolved_default = (
            Path(default_path) if default_path
            else default_ssh_key_path()
        )
        self._path_edit.setText(str(resolved_default))
        self._path_edit.setPlaceholderText(
            str(default_ssh_key_path()),
        )
        form.addRow("Key file:", self._path_edit)

        self._comment_edit = QLineEdit()
        self._comment_edit.setPlaceholderText("you@example.com")
        form.addRow("Comment:", self._comment_edit)
        layout.addLayout(form)

        self._output = QLineEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("(public key appears here)")
        layout.addWidget(QLabel("Public key:"))
        layout.addWidget(self._output)

        self._generate_btn = QPushButton("Generate")
        self._generate_btn.clicked.connect(self._on_generate)
        layout.addWidget(self._generate_btn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Close,
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def showEvent(self, event: QWidget.showEvent) -> None:  # noqa: N802 - Qt naming
        """Prefill the comment field from ``git config user.email`` on show.

        Done in ``showEvent`` (not ``__init__``) so tests that build the
        dialog without showing it never trigger the subprocess call.
        """
        super().showEvent(event)
        if not self._comment_edit.text():
            self._prefill_comment_from_git_config()

    def _prefill_comment_from_git_config(self) -> None:
        try:
            email_proc = subprocess.run(  # noqa: S603 - intentional subprocess
                ["git", "config", "user.email"],  # noqa: S607
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return
        if email_proc.returncode != 0:
            return
        email = email_proc.stdout.strip()
        if email:
            self._comment_edit.setText(email)

    def _ensure_parent_dir(self, path: Path) -> bool:
        """Create the chosen directory; never relocate a private key implicitly."""
        parent = path.parent
        if parent.exists() and not parent.is_dir():
            QMessageBox.warning(
                self,
                "Generate SSH Key",
                f"Cannot use {parent} as a directory: a file already exists there.\n\n"
                "Move or rename that file and retry, or choose a different key path.",
            )
            return False
        try:
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self, "Generate SSH Key", f"Cannot create SSH key directory {parent}: {exc}",
            )
            return False
        return True

    def _on_generate(self) -> None:
        path_text = self._path_edit.text().strip()
        if not path_text:
            QMessageBox.warning(self, "Generate SSH Key", "Please enter a key file path.")
            return
        path = Path(path_text).expanduser().absolute()
        pub_path = Path(str(path) + ".pub")
        if path.exists() or pub_path.exists():
            QMessageBox.warning(
                self,
                "Generate SSH Key",
                f"Key file {path} or {pub_path} already exists. Choose a different path.",
            )
            return
        ssh_keygen = _find_ssh_keygen()
        if ssh_keygen is None:
            QMessageBox.warning(
                self,
                "Generate SSH Key",
                "`ssh-keygen` was not found on PATH. Install OpenSSH and retry.",
            )
            return
        # Ensure the parent directory exists. On Windows ~/.ssh is often
        # missing; without this ssh-keygen fails with
        # "Saving key '<path>' failed: No such file or directory".
        if not self._ensure_parent_dir(path):
            return
        try:
            completed = subprocess.run(  # noqa: S603 - intentional subprocess
                [
                    ssh_keygen,
                    "-t",
                    "ed25519",
                    "-f",
                    str(path),
                    "-N",
                    "",
                    "-C",
                    self._comment_edit.text().strip(),
                ],
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
        try:
            pub_path = path.with_suffix(path.suffix + ".pub")
            pub = pub_path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(
                self, "Generate SSH Key", f"Generated, but failed to read public key: {exc}",
            )
            return
        self._path_edit.setText(str(path))
        self._output.setText(pub)
        self.key_generated.emit(str(path), str(pub_path), pub)


def _find_ssh_keygen() -> str | None:
    """Return the absolute path of ``ssh-keygen`` on PATH, or ``None``."""
    completed = subprocess.run(  # noqa: S603 - intentional subprocess
        ["where", "ssh-keygen"] if os.name == "nt" else ["which", "ssh-keygen"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    lines = completed.stdout.strip().splitlines()
    return lines[0] if completed.returncode == 0 and lines else None


class CloneDialog(QDialog):
    """Pick a provider / URL / local path and request a clone."""

    accepted = Signal(str, str)  # (url, local_path)
    key_generated = Signal(str, str, str)  # (private, public, contents)

    def __init__(
        self,
        default_path: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Clone Repository")
        self.resize(640, 240)

        self._user_typed = False

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self._provider_combo = QComboBox()
        self._provider_combo.addItem("Custom URL")
        for name in _PROVIDER_PRESETS:
            self._provider_combo.addItem(name)
        self._provider_combo.currentTextChanged.connect(self._on_provider_changed)
        form.addRow("Provider:", self._provider_combo)

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://github.com/owner/repo.git")
        self._url_edit.textEdited.connect(self._on_url_edited)
        form.addRow("URL:", self._url_edit)

        path_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("C:/path/to/clone")
        if default_path:
            self._path_edit.setText(default_path)
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._on_browse)
        path_row.addWidget(self._path_edit, stretch=1)
        path_row.addWidget(self._browse_btn)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        form.addRow("Local path:", path_widget)
        layout.addLayout(form)

        ssh_row = QHBoxLayout()
        self._ssh_btn = QPushButton("Generate SSH Key…")
        self._ssh_btn.clicked.connect(self._on_generate_ssh)
        ssh_row.addWidget(self._ssh_btn)
        ssh_row.addStretch(1)
        layout.addLayout(ssh_row)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        self._button_box.accepted.connect(self._on_accept)
        self._button_box.rejected.connect(self.reject)
        ok_btn = self._button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Clone")
            ok_btn.setDefault(True)
        layout.addWidget(self._button_box)

    # ----- public API (used by tests) ---------------------------------

    def set_provider(self, name: str) -> None:
        """Programmatically pick ``name`` (must be a known provider label)."""
        idx = self._provider_combo.findText(name)
        if idx >= 0:
            self._provider_combo.setCurrentIndex(idx)

    def provider(self) -> str:
        return self._provider_combo.currentText()

    def set_url(self, url: str) -> None:
        self._url_edit.setText(url)
        self._user_typed = True

    def url(self) -> str:
        return self._url_edit.text().strip()

    def set_local_path(self, path: str) -> None:
        self._path_edit.setText(path)

    def local_path(self) -> str:
        return self._path_edit.text().strip()

    # ----- internals ---------------------------------------------------

    def _on_provider_changed(self, name: str) -> None:
        """Pre-fill the URL field with a sample URL for the picked provider.

        The user-edited flag is honoured: we only overwrite the field
        if the user has not typed anything yet.
        """
        if self._user_typed:
            return
        if name in _PROVIDER_PRESETS:
            self._url_edit.setText(_PROVIDER_PRESETS[name]["https"])
            self._user_typed = False  # this is a *preset*, not a user edit

    def _on_url_edited(self, _text: str) -> None:
        self._user_typed = True

    def _on_browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select clone destination")
        if directory:
            self._path_edit.setText(directory)

    def _on_generate_ssh(self) -> None:
        dialog = SshKeyDialog(
            self,
            default_path=str(default_ssh_key_path()),
        )
        dialog.key_generated.connect(self.key_generated)
        dialog.exec()

    def _on_accept(self) -> None:
        url = self.url()
        path = self.local_path()
        if not url:
            QMessageBox.warning(self, "Clone Repository", "Please enter a URL.")
            return
        if not path:
            QMessageBox.warning(self, "Clone Repository", "Please choose a local path.")
            return
        # If the destination directory does not exist yet, treat it as
        # the **parent** directory and append the repository name. This
        # matches what most Git GUI clients do: pick the folder where
        # the new repo should live, and the repo is created as a child.
        path = self._resolve_clone_target(url, path)
        self.accepted.emit(url, path)
        self.accept()

    def _resolve_clone_target(self, url: str, path: str) -> str:
        """Append the repo name to ``path`` unless it already ends there.

        The path the user picks is treated as the **parent directory**
        for the new clone, matching the behaviour of GitHub Desktop,
        Sourcetree, GitKraken and other Git GUI clients. Whether or
        not the path already exists on disk is irrelevant — append the
        repo name derived from ``url`` and let ``git clone`` create the
        directory as needed.

        Rules:
        - If we cannot parse a repo name from ``url`` → leave path alone.
        - If the last segment of ``path`` already equals the repo name
          → leave as is (avoid duplicating the name).
        - Otherwise → append the repo name.
        """
        try:
            from src.core.operations import _extract_repo_name
            repo_name = _extract_repo_name(url)
        except ImportError:
            return path
        if not repo_name:
            return path
        target = Path(path)
        # If user already pointed at a path that ends with the repo
        # name, do not duplicate.
        if target.name == repo_name:
            return path
        # Otherwise always append — the user's chosen path is treated
        # as the parent directory.
        return str(target / repo_name)


__all__ = ["CloneDialog", "SshKeyDialog"]
