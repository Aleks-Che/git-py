"""Image preview rendering, controls, and right-panel selection integration."""
from pathlib import Path

import pygit2
import pytest
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage
from src.ui.main_window import MainWindow
from src.ui.widgets.image_view_widget import ImageViewWidget
from src.utils.image_preview import ImagePreview, decode_image, is_image_path
from src.viewmodels.graph_viewmodel import WIP_SHA


def _image_bytes(color, width=80, height=40, fmt="PNG"):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, fmt)
    return bytes(buffer.data())


@pytest.mark.parametrize("fmt,extension", [("PNG", "PNG"), ("JPEG", "jpg"), ("BMP", "bmp"),
                                          ("WEBP", "webp")])
def test_supported_images_decode_without_text(fmt, extension, qtbot):
    assert is_image_path(f"folder/image.{extension}")
    data = _image_bytes("red", fmt=fmt)
    image = decode_image(data)
    assert image.width() == 80 and image.height() == 40
    widget = ImageViewWidget()
    qtbot.addWidget(widget)
    widget.show_loading("image")
    widget.show_result("image", ImagePreview(image, "Staged", len(data)), "")
    assert len(widget._canvas.scene().items()) == 1
    assert "80 × 40 px" in widget._info.text()


def test_svg_is_rendered_and_transparency_is_preserved(qtbot):
    data = (b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
            b'<rect width="20" height="20" fill="red"/></svg>')
    assert is_image_path("vector.svg")
    image = decode_image(data)
    assert image.pixelColor(5, 5) == QColor("red")
    assert image.pixelColor(30, 5).alpha() == 0


def test_fit_zoom_actual_size_and_resize(qtbot):
    widget = ImageViewWidget()
    qtbot.addWidget(widget)
    widget.resize(800, 500)
    widget.show()
    image = decode_image(_image_bytes("red", 2400, 1200))
    widget.show_loading("large.png")
    widget.show_result("large.png", ImagePreview(image, "Working tree", 1234), "")
    canvas = widget._canvas
    fit = canvas.transform().m11()
    assert 0 < fit < 1
    assert fit * image.width() <= canvas.viewport().width()
    qtbot.mouseClick(widget._buttons[2], Qt.MouseButton.LeftButton)
    assert canvas.transform().m11() == 1
    qtbot.mouseClick(widget._buttons[1], Qt.MouseButton.LeftButton)
    assert canvas.transform().m11() > 1
    qtbot.mouseClick(widget._buttons[3], Qt.MouseButton.LeftButton)
    assert canvas.transform().m11() == pytest.approx(fit, rel=0.02)
    widget.resize(1000, 600)
    qtbot.waitUntil(lambda: canvas.transform().m11() > fit)
    assert canvas.transform().m11() * image.height() <= canvas.viewport().height()


def test_failed_image_and_clear_release_previous_pixmap(qtbot):
    widget = ImageViewWidget()
    qtbot.addWidget(widget)
    preview = ImagePreview(decode_image(_image_bytes("blue")), "Staged", 100)
    widget.show_loading("good.png")
    widget.show_result("good.png", preview, "")
    widget.show_loading("broken.png")
    widget.show_result("good.png", preview, "")  # A late result cannot reappear.
    widget.show_result("broken.png", None, "Cannot display image")
    assert not widget._canvas.scene().items()
    assert "Cannot display image" in widget._message.text()
    assert all(not button.isEnabled() for button in widget._buttons)
    widget.clear()
    assert widget._path is None


def _wait_for_image(qtbot, window, color):
    viewer = window._image_view
    qtbot.waitUntil(lambda: bool(viewer._canvas.scene().items()), timeout=10000)
    image = viewer._canvas.scene().items()[0].pixmap().toImage()
    assert image.pixelColor(10, 10) == QColor(color)
    assert window._graph_stack.currentWidget() is viewer
    assert not window._diff_view.isVisible()


def test_right_panel_clicks_show_worktree_staged_and_historical_images(qtbot, committed_repo):
    manager = committed_repo
    file = Path(manager.path) / "image.PNG"
    file.write_bytes(_image_bytes("red"))
    manager.repo.index.add(file.name)
    manager.repo.index.write()
    sig = pygit2.Signature("tester", "tester@example.com")
    sha = str(manager.repo.create_commit(
        "HEAD", sig, sig, "image", manager.repo.index.write_tree(), [manager.repo.head.target],
    ))
    file.write_bytes(_image_bytes("green"))
    manager.repo.index.add(file.name)
    manager.repo.index.write()
    file.write_bytes(_image_bytes("blue"))
    window = MainWindow(config_path=None)
    qtbot.addWidget(window)
    window.show()
    vm = window._main_vm
    vm.set_repository(manager)
    vm.set_selected_commit(WIP_SHA)
    panel = vm.commit_panel_view_model()
    panel.select_file(file.name)
    _wait_for_image(qtbot, window, "blue")
    panel.select_file(file.name, staged=True)
    _wait_for_image(qtbot, window, "green")

    vm.set_selected_commit(sha)
    detail = window._right_panel._commit_detail
    qtbot.waitUntil(lambda: detail._files.count() == 1, timeout=15000)
    rect = detail._files.visualItemRect(detail._files.item(0))
    qtbot.mouseClick(detail._files.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    _wait_for_image(qtbot, window, "red")
    qtbot.mouseClick(detail._files.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert window._graph_stack.currentWidget() is window._graph_table
    assert not window._image_view._canvas.scene().items()

    vm.set_selected_commit(WIP_SHA)
    (Path(manager.path) / "new.txt").write_text("plain text\n")
    panel.refresh_status()
    panel.select_file("new.txt")
    assert window._graph_stack.currentWidget() is window._diff_view
    qtbot.waitUntil(lambda: "plain text" in window._diff_view.toPlainText(), timeout=10000)


def test_new_image_in_unborn_repository_opens_viewer(qtbot, tmp_git_repo):
    from src.core.repository import RepositoryManager

    file = tmp_git_repo / "new.png"
    file.write_bytes(_image_bytes("blue"))
    window = MainWindow(config_path=None)
    qtbot.addWidget(window)
    window.show()
    window._main_vm.set_repository(RepositoryManager(str(tmp_git_repo)))
    window._main_vm.set_selected_commit(WIP_SHA)
    window._main_vm.commit_panel_view_model().select_file(file.name)
    _wait_for_image(qtbot, window, "blue")
