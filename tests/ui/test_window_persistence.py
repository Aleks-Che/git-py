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

from pathlib import Path

import pytest
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


def test_main_window_persists_size_on_close(qtbot, tmp_path: Path) -> None:
    """After close, the config file holds the window size we set."""
    config_path = tmp_path / "config.json"
    window = _make_window(qtbot, config_path=config_path)
    # Resize to a recognisable non-default size.
    window.resize(1500, 900)
    # Qt does not always honour ``resize`` synchronously, so pump
    # events until the size sticks (or the test times out via
    # ``qtbot.waitUntil``).
    qtbot.waitUntil(
        lambda: window.size().width() == 1500 and window.size().height() == 900,
        timeout=2000,
    )
    window.close()
    assert config_path.is_file()
    saved = load_window_size(load_config(config_path))
    # The exact width / height we asked for.
    assert saved == (1500, 900)


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
    assert saved[SPLITTER_KEY_HORIZONTAL] == top_actual


def test_main_window_restores_size_on_next_launch(qtbot, tmp_path: Path) -> None:
    """A second ``MainWindow`` from the same config file picks up the saved size."""
    config_path = tmp_path / "config.json"

    first = _make_window(qtbot, config_path=config_path)
    first.resize(1500, 900)
    qtbot.waitUntil(
        lambda: first.size().width() == 1500 and first.size().height() == 900,
        timeout=2000,
    )
    first.close()

    # Second launch: build a fresh MainWindow against the same file.
    second = _make_window(qtbot, config_path=config_path)
    try:
        qtbot.waitUntil(
            lambda: second.size().width() == 1500 and second.size().height() == 900,
            timeout=2000,
        )
        assert second.size().width() == 1500
        assert second.size().height() == 900
    finally:
        second.close()


def test_main_window_restores_splitter_sizes_on_next_launch(
    qtbot, tmp_path: Path,
) -> None:
    """A second ``MainWindow`` from the same config file picks up the saved splitters.

    Same caveat as :func:`test_main_window_persists_splitter_sizes_on_close`:
    ``setSizes`` rescales to the available space, so we work with the
    Qt-reported sizes rather than the requested ones.
    """
    config_path = tmp_path / "config.json"

    first = _make_window(qtbot, config_path=config_path)
    assert first._top_splitter is not None  # noqa: SLF001 - test wiring
    qtbot.waitUntil(
        lambda: any(s > 0 for s in first._top_splitter.sizes()),  # noqa: SLF001
        timeout=2000,
    )
    first._top_splitter.setSizes([300, 600, 200])  # noqa: SLF001
    expected_top = first._top_splitter.sizes()  # noqa: SLF001
    first.close()

    second = _make_window(qtbot, config_path=config_path)
    try:
        assert second._top_splitter is not None  # noqa: SLF001 - test wiring
        qtbot.waitUntil(
            lambda: second._top_splitter.sizes() == expected_top,  # noqa: SLF001
            timeout=2000,
        )
        assert second._top_splitter.sizes() == expected_top  # noqa: SLF001
    finally:
        second.close()


def test_main_window_falls_back_to_defaults_when_config_has_bad_values(
    qtbot, tmp_path: Path,
) -> None:
    """A corrupted config must not crash the app — defaults are used."""
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
        # Bad window_size → default size from the constructor (the
        # same value ``__init__`` would use without persistence).
        assert window.size().width() == 1280
        assert window.size().height() == 800
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
    qtbot, tmp_path: Path,
) -> None:
    """End-to-end: resize the window, drag the horizontal splitter, close, reopen.

    This is the user-facing scenario from the bug report — make sure
    both the window size and the splitter position survive a
    normal close → reopen cycle. ``setSizes`` rescales the values
    to the available width, so the second launch's sizes will not
    match the first's pixel-for-pixel; we compare *proportions*
    instead, which is what the user actually cares about.
    """
    config_path = tmp_path / "config.json"

    def _proportions(sizes: list[int]) -> list[float]:
        total = sum(sizes)
        if total <= 0:
            return [0.0, 0.0, 0.0]
        return [s / total for s in sizes]

    first = _make_window(qtbot, config_path=config_path)
    first.resize(1500, 900)
    qtbot.waitUntil(
        lambda: first.size().width() == 1500 and first.size().height() == 900,
        timeout=2000,
    )
    qtbot.waitUntil(
        lambda: any(s > 0 for s in first._top_splitter.sizes()),  # noqa: SLF001
        timeout=2000,
    )
    # Widen the left panel and shrink the right panel — a recognisable
    # asymmetric layout (left > graph > right).
    first._top_splitter.setSizes([600, 400, 200])  # noqa: SLF001
    expected = first._top_splitter.sizes()  # noqa: SLF001
    first.close()

    second = _make_window(qtbot, config_path=config_path)
    try:
        qtbot.waitUntil(
            lambda: second.size().width() == 1500 and second.size().height() == 900,
            timeout=2000,
        )
        # Wait until the splitter has been laid out, then compare
        # proportions rather than absolute pixel sizes (setSizes
        # rescales to whatever space is available, which can differ
        # by a few pixels between launches).
        qtbot.waitUntil(
            lambda: any(s > 0 for s in second._top_splitter.sizes()),  # noqa: SLF001
            timeout=2000,
        )
        actual = second._top_splitter.sizes()  # noqa: SLF001
        p_first = _proportions(expected)
        p_second = _proportions(actual)
        for p1, p2 in zip(p_first, p_second, strict=False):
            assert abs(p1 - p2) < 0.02, f"proportion drifted: {p_first} -> {p_second}"
        # The left panel keeps its saved width (stretch=0) so it is
        # narrower than the graph (which absorbs all the free space
        # when the right panel is hidden).  The right panel is 0
        # because it starts hidden.
        assert actual[2] == 0
        assert 0 < actual[0] < actual[1]
    finally:
        second.close()
