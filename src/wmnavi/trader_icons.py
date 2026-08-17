"""Load trader portrait pixmaps for quest map pins."""

from __future__ import annotations

from PySide6.QtGui import QPixmap

from .paths import app_root

_TRADER_FILES = {
    "prapor": "prapor.jpg",
    "therapist": "therapist.jpg",
    "fence": "fence.jpg",
    "skier": "skier.jpg",
    "peacekeeper": "peacekeeper.jpg",
    "mechanic": "mechanic.jpg",
    "ragman": "ragman.jpg",
    "jaeger": "jaeger.jpg",
    "lightkeeper": "lightkeeper.jpg",
    "ref": "ref.jpg",
    "btrdriver": "btr-driver.png",
    "btr": "btr-driver.png",
}

_PIXMAP_CACHE: dict[str, QPixmap] = {}


def normalize_trader_name(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def load_trader_portrait(name: str) -> QPixmap:
    """QPixmap from app_root()/assets/traders/, or a null pixmap if missing."""
    key = normalize_trader_name(name)
    if key in _PIXMAP_CACHE:
        return _PIXMAP_CACHE[key]
    pix = QPixmap()
    filename = _TRADER_FILES.get(key)
    if filename:
        path = app_root() / "assets" / "traders" / filename
        if path.exists():
            loaded = QPixmap(str(path))
            if not loaded.isNull():
                pix = loaded
    _PIXMAP_CACHE[key] = pix
    return pix
