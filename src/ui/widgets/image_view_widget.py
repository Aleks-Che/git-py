"""Passive image viewer with fit, actual-size, zoom and drag-to-pan controls."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.utils.image_preview import ImagePreview


class _ImageCanvasWidget(QGraphicsView):
    zoom_changed = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self._fit_mode = True
        self._has_image = False
        # A palette-based checkerboard keeps transparent areas visible in any theme.
        tile = QPixmap(24, 24)
        tile.fill(self.palette().color(QPalette.ColorRole.Base))
        painter = QPainter(tile)
        color = self.palette().color(QPalette.ColorRole.AlternateBase)
        painter.fillRect(0, 0, 12, 12, color)
        painter.fillRect(12, 12, 12, 12, color)
        painter.end()
        self.setBackgroundBrush(QBrush(tile))

    def clear(self) -> None:
        self.scene().clear()
        self.setSceneRect(0, 0, 0, 0)
        self.resetTransform()
        self._has_image = False

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self.clear()
        item = self.scene().addPixmap(pixmap)
        self.setSceneRect(item.boundingRect())
        self._has_image = True
        self.fit_image()

    def fit_image(self) -> None:
        self._fit_mode = True
        self._fit()

    def _fit(self) -> None:
        if not self._has_image:
            return
        rect = self.sceneRect()
        viewport = self.viewport().size()
        factor = min(1.0, max(1, viewport.width() - 4) / rect.width(),
                     max(1, viewport.height() - 4) / rect.height())
        self.resetTransform()
        self.scale(factor, factor)
        self.centerOn(rect.center())
        self.zoom_changed.emit(factor)

    def set_zoom(self, factor: float) -> None:
        if not self._has_image:
            return
        self._fit_mode = False
        factor = max(0.01, min(32.0, factor))
        current = self.transform().m11()
        self.scale(factor / current, factor / current)
        self.zoom_changed.emit(factor)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.transform().m11() * factor)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode:
            self._fit()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._fit_mode:
            self._fit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.angleDelta().y() and self._has_image:
            self.zoom_by(1.25 if event.angleDelta().y() > 0 else 0.8)
            event.accept()
        else:
            super().wheelEvent(event)


class ImageViewWidget(QWidget):
    """Display an already decoded image; all file/Git reads belong to the VM."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._path: str | None = None
        self._title = QLabel(self)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setWordWrap(True)
        self._title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._info = QLabel(self)
        self._info.setTextFormat(Qt.TextFormat.PlainText)
        self._zoom_label = QLabel(self)
        self._canvas = _ImageCanvasWidget(self)
        self._canvas.zoom_changed.connect(
            lambda zoom: self._zoom_label.setText(f"{zoom:.0%}"),
        )
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._info, stretch=1)
        self._buttons: list[QPushButton] = []
        for text, tip, callback in (
            ("−", "Zoom out", lambda: self._canvas.zoom_by(0.8)),
            ("+", "Zoom in", lambda: self._canvas.zoom_by(1.25)),
            ("100%", "Actual size", lambda: self._canvas.set_zoom(1.0)),
            ("Fit", "Fit image to the available space", self._canvas.fit_image),
        ):
            button = QPushButton(text, self)
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(callback)
            toolbar.addWidget(button)
            self._buttons.append(button)
        toolbar.addWidget(self._zoom_label)

        self._message = QLabel(self)
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setWordWrap(True)
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._message)
        self._stack.addWidget(self._canvas)
        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addLayout(toolbar)
        layout.addWidget(self._stack, stretch=1)
        self.clear()

    def clear(self) -> None:
        self._path = None
        self._canvas.clear()
        self._title.clear()
        self._info.clear()
        self._zoom_label.clear()
        self._message.clear()
        self._stack.setCurrentWidget(self._message)
        for button in self._buttons:
            button.setEnabled(False)

    def show_loading(self, path: str) -> None:
        self.clear()
        self._path = path
        self._title.setText(path)
        self._message.setText("Loading image…")

    def show_result(self, path: str, preview: ImagePreview | None, error: str) -> None:
        if path != self._path:
            return
        if preview is None:
            self._canvas.clear()
            self._info.clear()
            self._zoom_label.clear()
            for button in self._buttons:
                button.setEnabled(False)
            self._message.setText(error or "Image preview is unavailable.")
            self._stack.setCurrentWidget(self._message)
            return
        image = preview.image
        self._info.setText(
            f"{image.width()} × {image.height()} px · "
            f"{preview.byte_size / 1024:.1f} KiB · {preview.version}",
        )
        self._stack.setCurrentWidget(self._canvas)
        self._canvas.set_pixmap(QPixmap.fromImage(image))
        for button in self._buttons:
            button.setEnabled(True)
