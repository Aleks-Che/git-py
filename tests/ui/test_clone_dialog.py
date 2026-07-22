"""UI tests for :class:`CloneDialog`.

Exercises the dialog under ``pytest-qt`` (``QT_QPA_PLATFORM=offscreen``
on headless CI). We drive the widget through its public methods
(:meth:`set_provider`, :meth:`set_url`, :meth:`set_local_path`)
rather than entering keystrokes into the line edits.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox
from src.ui.dialogs.clone_dialog import CloneDialog, SshKeyDialog

# ----- construction --------------------------------------------------------


def test_dialog_builds(qtbot) -> None:
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.windowTitle() == "Clone Repository"
    # Default provider is "Custom URL"; URL and path are empty.
    assert dialog.provider() == "Custom URL"
    assert dialog.url() == ""
    assert dialog.local_path() == ""


def test_dialog_default_local_path(qtbot) -> None:
    dialog = CloneDialog(default_path="C:/some/path")
    qtbot.addWidget(dialog)
    assert dialog.local_path() == "C:/some/path"


# ----- provider preset -----------------------------------------------------


def test_picking_provider_prefills_url(qtbot) -> None:
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    dialog.set_provider("GitHub")
    assert "github.com" in dialog.url()
    assert "user" in dialog.url()  # template placeholders left in


def test_changing_provider_does_not_clobber_user_url(qtbot) -> None:
    """Once the user has typed a URL, switching provider must not overwrite it."""
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    dialog.set_url("https://my.custom/host/repo.git")
    dialog.set_provider("GitHub")
    assert dialog.url() == "https://my.custom/host/repo.git"


def test_picking_custom_url_leaves_url_empty(qtbot) -> None:
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    dialog.set_url("https://my.custom/host/repo.git")
    dialog.set_provider("Custom URL")
    # No preset for "Custom URL" — the user-typed URL stays.
    assert dialog.url() == "https://my.custom/host/repo.git"


# ----- accept with empty fields shows warning ------------------------------


def test_accept_with_empty_url_does_not_emit(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok),
    )
    captured: list[tuple[str, str]] = []
    dialog.accepted.connect(lambda u, p: captured.append((u, p)))
    dialog.set_local_path("/tmp/x")
    dialog._on_accept()  # noqa: SLF001
    assert captured == []


def test_accept_with_empty_path_does_not_emit(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok),
    )
    captured: list[tuple[str, str]] = []
    dialog.accepted.connect(lambda u, p: captured.append((u, p)))
    dialog.set_url("https://example.com/repo.git")
    dialog._on_accept()  # noqa: SLF001
    assert captured == []


def test_accept_with_both_fields_emits(qtbot) -> None:
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    captured: list[tuple[str, str]] = []
    dialog.accepted.connect(lambda u, p: captured.append((u, p)))
    dialog.set_url("https://example.com/repo.git")
    dialog.set_local_path("/tmp/clone-target")
    dialog._on_accept()  # noqa: SLF001
    assert captured == [("https://example.com/repo.git", "/tmp/clone-target")]


# ----- SSH key dialog (no real subprocess) --------------------------------


def test_ssh_dialog_with_empty_path_warns(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Ok),
    )
    dialog._on_generate()  # noqa: SLF001
    assert dialog._output.text() == ""  # noqa: SLF001


def test_ssh_dialog_existing_file_warns(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = tmp_path / "id_test"
    existing.write_text("already exists")
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    warned: list[bool] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(
            lambda *args, **kwargs: (
                warned.append(True),
                QMessageBox.StandardButton.Ok,
            )[1],
        ),
    )
    dialog._path_edit.setText(str(existing))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001
    assert warned


def test_ssh_dialog_ssh_keygen_not_found(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``ssh-keygen`` is not on PATH, the dialog warns and emits nothing."""
    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: None)
    warned: list[bool] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(
            lambda *args, **kwargs: (
                warned.append(True),
                QMessageBox.StandardButton.Ok,
            )[1],
        ),
    )
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    dialog._path_edit.setText(str(tmp_path / "id_test"))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001
    assert warned
    assert dialog._output.text() == ""  # noqa: SLF001


def test_ssh_dialog_subprocess_failure_warns(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing ``ssh-keygen`` call surfaces as a warning."""
    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")
    fake = type(
        "FakeProcess",
        (),
        {
            "returncode": 1,
            "stdout": "",
            "stderr": "boom",
        },
    )()
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: fake,
    )
    warned: list[bool] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(
            lambda *args, **kwargs: (
                warned.append(True),
                QMessageBox.StandardButton.Ok,
            )[1],
        ),
    )
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    dialog._path_edit.setText(str(tmp_path / "id_test"))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001
    assert warned


def test_ssh_dialog_success(qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful ``ssh-keygen`` run populates the public key field."""
    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")

    key_path = tmp_path / "id_test"
    pub_path = tmp_path / "id_test.pub"

    # Mock ``subprocess.run`` to write the files as a side effect.
    def _fake_run(args, **kwargs):  # noqa: ANN001
        key_path.write_text("PRIVATE\n")
        pub_path.write_text("ssh-ed25519 AAAA... comment\n")
        return type(
            "FakeProcess",
            (),
            {"returncode": 0, "stdout": "", "stderr": ""},
        )()

    monkeypatch.setattr("subprocess.run", _fake_run)

    emitted: list[str] = []
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    dialog.key_generated.connect(emitted.append)
    dialog._path_edit.setText(str(key_path))  # noqa: SLF001
    dialog._comment_edit.setText("tester@example.com")  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    assert "ssh-ed25519" in dialog._output.text()  # noqa: SLF001
    assert emitted and "ssh-ed25519" in emitted[0]


# ----- generate-ssh-key button on CloneDialog opens sub-dialog ------------


def test_generate_ssh_button_opens_subdialog(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``Generate SSH Key…`` button on the clone dialog launches :class:`SshKeyDialog`."""
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    calls: list[bool] = []
    monkeypatch.setattr(
        "src.ui.dialogs.clone_dialog.SshKeyDialog.exec",
        lambda self: calls.append(True) or True,  # return truthy
    )
    dialog._on_generate_ssh()  # noqa: SLF001
    assert calls
