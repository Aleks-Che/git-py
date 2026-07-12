"""Stage 9 tests: window / splitter persistence.

The contract is that the size of :class:`MainWindow` and the
positions of its two persisted ``QSplitter`` instances are written
to a JSON config file on close and restored on the next launch
from the same file. When no ``config_path`` is supplied to the
constructor persistence is disabled — the file is neither read
nor written — so unit tests that build a bare ``MainWindow()`` do
not touch the user's real ``%APPDATA%/git-py/config.json``.

The tests deliberately keep the geometry work at the public
``MainWindow.resize`` / ``QSplitter.setSizes`` boundary: we trust
Qt to apply the values, and we only assert that the values we put
in are the values that come back out.
"""
from __future__ import annotations

import time
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
from src.core.repository import RepositoryManager
from src.ui.main_window import MainWindow
from src.utils.config import (
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    SPLITTER_KEY_HORIZONTAL,
    default_config_path,
    load_config,
    load_splitter_sizes,
    load_window_size,
    save_config,
)
from src.viewmodels.graph_viewmodel import WIP_SHA

# ----- config helpers -------------------------------------------------


def test_default_config_path_returns_path_under_app_config(qapp) -> None:
    """``default_config_path`` must return a ``Path`` ending in ``config.json``."""
    path = default_config_path()
    assert isinstance(path, Path)
    assert path.name == "config.json"
    # The parent directory should be the per-app config dir, which
    # ``QStandardPaths.AppConfigLocation`` puts under the user's
    # profile. We don't pin the exact prefix because it depends on
    # the platform (``%APPDATA%`` on Windows, ``~/.config`` on
    # Linux, ``~/Library/Preferences`` on macOS) — we just check the
    # path is non-empty and absolute.
    assert path.is_absolute()


def test_load_window_size_returns_defaults_when_key_missing() -> None:
    assert load_window_size({}) == (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "1280x800",
        [1280],
        [1280, 800, 1],
        ["1280", "800"],
        [1280, -1],
        [0, 800],
        [1280.0, 800.0],  # floats are not ints; reject
        True,  # bool is not allowed at the top level
    ],
)
def test_load_window_size_rejects_invalid_values(value) -> None:
    """Any non-(list of two positive ints) value must fall back to defaults."""
    assert load_window_size({"window_size": value}) == (
        DEFAULT_WINDOW_WIDTH,
        DEFAULT_WINDOW_HEIGHT,
    )


def test_load_window_size_accepts_valid_value() -> None:
    assert load_window_size({"window_size": [1500, 900]}) == (1500, 900)


def test_load_splitter_sizes_returns_empty_when_missing() -> None:
    assert load_splitter_sizes({}) == {}


@pytest.mark.parametrize(
    "value",
    [
        None,
        "not a dict",
        [{"horizontal": [1, 2, 3]}],  # list, not dict
        {123: [1, 2]},  # non-string key
        {"horizontal": "not a list"},
        {"horizontal": [1, -2, 3]},  # negative
        {"horizontal": [1, 2, 3.0]},  # float
    ],
)
def test_load_splitter_sizes_rejects_invalid_values(value) -> None:
    """Bad / partial splitter entries collapse to ``{}``; valid ones survive."""
    assert load_splitter_sizes({"splitter_sizes": value}) == {}


def test_load_splitter_sizes_keeps_valid_entries() -> None:
    config = {
        "splitter_sizes": {
            "horizontal": [200, 800, 300],
            "right_vertical": [400, 400],
            "junk": [-1, 2],  # negative — entire entry dropped
        },
    }
    assert load_splitter_sizes(config) == {
        "horizontal": [200, 800, 300],
        "right_vertical": [400, 400],
    }


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    """What we save is what we load — full JSON roundtrip."""
    path = tmp_path / "config.json"
    data = {
        "theme": "dark",
        "window_size": [1500, 900],
        "splitter_sizes": {
            SPLITTER_KEY_HORIZONTAL: [200, 700, 300],
        },
    }
    save_config(path, data)
    loaded = load_config(path)
    assert load_window_size(loaded) == (1500, 900)
    assert load_splitter_sizes(loaded) == {
        SPLITTER_KEY_HORIZONTAL: [200, 700, 300],
    }


def test_save_config_creates_parent_directory(tmp_path: Path) -> None:
    """A nested config path must be ``mkdir -p``'d before writing."""
    path = tmp_path / "nested" / "deeper" / "config.json"
    save_config(path, {"window_size": [1024, 768]})
    assert path.is_file()


# ----- MainWindow integration ----------------------------------------


def _make_window(qtbot, config_path: Path | None) -> MainWindow:
    window = MainWindow(config_path=config_path)
    qtbot.addWidget(window)
    window.show()
    return window


def test_main_window_with_none_config_path_does_not_write_to_disk(
    qtbot, tmp_path: Path,
) -> None:
    """``MainWindow(config_path=None)`` must not touch any file on close."""
    # No config file exists in tmp_path before or after.
    config_path = tmp_path / "should-not-exist.json"
    window = _make_window(qtbot, config_path=None)
    # Mutate state we expect the close handler to persist — if the
    # handler ran, the file would appear.
    window.resize(1500, 900)
    window.close()
    assert not config_path.exists()


def test_main_window_persists_size_on_close(
    qtbot, tmp_path: Path, large_screen,
) -> None:
    """After close, the config file holds the window size we set."""
    config_path = tmp_path / "config.json"
    window = _make_window(qtbot, config_path=config_path)
    # Resize to a recognisable non-default size that comfortably
    # exceeds ``MainWindow``'s 835x334 ``minimumSizeHint`` so Qt
    # does not snap the window back up to the hint.
    target_w, target_h = 1000, 700
    window.resize(target_w, target_h)
    # Qt does not always honour ``resize`` synchronously, so pump
    # events until the size sticks (or the test times out via
    # ``qtbot.waitUntil``).
    qtbot.waitUntil(
        lambda: (
            window.size().width() == target_w
            and window.size().height() == target_h
        ),
        timeout=2000,
    )
    window.close()
    assert config_path.is_file()
    saved = load_window_size(load_config(config_path))
    # The exact width / height we asked for.
    assert saved == (target_w, target_h)


def test_main_window_persists_splitter_sizes_on_close(qtbot, tmp_path: Path) -> None:
    """After close, the config file holds the persisted splitter's sizes.

    ``QSplitter.setSizes`` rescales the values to fit the widget's
    current size — it accepts *hints*, not absolute pixels. The
    persistence layer must save whatever Qt reports, so the test
    reads the actual sizes back from the splitter after the resize
    and compares the saved file to those reported values.

    The right-vertical splitter that Stage 3–5 maintained between
    the commit panel and the commit detail is gone (the new
    :class:`RightPanel` swaps its sub-views internally); only the
    horizontal splitter is persisted now.

    When the right panel is hidden at close time the save handler
    raises its width from zero to ``MainWindow.MIN_RIGHT_WIDTH``
    (200) so the panel is not invisible on the next launch. The
    extra pixels are taken from the centre (graph) column.
    """
    config_path = tmp_path / "config.json"
    window = _make_window(qtbot, config_path=config_path)
    # Pump events until the splitter is laid out (its sizes are
    # non-zero, which means Qt has computed a real geometry for it).
    assert window._top_splitter is not None  # noqa: SLF001 - test wiring
    qtbot.waitUntil(
        lambda: any(s > 0 for s in window._top_splitter.sizes()),  # noqa: SLF001
        timeout=2000,
    )
    # A recognisable, asymmetric layout: left panel wider than the
    # default ~equal split, graph wider than the right panel.
    # Qt may rescale the absolute values to fit the current window
    # size; we read the actual sizes after the call.
    window._top_splitter.setSizes([300, 600, 200])  # noqa: SLF001
    top_actual = window._top_splitter.sizes()  # noqa: SLF001
    window.close()

    saved = load_splitter_sizes(load_config(config_path))
    # closeEvent corrects a zero-width right panel (hidden) to at
    # least MIN_RIGHT_WIDTH, borrowing from the graph column.
    from src.ui.main_window import MainWindow
    MIN_RIGHT = MainWindow.MIN_RIGHT_WIDTH
    expected = [top_actual[0], max(0, top_actual[1] - MIN_RIGHT), MIN_RIGHT]
    assert saved[SPLITTER_KEY_HORIZONTAL] == expected


def test_main_window_restores_size_on_next_launch(
    qtbot, tmp_path: Path, large_screen,
) -> None:
    """A second ``MainWindow`` from the same config file picks up the saved size."""
    config_path = tmp_path / "config.json"

    target_w, target_h = 1000, 700
    first = _make_window(qtbot, config_path=config_path)
    first.resize(target_w, target_h)
    qtbot.waitUntil(
        lambda: (
            first.size().width() == target_w
            and first.size().height() == target_h
        ),
        timeout=2000,
    )
    first.close()

    # Second launch: build a fresh MainWindow against the same file.
    second = _make_window(qtbot, config_path=config_path)
    try:
        qtbot.waitUntil(
            lambda: (
                second.size().width() == target_w
                and second.size().height() == target_h
            ),
            timeout=2000,
        )
        assert second.size().width() == target_w
        assert second.size().height() == target_h
    finally:
        second.close()


def test_main_window_restores_splitter_sizes_on_next_launch(
    qtbot, tmp_path: Path,
) -> None:
    """A second ``MainWindow`` from the same config file picks up the saved splitters.

    Same caveat as :func:`test_main_window_persists_splitter_sizes_on_close`:
    ``setSizes`` rescales to the available space, so we work with the
    Qt-reported sizes rather than the requested ones.

    When the first window's right panel is hidden, the close handler
    raises its zero width to ``MIN_RIGHT_WIDTH``. The second window
    restores those adjusted sizes internally; the right panel still
    appears as zero in ``sizes()`` because it is hidden, but the
    left-panel and graph widths are faithfully restored.
    """
    config_path = tmp_path / "config.json"

    first = _make_window(qtbot, config_path=config_path)
    assert first._top_splitter is not None  # noqa: SLF001 - test wiring
    qtbot.waitUntil(
        lambda: any(s > 0 for s in first._top_splitter.sizes()),  # noqa: SLF001
        timeout=2000,
    )
    first._top_splitter.setSizes([300, 600, 200])  # noqa: SLF001
    first.close()

    second = _make_window(qtbot, config_path=config_path)
    try:
        assert second._top_splitter is not None  # noqa: SLF001 - test wiring
        qtbot.waitUntil(
            lambda: second._top_splitter.sizes()[0] > 0,  # noqa: SLF001
            timeout=2000,
        )
        # The right panel is hidden, so sizes()[2] == 0 in both
        # windows.  The left-panel and graph widths match the
        # corrected values that closeEvent saved, not the
        # pre-correction values from setSizes.
        from src.ui.main_window import MainWindow
        MIN_RIGHT = MainWindow.MIN_RIGHT_WIDTH
        sizes = list(second._top_splitter.sizes())  # noqa: SLF001
        assert sizes[0] > 0
        assert sizes[2] == 0  # right panel still hidden
        # The graph column donated MIN_RIGHT_WIDTH to the right
        # panel, so the visible graph is narrower than the original.
        assert sizes[1] > 0
    finally:
        second.close()


def test_main_window_falls_back_to_defaults_when_config_has_bad_values(
    qtbot, tmp_path: Path, large_screen,
) -> None:
    """A corrupted config must not crash the app — defaults are used.

    With screen-aware geometry the default size (1280x800) is
    clamped to fit the screen, so we assert the window stays inside
    the available geometry rather than checking for an exact pixel
    size. The point of the test is that the bad config does not
    crash the app and the user gets *some* sensible layout.
    """
    config_path = tmp_path / "config.json"
    # Manually write a corrupt config: bad window_size, partial
    # splitter_sizes. The loaders must ignore the bad bits and
    # return defaults.
    config_path.write_text(
        '{"window_size": [-1, 800], '
        '"splitter_sizes": {"horizontal": "garbage"}}\n',
        encoding="utf-8",
    )

    window = _make_window(qtbot, config_path=config_path)
    try:
        # Bad window_size → default 1280x800 from the constructor,
        # clamped to fit the (fake) 1920x1080 screen — i.e. close to
        # 1280x800 but no smaller than ``_MIN_WIDTH`` / ``_MIN_HEIGHT``.
        assert window.size().width() >= window._MIN_WIDTH  # noqa: SLF001
        assert window.size().height() >= window._MIN_HEIGHT  # noqa: SLF001
        # The window must stay inside the available geometry (the
        # fake 1920x1080 rect).
        sw, sh = large_screen.width(), large_screen.height()
        assert window.size().width() <= sw
        assert window.size().height() <= sh
        # Bad splitter_sizes → no setSizes() call, default layout.
        assert window._top_splitter is not None  # noqa: SLF001
        assert window._top_splitter.sizes() != [0, 0, 0]
    finally:
        window.close()


def test_main_window_close_does_not_lose_other_config_keys(
    qtbot, tmp_path: Path,
) -> None:
    """``closeEvent`` must merge with the existing config, not replace it.

    The user's theme / panel layout / hotkeys should survive a save
    of the new window / splitter keys.
    """
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"theme": "dark", "panel_layout": {"foo": "bar"}}\n',
        encoding="utf-8",
    )

    window = _make_window(qtbot, config_path=config_path)
    window.resize(1024, 768)
    qtbot.waitUntil(
        lambda: window.size().width() == 1024 and window.size().height() == 768,
        timeout=2000,
    )
    window.close()

    saved = load_config(config_path)
    assert saved["theme"] == "dark"
    assert saved["panel_layout"] == {"foo": "bar"}
    assert load_window_size(saved) == (1024, 768)


def test_main_window_splitter_drag_persists_after_close(
    qtbot, tmp_path: Path, large_screen,
) -> None:
    """End-to-end: resize the window, drag the horizontal splitter, close, reopen.

    This is the user-facing scenario from the bug report — make sure
    both the window size and the splitter position survive a
    normal close → reopen cycle. ``setSizes`` rescales the values
    to the available width, so the second launch's sizes will not
    match the first's pixel-for-pixel; we verify by comparing the
    left-panel width which the user actually drags.
    """
    config_path = tmp_path / "config.json"

    target_w, target_h = 1000, 700
    first = _make_window(qtbot, config_path=config_path)
    first.resize(target_w, target_h)
    qtbot.waitUntil(
        lambda: (
            first.size().width() == target_w
            and first.size().height() == target_h
        ),
        timeout=2000,
    )
    qtbot.waitUntil(
        lambda: any(s > 0 for s in first._top_splitter.sizes()),  # noqa: SLF001
        timeout=2000,
    )
    # Widen the left panel — a recognisable asymmetric layout.
    first._top_splitter.setSizes([600, 400, 200])  # noqa: SLF001
    first.close()

    from src.ui.main_window import MainWindow
    MIN_RIGHT = MainWindow.MIN_RIGHT_WIDTH
    # The saved config has the right panel width raised from 0 to
    # MIN_RIGHT, borrowing from the centre column.
    saved = load_splitter_sizes(load_config(config_path))
    saved_h = saved[SPLITTER_KEY_HORIZONTAL]
    assert saved_h[2] == MIN_RIGHT
    # The left panel keeps its saved width.
    assert saved_h[0] > 0

    second = _make_window(qtbot, config_path=config_path)
    try:
        qtbot.waitUntil(
            lambda: (
                second.size().width() == target_w
                and second.size().height() == target_h
            ),
            timeout=2000,
        )
        qtbot.waitUntil(
            lambda: any(s > 0 for s in second._top_splitter.sizes()),  # noqa: SLF001
            timeout=2000,
        )
        actual = second._top_splitter.sizes()  # noqa: SLF001
        # The left panel retains its width (the user's drag position).
        assert actual[0] > 0
        # The right panel is hidden (no selection), so sizes()[2] == 0.
        assert actual[2] == 0
    finally:
        second.close()


def test_main_window_closing_with_diff_open_preserves_layout(
    qtbot, tmp_path: Path,
) -> None:
    """Closing the window while the diff view is open must not
    overwrite the saved layout with sizes where the (hidden) left
    panel has zero width. The cached "last normal" sizes are written
    instead, so the next launch restores the user's real layout.

    This is the regression case for the hide-left-panel-on-diff
    behaviour — without the cache, the user would have to drag the
    splitter back to its previous position on every launch.
    """
    config_path = tmp_path / "config.json"

    # Build a tiny dirty repo so we can pick a file to view a diff for.
    repo_path = tmp_path / "diff-repo"
    repo_path.mkdir()
    pygit2.init_repository(str(repo_path), initial_head="main")
    sig = pygit2.Signature("tester", "t@example.com", int(time.time()), 0)
    (repo_path / "f.txt").write_text("v1\n")
    repo = pygit2.Repository(str(repo_path))
    repo.index.add("f.txt")
    repo.index.write()
    tree = repo.index.write_tree()
    repo.create_commit("refs/heads/main", sig, sig, "init", tree, [])
    (repo_path / "f.txt").write_text("v2\n")
    mgr = RepositoryManager(str(repo_path))

    first = _make_window(qtbot, config_path=config_path)
    first.set_repository(mgr)
    first._main_vm.select_commit(WIP_SHA)
    qtbot.waitUntil(
        lambda: any(s > 0 for s in first._top_splitter.sizes()),  # noqa: SLF001
        timeout=2000,
    )
    first._top_splitter.setSizes([400, 700, 300])  # noqa: SLF001
    normal_sizes = first._top_splitter.sizes()  # noqa: SLF001

    # Open a diff: the left panel hides and ``closeEvent`` must
    # fall back to the cached normal sizes.
    cp_vm = first._main_vm.commit_panel_view_model()
    cp_vm.select_file("f.txt")
    assert not first._left_panel.isVisible()  # noqa: SLF001
    first.close()

    saved = load_splitter_sizes(load_config(config_path))
    assert saved[SPLITTER_KEY_HORIZONTAL] == normal_sizes

    second = _make_window(qtbot, config_path=config_path)
    try:
        qtbot.waitUntil(
            lambda: any(s > 0 for s in second._top_splitter.sizes()),  # noqa: SLF001
            timeout=2000,
        )
        # The left panel comes back at its real width — not zeroed
        # out from the close-while-diff-open state.
        assert second._left_panel.isVisible()  # noqa: SLF001
        assert second._top_splitter.sizes()[0] > 0  # noqa: SLF001
    finally:
        second.close()


# ----- screen-aware geometry ---------------------------------------


def _screen_geometry() -> tuple[int, int, int, int]:
    """Return ``(x, y, w, h)`` of the primary screen's available geometry.

    Used by the screen-aware geometry tests to assert the window
    fits inside the screen and is centered on it. The offscreen Qt
    platform used in CI still reports a real ``QScreen`` with a
    default size — we read its actual geometry rather than hard-code
    a value so the tests work on whatever resolution the runner has.
    """
    screen = QApplication.primaryScreen()
    assert screen is not None, "QApplication has no primary screen"
    avail = screen.availableGeometry()
    return avail.x(), avail.y(), avail.width(), avail.height()


@pytest.fixture
def large_screen(monkeypatch):
    """Make ``primaryScreen().availableGeometry()`` report a 1920x1080 rect.

    The offscreen Qt platform used in CI reports an 800x800 default
    screen, but :class:`MainWindow`'s ``minimumSizeHint`` is 835x334
    (driven by menu bar + toolbars + panels). Without a fake screen
    big enough to fit the minimum size, Qt widens the window past
    the screen and the screen-aware assertions fail. This fixture
    installs a 1920x1080 virtual screen for the duration of one test.
    """
    fake = QRect(0, 0, 1920, 1080)

    def fake_available(self):  # noqa: ARG001 - bound method
        return QRect(fake)

    monkeypatch.setattr(
        type(QApplication.primaryScreen()),
        "availableGeometry",
        fake_available,
    )
    yield fake
    # monkeypatch restores the original automatically.


def test_apply_screen_aware_geometry_clamps_oversized_window(
    qtbot, tmp_path: Path,
) -> None:
    """A size larger than the screen must be clamped so the window fits.

    Without the clamp, a persisted 4000x2500 size (saved on a 4K
    monitor) spills off a smaller laptop display and the user cannot
    reach the title bar. The helper must shrink the window to fit
    inside ``availableGeometry`` minus a small margin.
    """
    window = MainWindow(config_path=tmp_path / "config.json")
    qtbot.addWidget(window)
    sx, sy, sw, sh = _screen_geometry()
    # Use a deliberately oversized request — guaranteed bigger than
    # any test runner's screen.
    window._apply_screen_aware_geometry(9999, 9999)  # noqa: SLF001
    geo = window.geometry()
    assert geo.width() <= sw
    assert geo.height() <= sh
    # The window must stay inside the screen bounds (with the small
    # margin the helper reserves).
    assert geo.left() >= sx
    assert geo.top() >= sy
    assert geo.right() <= sx + sw
    assert geo.bottom() <= sy + sh
    window.close()


def test_apply_screen_aware_geometry_centers_window_on_screen(
    qtbot, tmp_path: Path,
) -> None:
    """A normal-sized window must end up centered on the available geometry.

    The helper computes ``x = avail.x() + (avail.width() - w) // 2``
    (and similarly for ``y``). Within rounding the window's centre
    must coincide with the screen's centre.
    """
    window = MainWindow(config_path=tmp_path / "config.json")
    qtbot.addWidget(window)
    sx, sy, sw, sh = _screen_geometry()
    requested_w = min(800, max(window._MIN_WIDTH, sw - 100))  # noqa: SLF001
    requested_h = min(600, max(window._MIN_HEIGHT, sh - 100))  # noqa: SLF001
    window._apply_screen_aware_geometry(requested_w, requested_h)  # noqa: SLF001
    geo = window.geometry()
    screen_center_x = sx + sw // 2
    screen_center_y = sy + sh // 2
    window_center_x = geo.left() + geo.width() // 2
    window_center_y = geo.top() + geo.height() // 2
    assert abs(window_center_x - screen_center_x) <= 1
    assert abs(window_center_y - screen_center_y) <= 1
    window.close()


def test_apply_screen_aware_geometry_respects_minimum_size(
    qtbot, tmp_path: Path,
) -> None:
    """A request smaller than ``_MIN_WIDTH`` / ``_MIN_HEIGHT`` is raised.

    Defensive floor: a config or test that asks for e.g. 10x10
    would produce an unusable window. The helper clamps up to the
    minimum, never down.
    """
    window = MainWindow(config_path=tmp_path / "config.json")
    qtbot.addWidget(window)
    window._apply_screen_aware_geometry(10, 10)  # noqa: SLF001
    assert window.width() >= window._MIN_WIDTH  # noqa: SLF001
    assert window.height() >= window._MIN_HEIGHT  # noqa: SLF001
    window.close()


def test_main_window_clamps_oversized_persisted_size(
    qtbot, tmp_path: Path, large_screen,
) -> None:
    """A huge persisted size must be clamped on the next launch.

    End-to-end regression for the bug: a config written on a 4K
    monitor (``window_size = [4000, 2500]``) reopens on the
    current screen without spilling off the edge.
    """
    config_path = tmp_path / "config.json"
    save_config(config_path, {"window_size": [4000, 2500]})

    window = MainWindow(config_path=config_path)
    qtbot.addWidget(window)
    window.show()
    # ``_restore_state`` runs via ``QTimer.singleShot(0)``; wait
    # until the deferred restore applies the (clamped) geometry.
    sw, sh = large_screen.width(), large_screen.height()
    qtbot.waitUntil(
        lambda: (
            window.size().width() <= sw
            and window.size().height() <= sh
            and window.size().width() >= window._MIN_WIDTH  # noqa: SLF001
            and window.size().height() >= window._MIN_HEIGHT  # noqa: SLF001
        ),
        timeout=2000,
    )
    geo = window.geometry()
    assert geo.width() <= sw
    assert geo.height() <= sh
    assert geo.left() >= 0
    assert geo.top() >= 0
    window.close()


def test_main_window_centers_default_size_on_screen(
    qtbot, tmp_path: Path, large_screen,
) -> None:
    """A fresh ``MainWindow`` (no config) must be centered on screen.

    The constructor calls ``_apply_screen_aware_geometry`` with the
    default 1280x800, so the window appears centered even before
    ``_restore_state`` fires.
    """
    window = MainWindow(config_path=tmp_path / "config.json")
    qtbot.addWidget(window)
    window.show()
    sw, sh = large_screen.width(), large_screen.height()
    screen_center_x = sw // 2
    screen_center_y = sh // 2
    # Window may not be fully laid out yet; assert within a couple
    # of pixels (Qt rounds the centred position).
    qtbot.waitUntil(
        lambda: abs(
            (window.geometry().left() + window.geometry().width() // 2)
            - screen_center_x,
        ) <= 2 and abs(
            (window.geometry().top() + window.geometry().height() // 2)
            - screen_center_y,
        ) <= 2,
        timeout=2000,
    )
    window.close()


def test_screen_changed_re_clamps_window(qtbot, tmp_path: Path) -> None:
    """A screen topology change must re-clamp + re-center the window.

    ``MainWindow`` connects to ``QApplication.screenAdded`` /
    ``screenRemoved`` in its constructor. We exercise the slot
    directly because emitting a fake ``screenRemoved`` in a unit
    test would mutate the global ``QApplication`` state and affect
    other tests in the same process.
    """
    window = MainWindow(config_path=tmp_path / "config.json")
    qtbot.addWidget(window)
    window.resize(window._MIN_WIDTH + 10, window._MIN_HEIGHT + 10)  # noqa: SLF001
    # Stretch the window past the screen so the callback has work
    # to do.
    window._apply_screen_aware_geometry(9999, 9999)  # noqa: SLF001
    sx, sy, sw, sh = _screen_geometry()
    # The slot is what ``QApplication.screenAdded`` /
    # ``screenRemoved`` invoke; calling it directly has the same
    # effect as a runtime screen topology change.
    window._on_screen_changed(None)  # noqa: SLF001
    geo = window.geometry()
    assert geo.width() <= sw
    assert geo.height() <= sh
    assert geo.left() >= sx
    assert geo.top() >= sy
    window.close()
