"""Tests for :class:`src.viewmodels.repo_tabs_viewmodel.RepoTabViewModel`.

Covers the three mutating actions invoked by the repository tab bar's
right-click context menu — :meth:`remove_tab`, :meth:`close_others`,
:meth:`close_to_right` — plus the persistence round-trip.  The widget
tests in ``tests/ui/test_repo_bar_widget.py`` cover the wiring on top
of these primitives; here we pin the ViewModel contract.

Note on path normalisation
--------------------------
``RepoTabViewModel.add_tab`` normalises inputs through
``Path(path).resolve().as_posix()`` so the same logical repository
never lands twice. On Windows that turns ``"/repo/a"`` into
``"C:/repo/a"``. We feed absolute paths through ``tmp_path`` and
compare on resolved ``Path`` objects instead of literal strings to
stay portable.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication
from src.viewmodels.repo_tabs_viewmodel import RepoTabViewModel


def _ensure_app() -> None:
    QApplication.instance() or QApplication([])


def _add(vm: RepoTabViewModel, base: Path, name: str) -> str:
    """Create ``base / name`` as a directory, then register it as a tab."""
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    vm.add_tab(str(path))
    return str(path.resolve())


def _tabs_as_set(vm: RepoTabViewModel) -> set[Path]:
    return {Path(p).resolve() for p in vm.tabs}


# ----- remove_tab (existing contract, kept here too) --------------------


def test_remove_tab_drops_path_and_adjusts_active(tmp_path: Path) -> None:
    """Removing the active tab keeps another tab active."""
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    # Active is ``c`` (last add). Remove it → active falls back to ``b``.
    vm.remove_tab(2)
    assert _tabs_as_set(vm) == {(tmp_path / "a").resolve(), (tmp_path / "b").resolve()}
    assert vm.active_index == 1
    assert Path(vm.active_path).resolve() == (tmp_path / "b").resolve()


def test_remove_tab_before_active_keeps_index(tmp_path: Path) -> None:
    """Removing a tab before the active tab does not change the index."""
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    vm.set_active_tab(2)
    vm.remove_tab(0)
    # ``c`` was at index 2 → now at index 1, still active.
    assert vm.active_index == 1
    assert _tabs_as_set(vm) == {(tmp_path / "b").resolve(), (tmp_path / "c").resolve()}


def test_remove_tab_out_of_range_is_noop(tmp_path: Path) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    tabs_before = _tabs_as_set(vm)
    active_before = vm.active_index
    vm.remove_tab(-1)
    vm.remove_tab(99)
    assert _tabs_as_set(vm) == tabs_before
    assert vm.active_index == active_before


def test_remove_tab_above_active_adjusts_active_index(tmp_path: Path) -> None:
    """H10 — removing a tab to the left of the active one shifts the index.

    With three tabs and the active tab at the rightmost position, dropping
    the leftmost tab must shift the active index down by one so it keeps
    pointing at the *same repository* rather than jumping onto the next
    neighbour.
    """
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    vm.set_active_tab(2)  # active = ``c`` (rightmost)
    vm.remove_tab(0)       # drop ``a`` to the left of active
    assert vm.active_index == 1
    assert Path(vm.active_path).resolve() == (tmp_path / "c").resolve()


def test_remove_tab_to_left_of_middle_active_decrements(tmp_path: Path) -> None:
    """H10 — corner case where ``len(self._tabs)-1`` masking would hide the bug.

    With three tabs and the active one in the middle, removing the leftmost
    tab must decrement the active index by one. The previous
    implementation only adjusted when ``active_index >= len(tabs)`` after
    pop, which left the active tab pointing at the *neighbouring* repo
    rather than the one the user originally selected.
    """
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    vm.set_active_tab(1)  # active = ``b`` (middle)
    vm.remove_tab(0)       # drop ``a`` to the left; ``b`` slides to index 0
    assert vm.active_index == 0
    assert Path(vm.active_path).resolve() == (tmp_path / "b").resolve()


# ----- close_others -----------------------------------------------------


def test_close_others_keeps_only_clicked_tab(tmp_path: Path) -> None:
    """``close_others`` collapses the tab list to the one tab at *index*.

    The remaining tab becomes active. Order inside the list collapses
    to a single element so the persistence ``save_to_state`` round-trip
    records exactly one repository.
    """
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    vm.close_others(1)
    assert _tabs_as_set(vm) == {(tmp_path / "b").resolve()}
    assert vm.active_index == 0
    assert Path(vm.active_path).resolve() == (tmp_path / "b").resolve()


def test_close_others_with_single_tab_is_noop(tmp_path: Path) -> None:
    """A single tab cannot have "others" — the action must be a no-op."""
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "only")
    vm.close_others(0)
    assert _tabs_as_set(vm) == {(tmp_path / "only").resolve()}
    assert vm.active_index == 0


def test_close_others_out_of_range_is_noop(tmp_path: Path) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    before = _tabs_as_set(vm)
    vm.close_others(-1)
    vm.close_others(99)
    assert _tabs_as_set(vm) == before


def test_close_others_emits_change_notifications(tmp_path: Path) -> None:
    """``close_others`` emits ``tabs_changed`` and ``active_tab_changed``."""
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    tab_calls: list[list[str]] = []
    active_calls: list[int] = []
    vm.tabs_changed.connect(lambda paths: tab_calls.append(list(paths)))
    vm.active_tab_changed.connect(lambda i: active_calls.append(i))
    vm.close_others(2)
    assert len(tab_calls) == 1
    assert {Path(p).resolve() for p in tab_calls[0]} == {(tmp_path / "c").resolve()}
    assert active_calls == [0]


# ----- close_to_right ----------------------------------------------------


def test_close_to_right_keeps_tabs_up_to_and_including_index(tmp_path: Path) -> None:
    """``close_to_right`` truncates the list after *index*.

    Active tab stays on whatever it was; if it lands beyond the new
    end it falls back to the new tail.
    """
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    _add(vm, tmp_path, "d")
    vm.set_active_tab(3)  # active = ``d`` (will get truncated)
    vm.close_to_right(1)
    assert _tabs_as_set(vm) == {(tmp_path / "a").resolve(), (tmp_path / "b").resolve()}
    # Active was ``d`` (gone) — should fall back to the new tail = ``b``.
    assert vm.active_index == 1
    assert Path(vm.active_path).resolve() == (tmp_path / "b").resolve()


def test_close_to_right_with_active_inside_keeps_index(tmp_path: Path) -> None:
    """If the active tab survives the truncation, its index is unchanged."""
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    vm.set_active_tab(0)  # active = ``a`` (survives)
    vm.close_to_right(1)
    assert _tabs_as_set(vm) == {(tmp_path / "a").resolve(), (tmp_path / "b").resolve()}
    assert vm.active_index == 0
    assert Path(vm.active_path).resolve() == (tmp_path / "a").resolve()


def test_close_to_right_on_rightmost_tab_is_noop(tmp_path: Path) -> None:
    """Nothing to the right of the rightmost tab — action must do nothing."""
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    before = _tabs_as_set(vm)
    active_before = vm.active_index
    vm.close_to_right(1)
    assert _tabs_as_set(vm) == before
    assert vm.active_index == active_before


def test_close_to_right_out_of_range_is_noop(tmp_path: Path) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    before = _tabs_as_set(vm)
    vm.close_to_right(-1)
    vm.close_to_right(99)
    assert _tabs_as_set(vm) == before


def test_close_to_right_emits_tabs_changed(tmp_path: Path) -> None:
    _ensure_app()
    vm = RepoTabViewModel()
    _add(vm, tmp_path, "a")
    _add(vm, tmp_path, "b")
    _add(vm, tmp_path, "c")
    captured: list[list[str]] = []
    vm.tabs_changed.connect(lambda paths: captured.append(list(paths)))
    vm.close_to_right(0)
    assert len(captured) == 1
    assert {Path(p).resolve() for p in captured[0]} == {(tmp_path / "a").resolve()}
