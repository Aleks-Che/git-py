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


def test_ssh_dialog_falls_back_to_tempdir_when_home_unwritable(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``Path.home()/.ssh`` cannot be created, fall back to ``tempdir/git-py-ssh``.

    We patch ``_ensure_parent_dir`` directly instead of ``Path.mkdir`` to
    avoid Qt/ctypes crashes when monkeypatching built-in types globally.
    """
    import tempfile

    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")

    fallback_root = tmp_path / "tempdir"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fallback_root))

    # Patch QMessageBox.information so it doesn't block on modal.
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(lambda *a, **k: 0),
    )

    pub_target = fallback_root / "git-py-ssh" / "id_test.pub"

    def fake_run(args, **kwargs):  # noqa: ANN001
        # Verify the resolved -f path now lives under fallback_root/git-py-ssh
        assert str(fallback_root / "git-py-ssh") in args[args.index("-f") + 1]
        # Simulate ssh-keygen creating the files.
        priv = Path(args[args.index("-f") + 1])
        priv.parent.mkdir(parents=True, exist_ok=True)
        priv.write_text("PRIVATE\n")
        priv.with_suffix(priv.suffix + ".pub").write_text("ssh-ed25519 AAAA\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)

    # Patch the instance method so primary path always "fails". This is
    # safer than monkeypatching the global Path class.
    def fake_ensure(path):  # noqa: ANN001
        new = fallback_root / "git-py-ssh" / path.name
        new.parent.mkdir(parents=True, exist_ok=True)
        return new, True

    monkeypatch.setattr(dialog, "_ensure_parent_dir", fake_ensure)
    dialog._path_edit.setText(str(tmp_path / "id_test"))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    assert pub_target.exists()


def test_ssh_dialog_aborts_when_no_directory_is_creatable(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If both primary AND tempdir are uncreatable, show warning and return None.

    Patch the instance method directly (not Path.mkdir globally) to avoid
    Qt/ctypes crashes on monkeypatching built-in types.
    """
    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")

    subprocess_calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ANN001
        subprocess_calls.append(list(args))
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)

    # Simulate _ensure_parent_dir fully failing AND showing the warning
    # the real implementation would show.
    warned: list[bool] = []

    def fake_warning(*a, **k):  # noqa: ANN001
        warned.append(True)
        return 0

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))

    def fake_ensure(path):  # noqa: ANN001
        QMessageBox.warning(
            None,
            "Generate SSH Key",
            "Cannot create directory for SSH key",
        )
        return None, False

    monkeypatch.setattr(dialog, "_ensure_parent_dir", fake_ensure)
    dialog._path_edit.setText(str(tmp_path / "id_test"))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    assert warned, "expected warning dialog"
    assert subprocess_calls == [], "ssh-keygen must not be invoked when no dir is creatable"


# ----- update7: handle parent-is-a-file conflict (e.g. .ssh is a file) -----


def test_ssh_dialog_detects_file_with_ssh_name(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``~/.ssh`` exists as a *file* (not dir), user gets a question dialog."""
    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")

    # Simulate the situation: parent of chosen path is a FILE, not a directory.
    fake_ssh_file = tmp_path / ".ssh"
    fake_ssh_file.write_text("not a directory\n")

    requested = fake_ssh_file / "git-py-ed25519"

    # Mock QMessageBox.question to capture the question text and choose Cancel
    # (so we don't recurse into _ensure_parent_dir).
    questions: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: (
                questions.append(a[2] if len(a) > 2 else k.get("text", "")),
                QMessageBox.StandardButton.Cancel,
            )[1],
        ),
    )

    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    dialog._path_edit.setText(str(requested))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    assert questions, "expected a question dialog for file-vs-dir conflict"
    question_text = questions[0]
    assert ".ssh" in question_text or str(fake_ssh_file) in question_text
    assert "already exists" in question_text or "file" in question_text.lower()


def test_ssh_dialog_offers_alternative_path_on_file_conflict(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User clicks 'Yes' on the question dialog -> path moves to sibling."""
    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")

    fake_ssh_file = tmp_path / ".ssh"
    fake_ssh_file.write_text("not a directory\n")
    requested = fake_ssh_file / "git-py-ed25519"

    # Choose 'Yes' for the question dialog.
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes,
        ),
    )

    captured_args: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ANN001
        captured_args.append(list(args))
        # ssh-keygen creates files at the resolved path.
        priv = Path(args[args.index("-f") + 1])
        priv.parent.mkdir(parents=True, exist_ok=True)
        priv.write_text("PRIV\n")
        priv.with_suffix(priv.suffix + ".pub").write_text("PUB\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)
    dialog._path_edit.setText(str(requested))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    # Sibling path was used: tmp_path/git-py-ed25519 (parent is tmp_path, a real dir).
    assert captured_args, "ssh-keygen was not called"
    resolved_path = Path(captured_args[0][captured_args[0].index("-f") + 1])
    assert resolved_path == tmp_path / "git-py-ed25519"
    assert (tmp_path / "git-py-ed25519").exists()
    assert (tmp_path / "git-py-ed25519.pub").exists()


def test_ssh_dialog_fallback_message_mentions_pub_file(
    qtbot, tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback 'information' dialog must mention the .pub file location."""
    import tempfile

    from src.ui.dialogs import clone_dialog

    monkeypatch.setattr(clone_dialog, "_find_ssh_keygen", lambda: "ssh-keygen")

    # Force mkdir to fail globally via instance-method patch (avoids Qt/ctypes crash).
    dialog = SshKeyDialog()
    qtbot.addWidget(dialog)

    def fake_ensure(path):  # noqa: ANN001
        return dialog._fallback_to_tempdir(
            path, OSError("simulated permission denied"),
        )

    monkeypatch.setattr(dialog, "_ensure_parent_dir", fake_ensure)

    info_messages: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        staticmethod(
            lambda *a, **k: (
                info_messages.append(a[2] if len(a) > 2 else k.get("text", "")),
                0,
            )[1],
        ),
    )

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    def fake_run(args, **kwargs):  # noqa: ANN001
        priv = Path(args[args.index("-f") + 1])
        priv.parent.mkdir(parents=True, exist_ok=True)
        priv.write_text("PRIV\n")
        priv.with_suffix(priv.suffix + ".pub").write_text("PUB\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    dialog._path_edit.setText(str(tmp_path / "no-dir" / "key"))  # noqa: SLF001
    dialog._on_generate()  # noqa: SLF001

    assert info_messages, "expected an information dialog"
    msg = info_messages[0]
    assert ".pub" in msg, f"message should mention .pub file location, got: {msg!r}"
    assert "Private" in msg or "private" in msg
    assert "Public" in msg or "public" in msg


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
