"""An idle client must let another Git process maintain the same repository."""
import subprocess
from pathlib import Path

import pytest
from src.viewmodels.main_viewmodel import MainViewModel


def _rename_pack_directory(path):
    pack = Path(path) / ".git" / "objects" / "pack"
    moved = pack.with_name("pack-probe")
    pack.rename(moved)
    moved.rename(pack)


@pytest.mark.parametrize("async_enabled", [False, True])
def test_idle_vm_allows_external_repack_and_remains_usable(
    qtbot, packed_repo, tmp_path, async_enabled,
):
    vm = MainViewModel(async_enabled=async_enabled, config_path=tmp_path / "config.json")
    vm.set_repository(packed_repo, refresh=not async_enabled)
    if async_enabled:
        vm.load_repository_data()
        qtbot.waitUntil(lambda: not vm.is_busy(), timeout=15000)
    vm.stop_worktree_refresh()
    path = packed_repo.path
    old_packs = set((Path(path) / ".git" / "objects" / "pack").glob("*.pack"))
    # Read on the GUI handle, then let the real event dispatcher become idle.
    assert packed_repo.get_all_history()
    qtbot.wait(10)
    _rename_pack_directory(path)
    (Path(path) / "external.txt").write_text("external change\n")
    commands = [
        ["add", "external.txt"],
        ["-c", "user.name=External", "-c", "user.email=external@example.com",
         "commit", "-m", "external commit"],
        ["repack", "-ad"],
        ["fsck", "--full"],
    ]
    for args in commands:
        subprocess.run(
            ["git", "-C", path, *args], check=True, capture_output=True, timeout=15,
        )
    assert all(not pack.exists() for pack in old_packs)
    assert packed_repo.head_commit.message.strip() == "external commit"
    vm.refresh_state()
    qtbot.waitUntil(lambda: not vm.is_busy(), timeout=15000)
    qtbot.wait(10)
    _rename_pack_directory(path)
    vm.close_repository()
    vm.deleteLater()


def test_switch_releases_old_handle_without_waiting_for_gc(qtbot, packed_repo, tmp_path):
    vm = MainViewModel(async_enabled=False, config_path=tmp_path / "config.json")
    vm.set_repository(packed_repo)
    assert packed_repo.get_all_history()
    vm.close_repository()
    _rename_pack_directory(packed_repo.path)
    vm.deleteLater()
