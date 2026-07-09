"""Tests for the repository-tab-bar helpers on :class:`MainViewModel`.

Both :meth:`show_repo_in_folder` and :meth:`copy_repo_path` are
invoked by the right-click context menu on a repo tab. The helpers
are pure UI plumbing — they neither touch Git state nor carry
undo semantics — so the tests pin only the observable contract:

* ``show_repo_in_folder`` opens Explorer at *path*, but stays silent
  on missing paths (a tab may briefly reference a stale path during
  config restore — silently no-op'ing matches the existing
  ``show_in_folder`` helper for files).
* ``copy_repo_path`` writes *path* to the system clipboard through
  :meth:`MainViewModel.copy_to_clipboard`. Empty payloads are
  ignored.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication
from src.viewmodels.main_viewmodel import MainViewModel


def _ensure_app() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _clipboard_text() -> str:
    return QApplication.clipboard().text()


# ----- show_repo_in_folder ----------------------------------------------


def test_show_repo_in_folder_opens_explorer(
    qtbot, tmp_path: Path, monkeypatch: object,
) -> None:
    """Show repo folder spawns Explorer with the normalised path."""
    _ensure_app()
    repo = tmp_path / "myrepo"
    repo.mkdir()
    popen = MagicMock()
    monkeypatch_subprocess_popen(monkeypatch, popen)  # type: ignore[arg-type]

    vm = MainViewModel()
    vm.show_repo_in_folder(str(repo))

    popen.assert_called_once()
    args = popen.call_args.args[0]
    assert args[0] == "explorer"
    # Path is passed through ``os.path.normpath`` — accept whatever the
    # platform produces for a normalised, existing directory.
    assert Path(args[1]).resolve() == repo.resolve()


def test_show_repo_in_folder_ignores_missing_path(
    qtbot, tmp_path: Path, monkeypatch: object,
) -> None:
    """A stale tab path must not spawn Explorer for a non-existent dir."""
    _ensure_app()
    popen = MagicMock()
    monkeypatch_subprocess_popen(monkeypatch, popen)  # type: ignore[arg-type]

    vm = MainViewModel()
    vm.show_repo_in_folder(str(tmp_path / "does_not_exist"))

    popen.assert_not_called()


def test_show_repo_in_folder_empty_string_is_noop(
    qtbot, monkeypatch: object,
) -> None:
    _ensure_app()
    popen = MagicMock()
    monkeypatch_subprocess_popen(monkeypatch, popen)  # type: ignore[arg-type]

    vm = MainViewModel()
    vm.show_repo_in_folder("")

    popen.assert_not_called()


def test_show_repo_in_folder_swallows_popen_failure(
    qtbot, tmp_path: Path, monkeypatch: object,
) -> None:
    """Spawn failures are silently ignored — no ``error_occurred`` popup.

    A failure inside ``subprocess.Popen`` is not actionable from the
    user's perspective and must not interrupt the click that
    triggered the menu action.
    """
    _ensure_app()
    repo = tmp_path / "myrepo"
    repo.mkdir()
    popen = MagicMock(side_effect=OSError("boom"))
    monkeypatch_subprocess_popen(monkeypatch, popen)  # type: ignore[arg-type]

    errors: list[str] = []
    vm = MainViewModel()
    vm.error_occurred.connect(errors.append)
    vm.show_repo_in_folder(str(repo))

    assert errors == []


# ----- copy_repo_path ---------------------------------------------------


def test_copy_repo_path_writes_to_clipboard(
    qtbot, tmp_path: Path,
) -> None:
    _ensure_app()
    repo = tmp_path / "myrepo"
    repo.mkdir()
    vm = MainViewModel()
    vm.copy_repo_path(str(repo))
    assert _clipboard_text() == str(repo)


def test_copy_repo_path_empty_string_leaves_clipboard(
    qtbot,
) -> None:
    """An empty payload must not silently clear the clipboard."""
    _ensure_app()
    QApplication.clipboard().setText("sentinel")
    vm = MainViewModel()
    vm.copy_repo_path("")
    assert _clipboard_text() == "sentinel"


def test_copy_repo_path_routes_through_copy_to_clipboard(
    qtbot, tmp_path: Path, monkeypatch: object,
) -> None:
    """The helper delegates to :meth:`copy_to_clipboard` so the same
    empty-QApplication guard applies."""
    _ensure_app()
    repo = tmp_path / "myrepo"
    repo.mkdir()
    captured: list[str] = []
    vm = MainViewModel()
    monkeypatch.setattr(vm, "copy_to_clipboard", lambda text: captured.append(text))
    vm.copy_repo_path(str(repo))
    assert captured == [str(repo)]


# ----- helpers ----------------------------------------------------------


def monkeypatch_subprocess_popen(
    monkeypatch: object, replacement: MagicMock,
) -> None:
    """Patch ``subprocess.Popen`` inside :mod:`sys.modules`.

    The VM does the import inline (``import subprocess as _sp``), so
    we patch the name on :mod:`subprocess` itself rather than the VM
    module — both lookups resolve to the same object.
    """
    monkeypatch.setattr(subprocess, "Popen", replacement)  # type: ignore[attr-defined]
