"""Cache remote item icons locally."""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from .paths import cache_dir

_ICON_MEM: dict[str, QPixmap] = {}


def _icon_path(url: str) -> Path:
    icons_dir = cache_dir() / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    if not filename.endswith((".png", ".jpg", ".webp", ".gif")):
        digest = hashlib.md5(url.encode()).hexdigest()[:12]
        filename = f"{digest}.png"
    return icons_dir / filename


def _download_icon(url: str, path: Path) -> bool:
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "WMNavigation/0.3.4"})
        response.raise_for_status()
        if len(response.content) < 128:
            return False
        path.write_bytes(response.content)
        return True
    except Exception:
        return False


def get_item_icon(url: str, size: int = 48, allow_download: bool = True) -> QPixmap:
    size = max(8, int(size))
    if not url:
        return QPixmap()

    cache_key = f"{url}:{size}"
    if cache_key in _ICON_MEM:
        return _ICON_MEM[cache_key]

    path = _icon_path(url)
    if not path.exists() or path.stat().st_size < 128:
        if not allow_download:
            return QPixmap()
        if not _download_icon(url, path):
            _ICON_MEM[cache_key] = QPixmap()
            return QPixmap()

    source = QPixmap(str(path))
    if source.isNull():
        _ICON_MEM[cache_key] = QPixmap()
        return QPixmap()

    if source.width() == size and source.height() == size:
        _ICON_MEM[cache_key] = source
        return source

    scaled = source.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    _ICON_MEM[cache_key] = scaled
    return scaled
