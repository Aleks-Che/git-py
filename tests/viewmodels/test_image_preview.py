"""Image requests preserve responsiveness and discard results for stale selections."""
import threading

import pytest
from PySide6.QtGui import QColor, QImage
from src.core.models import FileContent
from src.core.repository import RepositoryManager
from src.viewmodels.main_viewmodel import MainViewModel


def _decoded_image(data):
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor(data.decode()))
    return image


def test_image_read_and_decode_use_worker_owned_repository(qtbot, committed_repo, monkeypatch):
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    main_thread = threading.get_ident()
    called = []

    def read(manager, path, **selection):
        called.append((manager is committed_repo, threading.get_ident(), path, selection))
        return FileContent(b"red", before_deletion=True)

    def decode(data):
        assert threading.get_ident() != main_thread
        return _decoded_image(data)

    monkeypatch.setattr(RepositoryManager, "read_file_content", read)
    monkeypatch.setattr("src.viewmodels.main_viewmodel.decode_image", decode)
    activity = []
    vm.activity_changed.connect(activity.append)
    with qtbot.waitSignal(vm.file_image_ready, timeout=5000) as signal:
        vm.request_file_image("image.png", staged=True)
    path, preview, error = signal.args
    assert path == "image.png" and not error
    assert preview.image.pixelColor(0, 0) == QColor("red")
    assert "Staged" in preview.version and "before deletion" in preview.version
    assert called == [(False, called[0][1], "image.png", {"staged": True, "sha": None})]
    assert called[0][1] != main_thread
    qtbot.waitUntil(lambda: not vm._active_workers)
    assert activity == [True, False]


@pytest.mark.parametrize("action", ["replace", "deselect", "close_repository"])
def test_stale_image_never_replaces_current_selection(
    qtbot, committed_repo, monkeypatch, action,
):
    vm = MainViewModel(async_enabled=True)
    vm.set_repository(committed_repo)
    started = threading.Event()
    release = threading.Event()
    results = []
    vm.file_image_ready.connect(lambda *args: results.append(args))

    def read(manager, path, **selection):
        if selection["staged"]:
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release the image worker")
            return FileContent(b"red")
        return FileContent(b"blue")

    monkeypatch.setattr(RepositoryManager, "read_file_content", read)
    monkeypatch.setattr("src.viewmodels.main_viewmodel.decode_image", _decoded_image)
    try:
        vm.request_file_image("image.png", staged=True)
        qtbot.waitUntil(started.is_set, timeout=5000)
        if action == "replace":
            # Same path, different side: comparing only the path is insufficient.
            vm.request_file_image("image.png", staged=False)
            qtbot.waitUntil(lambda: len(results) == 1, timeout=5000)
        elif action == "deselect":
            vm.cancel_file_image()
        else:
            vm.close_repository()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not vm._active_workers, timeout=5000)
    if action == "replace":
        assert len(results) == 1
        assert results[0][1].image.pixelColor(0, 0) == QColor("blue")
    else:
        assert not results


@pytest.mark.parametrize("async_enabled", [False, True])
def test_corrupt_image_reports_error_and_finishes_loading(
    qtbot, committed_repo, monkeypatch, async_enabled,
):
    vm = MainViewModel(async_enabled=async_enabled)
    vm.set_repository(committed_repo)
    monkeypatch.setattr(
        RepositoryManager, "read_file_content", lambda *args, **kwargs: FileContent(b"broken"),
    )
    errors = []
    vm.error_occurred.connect(errors.append)
    with qtbot.waitSignal(vm.file_image_ready, timeout=5000) as signal:
        vm.request_file_image("broken.png")
    assert signal.args[0] == "broken.png"
    assert signal.args[1] is None
    assert "Cannot display image" in signal.args[2]
    assert errors == [signal.args[2]]
    qtbot.waitUntil(lambda: not vm._active_workers, timeout=5000)
    assert vm._activity_count == 0


def test_wip_refresh_requests_image_without_building_text_diff(qtbot, committed_repo, monkeypatch):
    vm = MainViewModel()
    vm.set_repository(committed_repo)
    panel = vm.commit_panel_view_model()
    requests = []
    monkeypatch.setattr(vm, "request_file_image", lambda *a, **kw: requests.append((a, kw)))

    def unexpected_diff(*args, **kwargs):
        pytest.fail("An image selection must not compute a text diff")

    monkeypatch.setattr(panel, "build_diff_text", unexpected_diff)
    panel.select_file("image.png", staged=True)
    panel.refresh_selected_diff()
    panel.request_full_document()
    assert requests == [(("image.png",), {"staged": True})] * 2
    assert panel.current_diff() == ""
    assert not panel.selected_file_supports_line_actions()
