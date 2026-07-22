"""UI tests for :class:`RemoteManageDialog`.

Exercises the dialog under ``pytest-qt`` (``QT_QPA_PLATFORM=offscreen``
on headless CI). The dialog only needs in-memory data; we feed it
a list of :class:`RemoteInfo` and assert that buttons fire the
right signals.
"""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox
from src.core.models import RemoteInfo
from src.ui.dialogs.remote_manage_dialog import RemoteManageDialog

# ----- construction --------------------------------------------------------


def test_dialog_builds_empty(qtbot) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.windowTitle() == "Manage Remotes"
    # 3 columns: name, URL, fetch refspec.
    assert dialog._table.columnCount() == 3  # noqa: SLF001
    # Remove button is disabled with no selection.
    assert not dialog._remove_btn.isEnabled()  # noqa: SLF001


def test_dialog_populated(qtbot) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    dialog.set_remotes(
        [
            RemoteInfo(
                name="origin",
                url="https://example.com/origin.git",
                fetch_refspec="+refs/heads/*:refs/remotes/origin/*",
                push_refspec="",
            ),
            RemoteInfo(
                name="upstream",
                url="git@example.com:foo.git",
                fetch_refspec="",
                push_refspec="",
            ),
        ],
    )
    assert dialog._table.rowCount() == 2  # noqa: SLF001
    # Origin appears in column 0.
    assert dialog._table.item(0, 0).text() == "origin"  # noqa: SLF001
    assert dialog._table.item(1, 0).text() == "upstream"  # noqa: SLF001


def test_selected_remote(qtbot) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    dialog.set_remotes(
        [RemoteInfo(name="origin", url="x", fetch_refspec="", push_refspec="")],
    )
    # Nothing selected yet.
    assert dialog.selected_remote() is None
    # Select row 0 by clicking the name cell.
    dialog._table.item(0, 0).setSelected(True)  # noqa: SLF001
    dialog._table.setCurrentItem(dialog._table.item(0, 0))  # noqa: SLF001
    assert dialog.selected_remote() == "origin"
    assert dialog._remove_btn.isEnabled()  # noqa: SLF001


# ----- add flow ------------------------------------------------------------


def test_add_button_emits_signal(qtbot, monkeypatch: pytest.MonkeyPatch) -> None:
    """Add… prompts twice (name, URL) and emits ``add_requested(name, url)``."""
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    responses = iter([("origin", True), ("https://example.com/origin.git", True)])

    def _fake_get_text(*args, **kwargs):  # noqa: ANN001
        return next(responses)

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(_fake_get_text))
    captured: list[tuple[str, str]] = []
    dialog.add_requested.connect(lambda n, u: captured.append((n, u)))
    dialog._on_add()  # noqa: SLF001
    assert captured == [("origin", "https://example.com/origin.git")]


def test_add_button_cancelled_does_not_emit(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("origin", False)),
    )
    captured: list[tuple[str, str]] = []
    dialog.add_requested.connect(lambda n, u: captured.append((n, u)))
    dialog._on_add()  # noqa: SLF001
    assert captured == []


def test_add_button_empty_name_does_not_emit(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    monkeypatch.setattr(
        QInputDialog, "getText",
        staticmethod(lambda *args, **kwargs: ("   ", True)),
    )
    captured: list[tuple[str, str]] = []
    dialog.add_requested.connect(lambda n, u: captured.append((n, u)))
    dialog._on_add()  # noqa: SLF001
    assert captured == []


# ----- remove flow ---------------------------------------------------------


def test_remove_button_emits_signal(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    dialog.set_remotes(
        [RemoteInfo(name="origin", url="x", fetch_refspec="", push_refspec="")],
    )
    dialog._table.setCurrentItem(dialog._table.item(0, 0))  # noqa: SLF001
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.Yes),
    )
    captured: list[str] = []
    dialog.remove_requested.connect(captured.append)
    dialog._on_remove()  # noqa: SLF001
    assert captured == ["origin"]


def test_remove_button_cancelled_does_not_emit(
    qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    dialog.set_remotes(
        [RemoteInfo(name="origin", url="x", fetch_refspec="", push_refspec="")],
    )
    dialog._table.setCurrentItem(dialog._table.item(0, 0))  # noqa: SLF001
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No),
    )
    captured: list[str] = []
    dialog.remove_requested.connect(captured.append)
    dialog._on_remove()  # noqa: SLF001
    assert captured == []


def test_remove_without_selection_does_nothing(qtbot) -> None:
    dialog = RemoteManageDialog()
    qtbot.addWidget(dialog)
    dialog.set_remotes(
        [RemoteInfo(name="origin", url="x", fetch_refspec="", push_refspec="")],
    )
    captured: list[str] = []
    dialog.remove_requested.connect(captured.append)
    dialog._on_remove()  # noqa: SLF001
    assert captured == []
