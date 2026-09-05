"""UI tests for :class:`CloneDialog`.

Exercises the dialog under ``pytest-qt`` (``QT_QPA_PLATFORM=offscreen``
on headless CI). We drive the widget through its public methods
(:meth:`set_provider`, :meth:`set_url`, :meth:`set_local_path`)
rather than entering keystrokes into the line edits.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox
from src.ui.dialogs.clone_dialog import CloneDialog, SshKeyDialog

# ----- construction --------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_ssh_home(tmp_path, monkeypatch):
    """Never allow a key-generation test to write into the real user profile."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


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
    # Use a path that already ends with the repo name so update11's
    # auto-append logic doesn't change it.
    dialog.set_local_path("/tmp/clone-target/repo")
    dialog._on_accept()  # noqa: SLF001
    assert captured == [("https://example.com/repo.git", "/tmp/clone-target/repo")]


# ----- SSH key dialog (no real subprocess) --------------------------------


def test_ssh_dialog_with_empty_path_warns(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    dialog._path_edit.clear()
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

    emitted: list[tuple[str, str, str]] = []
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    # Signal is now 3-arg (priv, pub, contents); lambda absorbs them as one tuple.
    dialog.key_generated.connect(lambda *args: emitted.append(args))
    dialog._path_edit.setText(str(key_path))  # noqa: SLF001
    dialog._comment_edit.setText("tester@example.com")  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    assert "ssh-ed25519" in dialog._output.text()  # noqa: SLF001
    assert emitted, "key_generated signal was not emitted"
    priv, pub, contents = emitted[0]
    assert priv == str(key_path)
    assert pub == str(pub_path)
    assert "ssh-ed25519" in contents


# ----- prefill + default_path for SshKeyDialog -----------------------------


def test_ssh_dialog_prefills_default_path(qtbot) -> None:
    """Opening ``SshKeyDialog()`` without args prefills ``~/.ssh/git-py-ed25519``."""
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    text = dialog._path_edit.text()  # noqa: SLF001
    assert text != ""
    assert text.endswith("/.ssh/git-py-ed25519") or text.endswith(r"\.ssh\git-py-ed25519")
    assert dialog._path_edit.placeholderText() == text  # noqa: SLF001


def test_ssh_dialog_respects_explicit_default_path(qtbot, tmp_path) -> None:
    """``default_path`` constructor arg overrides the built-in default."""
    custom = tmp_path / "my-key"
    dialog = SshKeyDialog(default_path=str(custom))
    qtbot.addWidget(dialog)
    assert dialog._path_edit.text() == str(custom)  # noqa: SLF001


def test_ssh_dialog_show_event_does_not_block_tests(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``showEvent`` calls git config user.email only when dialog is shown.

    Without a show, the constructor must NOT invoke subprocess, so test
    runners using monkeypatch on subprocess.run never deadlock.
    """
    from src.ui.dialogs import clone_dialog

    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ANN001
        calls.append(list(args))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(clone_dialog, "subprocess", type("M", (), {"run": staticmethod(fake_run)}))

    # Constructor only — must not call subprocess.run
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    assert calls == [], "subprocess.run was called during __init__, would deadlock tests"
    # After show(), it must be called once for 'git config user.email'
    dialog.show()
    qtbot.waitExposed(dialog)
    assert any("user.email" in a for a in calls), calls


# ----- auto-create ~/.ssh directory before ssh-keygen (update6) ----------


def test_ssh_dialog_creates_missing_parent_directory(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing parent dir is created automatically before ssh-keygen runs.

    Reproduces the Windows bug: user has no ``~/.ssh`` folder; prefill
    selects ``~/.ssh/git-py-ed25519``; without mkdir, ssh-keygen fails
    with ``Saving key "..." failed: No such file or directory``.
    """
    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")

    nested = tmp_path / "no" / "sub" / "id_test"
    pub_path = tmp_path / "no" / "sub" / "id_test.pub"
    assert not nested.parent.exists()  # precondition

    def fake_run(args, **kwargs):  # noqa: ANN001
        # Verify parent now exists before ssh-keygen would be called.
        assert nested.parent.exists(), "parent dir was not created before ssh-keygen"
        nested.write_text("PRIVATE\n")
        pub_path.write_text("ssh-ed25519 AAAA comment\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    dialog._path_edit.setText(str(nested))  # noqa: SLF001
    dialog._comment_edit.setText("")  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    assert nested.exists()
    assert pub_path.exists()


def test_ssh_dialog_keeps_conflicting_ssh_file(qtbot, tmp_path, monkeypatch):
    """A .ssh file is preserved and must not redirect keys into .ssh-py or temp."""
    from src.ui.dialogs import clone_dialog

    conflicting = tmp_path / ".ssh"
    conflicting.write_text("keep this file", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")
    warnings = []
    calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    monkeypatch.setattr(clone_dialog.subprocess, "run", lambda *a, **k: calls.append(a))
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    emitted = []
    dialog.key_generated.connect(lambda *args: emitted.append(args))

    dialog._on_generate()

    assert warnings and str(conflicting) in warnings[0]
    assert conflicting.read_text(encoding="utf-8") == "keep this file"
    assert not (tmp_path / ".ssh-py").exists()
    assert not calls and not emitted
    assert Path(dialog._path_edit.text()).parent == conflicting


def test_ssh_dialog_reports_unwritable_directory(qtbot, tmp_path, monkeypatch):
    """Permission errors stop generation at the chosen path."""
    from src.ui.dialogs import clone_dialog

    requested = tmp_path / ".ssh" / "git-py-ed25519"
    dialog = SshKeyDialog(default_path=requested)
    qtbot.addWidget(dialog)
    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")
    warnings = []
    calls = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    monkeypatch.setattr(clone_dialog.subprocess, "run", lambda *a, **k: calls.append(a))
    original_mkdir = Path.mkdir

    def denied(path, *args, **kwargs):
        if path == requested.parent:
            raise PermissionError("permission denied")
        return original_mkdir(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "mkdir", denied)
        dialog._on_generate()

    assert warnings and "permission denied" in warnings[0]
    assert not calls
    assert not requested.exists()
    assert Path(dialog._path_edit.text()) == requested


def test_ssh_dialog_preserves_existing_public_key(qtbot, tmp_path, monkeypatch):
    key = tmp_path / "id_test"
    public = tmp_path / "id_test.pub"
    public.write_text("existing public key", encoding="utf-8")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: warnings.append(a[2]))
    dialog = SshKeyDialog(default_path=key)
    qtbot.addWidget(dialog)

    dialog._on_generate()

    assert warnings
    assert not key.exists()
    assert public.read_text(encoding="utf-8") == "existing public key"


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


# ----- update11: clone target path resolution --------------------------------


def test_clone_dialog_appends_repo_name_when_path_does_not_exist(
    qtbot, tmp_path,
) -> None:
    """Non-existent destination path → treated as parent, repo name appended."""
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    parent = tmp_path / "work" / "git"  # does not exist
    assert not parent.exists()

    resolved = dialog._resolve_clone_target(  # noqa: SLF001
        "git@github.com:Aleks-Che/git-py.git", str(parent),
    )
    assert resolved == str(parent / "git-py")


def test_clone_dialog_appends_repo_name_to_existing_path(
    qtbot, tmp_path,
) -> None:
    """Even an already-existing destination path gets the repo name appended.

    Matches the behaviour of GitHub Desktop / Sourcetree / GitKraken:
    the user picks a **parent** folder and the repo is created inside it
    as a child. Whether or not the parent exists already is irrelevant.
    """
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    existing = tmp_path / "my-repos"
    existing.mkdir()  # user already created this empty dir

    resolved = dialog._resolve_clone_target(  # noqa: SLF001
        "git@github.com:Aleks-Che/git-py.git", str(existing),
    )
    assert resolved == str(existing / "git-py"), (
        f"expected repo name to be appended even when path exists, "
        f"got: {resolved}"
    )


def test_clone_dialog_does_not_duplicate_when_path_already_has_name(
    qtbot, tmp_path,
) -> None:
    """Path ending with repo name → no duplication."""
    dialog = CloneDialog()
    qtbot.addWidget(dialog)
    target = tmp_path / "work" / "git-py"  # already ends with repo name
    assert not target.exists()

    resolved = dialog._resolve_clone_target(  # noqa: SLF001
        "git@github.com:Aleks-Che/git-py.git", str(target),
    )
    assert resolved == str(target), (
        f"should not append repo name again, got: {resolved}"
    )
