"""Refreshes reuse snapshots instead of repeating worktree scans."""
from pathlib import Path
from threading import get_ident

from src.core.repository import RepositoryManager
from src.viewmodels.graph_viewmodel import GraphViewModel
from src.viewmodels.main_viewmodel import MainViewModel


def test_merge_estimation_does_not_generate_file_patches(qtbot, committed_repo, monkeypatch):
    class MetadataOnlyDiff:
        deltas = [object(), object()]

        def __iter__(self):
            raise AssertionError("Counting merge files must not materialize patches")

    monkeypatch.setattr(committed_repo.repo, "diff", lambda *args: MetadataOnlyDiff())
    vm = MainViewModel()
    vm.set_repository(committed_repo, refresh=False)
    try:
        assert vm._estimate_merge_size("main") == 2
    finally:
        vm.stop_worktree_refresh()
        vm.deleteLater()


def test_graph_uses_supplied_status_even_when_empty(committed_repo, monkeypatch):
    def unexpected():
        raise AssertionError("Graph must reuse the panel's status snapshot")

    monkeypatch.setattr(committed_repo, "get_raw_status", unexpected)
    rows, error = GraphViewModel._compute_graph(
        committed_repo, other_worktrees=[], raw_status={},
    )
    assert rows and error is None
    assert not any(row["sha"] == "WIP" for row in rows)


def test_background_load_reads_each_checkout_once(
    qtbot, committed_repo, linked_worktree, tmp_path, monkeypatch,
):
    for manager in (committed_repo, linked_worktree):
        (Path(manager.path) / "hello.txt").write_bytes(b"dirty\n")
    vm = MainViewModel(async_enabled=True, config_path=tmp_path / "settings.json")
    vm.set_repository(committed_repo, refresh=False)
    vm.stop_worktree_refresh()
    original = RepositoryManager.get_raw_status
    calls = []
    gui_thread = get_ident()
    errors = []
    vm.error_occurred.connect(errors.append)

    def read(manager):
        calls.append((Path(manager.repo.workdir).resolve(), get_ident()))
        return original(manager)

    monkeypatch.setattr(RepositoryManager, "get_raw_status", read)
    try:
        vm.load_repository_data()
        qtbot.waitUntil(lambda: not vm._active_workers, timeout=10000)
        assert errors == []
        assert len(calls) == 2
        assert {path for path, _ in calls} == {
            Path(committed_repo.path).resolve(), Path(linked_worktree.path).resolve(),
        }
        assert all(thread != gui_thread for _, thread in calls)
        assert vm.commit_panel_view_model().unstaged_paths() == ["hello.txt"]
        assert vm.graph_view_model().other_worktrees()[0].count == 1
    finally:
        vm.stop_worktree_refresh()
        qtbot.waitUntil(lambda: not vm._active_workers, timeout=10000)
        vm.deleteLater()
