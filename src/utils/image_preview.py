"""Image format recognition and decoding, shared by the ViewModels and UI."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import PurePosixPath

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage, QImageReader

from src.core.exceptions import GitError


@lru_cache(maxsize=1)
def _image_extensions() -> frozenset[str]:
    # Keep familiar formats routed to the viewer even if a Qt image plugin is
    # unavailable: the user should see a decode error, not binary text.
    common = {"png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "svgz", "ico", "tif", "tiff"}
    supported = {bytes(f).decode("ascii") for f in QImageReader.supportedImageFormats()}
    return frozenset(common | supported)


def is_image_path(path: str | None) -> bool:
    return bool(path and PurePosixPath(path).suffix.lower().lstrip(".") in _image_extensions())


@dataclass(frozen=True)
class ImagePreview:
    image: QImage
    version: str
    byte_size: int


def decode_image(data: bytes) -> QImage:
    """Decode from memory; QImage is safe to produce in a worker thread."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise GitError(f"Cannot display image: {reader.errorString()}")
    return image
