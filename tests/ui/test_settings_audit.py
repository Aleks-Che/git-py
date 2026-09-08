"""Settings audit regressions at the window and repository-switch boundaries."""

from unittest.mock import Mock

import pygit2
import pytest
from PySide6.QtWidgets import QMessageBox
from src.ui.dialogs.settings_dialog import SettingsDialog
from src.ui.main_window import MainWindow
from src.utils.config import (
    load_config,
    load_graph_column_widths,
    save_config,
)


@pytest.fixture
def window_factory(qtbot, monkeypatch):
    # Exercise real repository switching and signals; leave unrelated shell and
    # background history loading out of these layout persistence scenarios.
    monkeypatch.setattr("src.ui.widgets.terminal_widget.TerminalWidget.set_repo_path", Mock())
    monkeypatch.setattr("src.viewmodels.main_viewmodel.MainViewModel.load_repository_data", Mock())

    def make(config_path):
        window = MainWindow(config_path=config_path)
        qtbot.addWidget(window)
        window.show()
        qtbot.waitUntil(window.isVisible)
        # Process the constructor's deferred restore before any simulated input.
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        return window

    return make


def test_graph_widths_follow_tabs_and_survive_restart(window_factory, tmp_path):
    paths = [(tmp_path / name).as_posix() for name in ("first", "second", "third")]
    for path in paths:
        pygit2.init_repository(path, initial_head="main")
    config_path = tmp_path / "config.json"
    save_config(config_path, {
        "recent_repos": paths,
        "active_repo": paths[0],
        "graph_configs": {paths[0]: [260, 140, 100], paths[1]: [320, 180, 100]},
    })
    window = window_factory(config_path)
    graph = window._graph_table
    tabs = window.repo_tabs_view_model()
    assert graph.divider_positions() == [260, 400]
    graph.set_divider_positions([280, 440])
    tabs.set_active_tab(1)
    assert graph.divider_positions() == [320, 500]
    graph.set_divider_positions([340, 540])
    tabs.set_active_tab(2)
    assert graph.divider_positions() == [180, 500]
    tabs.set_active_tab(0)
    assert graph.divider_positions() == [280, 440]
    # An unrelated on-disk change must survive the layout save.
    latest = load_config(config_path)
    latest["author_name"] = "Changed while window was open"
    latest["splitter_sizes"]["future_panel"] = [100, 200]
    save_config(config_path, latest)
    assert window.close()
    saved = load_config(config_path)
    assert load_graph_column_widths(saved, paths[0]) == [280, 160, 100]
    assert load_graph_column_widths(saved, paths[1]) == [340, 200, 100]
    assert saved["author_name"] == "Changed while window was open"
    assert saved["splitter_sizes"]["future_panel"] == [100, 200]

    reopened = window_factory(config_path)
    assert reopened._graph_table.divider_positions() == [280, 440]
    reopened.repo_tabs_view_model().set_active_tab(1)
    assert reopened._graph_table.divider_positions() == [340, 540]
    reopened.close()


def test_failed_repository_switch_does_not_relabel_layout(window_factory, tmp_path):
    first = (tmp_path / "first").as_posix()
    missing = (tmp_path / "missing").as_posix()
    pygit2.init_repository(first, initial_head="main")
    config_path = tmp_path / "config.json"
    save_config(config_path, {
        "recent_repos": [first, missing], "active_repo": first,
        "graph_configs": {first: [260, 140, 100], missing: [320, 180, 100]},
    })
    window = window_factory(config_path)
    window._graph_table.set_divider_positions([280, 440])
    window.repo_tabs_view_model().set_active_tab(1)
    assert window._graph_table.divider_positions() == [280, 440]
    window.close()
    saved = load_config(config_path)
    assert load_graph_column_widths(saved, first) == [280, 160, 100]
    assert load_graph_column_widths(saved, missing) == [320, 180, 100]


@pytest.mark.parametrize("discard", [False, True])
def test_save_failure_has_explicit_close_outcome(
    window_factory, tmp_path, monkeypatch, discard,
):
    config_path = tmp_path / "config.json"
    save_config(config_path, {"author_name": "Original"})
    window = window_factory(config_path)
    terminal_close = Mock(return_value=True)
    monkeypatch.setattr(window._terminal, "close", terminal_close)
    warning = Mock(return_value=(
        QMessageBox.StandardButton.Discard if discard else QMessageBox.StandardButton.Cancel
    ))
    with monkeypatch.context() as context:
        context.setattr("src.ui.main_window.save_config", Mock(side_effect=PermissionError("disk")))
        context.setattr(QMessageBox, "warning", warning)
        assert window.close() is discard
    assert warning.call_count == 1
    assert terminal_close.called is discard
    assert window.isVisible() is not discard
    assert load_config(config_path)["author_name"] == "Original"
    if not discard:
        # Retrying after the write error is resolved closes normally.
        assert window.close()
        assert terminal_close.called


def test_malformed_fields_do_not_break_window_or_settings(window_factory, qtbot, tmp_path):
    config_path = tmp_path / "config.json"
    save_config(config_path, {
        "author_name": 123, "author_email": [], "use_default_git_credentials": {},
        "ssh_private_key": 1, "ssh_public_key": {}, "hotkeys": {"undo": []},
        "diff_view_mode": [], "recent_repos": [123, None, "bad\0path"],
        "active_repo": [], "graph_configs": [], "auto_fetch_interval_ms": 2**40,
        "window_size": [2**40, 800], "ai": [],
    })
    window = window_factory(config_path)
    assert window.repo_tabs_view_model().save_to_state()["paths"] == []
    dialog = SettingsDialog(str(config_path), window)
    qtbot.addWidget(dialog)
    assert dialog._author_name_edit.text() == ""
    assert dialog._author_email_edit.text() == ""
    dialog._author_name_edit.setText("Fixed Name")
    dialog._on_accept()
    window.close()
    assert load_config(config_path)["author_name"] == "Fixed Name"
