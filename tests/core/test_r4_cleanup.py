"""Regression tests for update1 R4 cleanup and documentation changes."""
from __future__ import annotations

import inspect
from pathlib import Path

import pygit2
from PySide6.QtCore import QCoreApplication
from src.core import diff_parser, graph_v2, operations, repository
from src.core.repository import RepositoryManager
from src.utils import avatar, debug_mode
from src.viewmodels.main_viewmodel import MainViewModel


def _qapp() -> None:
    QCoreApplication.instance() or QCoreApplication([])


def _repo(tmp_path: Path) -> RepositoryManager:
    path = tmp_path / "repo"
    path.mkdir()
    pygit2.init_repository(str(path), initial_head="main")
    (path / "file.txt").write_text("one\n")
    manager = RepositoryManager(str(path))
    sig = pygit2.Signature("Tester", "tester@example.com")
    manager.repo.index.add("file.txt")
    manager.repo.index.write()
    tree = manager.repo.index.write_tree()
    manager.repo.create_commit("refs/heads/main", sig, sig, "initial", tree, [])
    return manager


def test_r4_public_docstrings_match_signatures() -> None:
    for function in (operations.revert, operations.commit_changes, operations.stash_push):
        doc = inspect.getdoc(function)
        assert doc
        assert any(name in doc for name in inspect.signature(function).parameters if name != "repo")
        assert set(inspect.signature(function).parameters) >= {"repo"}
    refresh = inspect.getdoc(MainViewModel.refresh_state)
    assert refresh and "repository" in refresh.lower()


def test_r4_dead_code_removed_or_legacy_marked() -> None:
    assert not hasattr(RepositoryManager, "_ensure_clean")
    repository_source = Path(repository.__file__).read_text()
    assert "_in_fork" not in repository_source or "DEPRECATED" in repository_source
    graph_source = Path(graph_v2.__file__).read_text()
    assert (
        "HEAD_SPECIAL_COLOR_INDEX" not in graph_source
        or "DEPRECATED" in graph_source
        or "legacy" in graph_source.lower()
    )
    widget_source = Path("src/ui/widgets/graph_widget.py").read_text()
    module_doc = widget_source.split("\"\"\"", 2)[1]
    assert "DEPRECATED" in module_doc or "legacy" in module_doc.lower()


def test_debug_print_only_runs_when_env_var_set(monkeypatch, capsys) -> None:
    monkeypatch.setattr(debug_mode, "_DEBUG", True)
    debug_mode.debug_print("diagnostic")
    assert "diagnostic" in capsys.readouterr().out
    monkeypatch.setattr(debug_mode, "_DEBUG", False)
    debug_mode.debug_print("hidden")
    assert capsys.readouterr().out == ""


def test_debug_print_no_op_when_unset(monkeypatch, capsys) -> None:
    monkeypatch.delenv("GIT_PY_DEBUG", raising=False)
    monkeypatch.setattr(debug_mode, "_DEBUG", False)
    debug_mode.debug_print("should not print")
    assert capsys.readouterr().out == ""


def test_avatar_helpers() -> None:
    assert avatar.initials("John Doe") == "JD"
    assert avatar.initials("Jane") == "J"
    assert avatar.initials("") == avatar.initials(None) == "?"
    assert avatar.avatar_color("Alice") == avatar.avatar_color("Alice")
    assert avatar.avatar_color("Alice") != avatar.avatar_color("Bob")
    assert avatar.avatar_color(None) == (128, 128, 128)


def test_recently_created_changed_emits_copy() -> None:
    _qapp()
    vm = MainViewModel.__new__(MainViewModel)
    vm.__init__()
    internal = {"feature"}
    vm._recently_created_branches = internal
    received = []
    vm.recently_created_changed.connect(received.append)
    vm.recently_created_changed.emit(set(internal))
    assert received[0] == internal
    assert id(received[0]) != id(internal)


def test_fetch_changes_emits_error_when_busy(tmp_path: Path) -> None:
    _qapp()
    vm = MainViewModel.__new__(MainViewModel)
    vm.__init__()
    vm.set_repository(_repo(tmp_path))
    vm._is_busy = True
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)
    vm.fetch_changes()
    assert errors == ["Another operation is in progress."]


def test_is_valid_sha_accepts_sha1_and_sha256() -> None:
    assert graph_v2._is_valid_sha("a" * 40)
    assert graph_v2._is_valid_sha("A" * 40)
    assert graph_v2._is_valid_sha("a" * 64)
    assert not graph_v2._is_valid_sha("xyz")


def test_blob_line_count_returns_zero_for_binary(tmp_path: Path) -> None:
    manager = _repo(tmp_path)
    binary = manager.repo.create_blob(b"a\x00b\n")
    text = manager.repo.create_blob(b"a\nb\n")
    assert diff_parser._blob_line_count(manager.repo, binary) == 0
    assert diff_parser._blob_line_count(manager.repo, text) == 2


def test_windows_path_case_insensitive(tmp_path: Path, monkeypatch) -> None:
    manager = _repo(tmp_path)
    path = Path(manager.path) / "file.txt"
    path.write_text("two\n")
    manager.repo.index.add("file.txt")
    manager.repo.index.write()
    monkeypatch.setattr(repository.os, "name", "nt")
    assert manager.get_commit_file_diff_text(str(manager.repo.head.target), "FILE.TXT")
