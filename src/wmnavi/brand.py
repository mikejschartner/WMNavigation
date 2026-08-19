"""App logo / window icon helpers."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap

from .paths import app_root

APP_USER_MODEL_ID = "WMMods.WMNavigation"


def icon_png_path() -> Path:
    return app_root() / "assets" / "icon.png"


def icon_ico_path() -> Path:
    return app_root() / "assets" / "icon.ico"


def load_logo_pixmap(size: int = 256) -> QPixmap:
    path = icon_png_path()
    pix = QPixmap(str(path)) if path.exists() else QPixmap()
    if pix.isNull():
        return pix
    return pix.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def app_icon() -> QIcon:
    icon = QIcon()
    ico = icon_ico_path()
    png = icon_png_path()
    if ico.exists():
        icon.addFile(str(ico))
    if png.exists():
        pix = QPixmap(str(png))
        if not pix.isNull():
            for edge in (16, 24, 32, 48, 64, 128, 256):
                icon.addPixmap(
                    pix.scaled(
                        edge,
                        edge,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
    return icon


def apply_windows_app_id() -> None:
    """So the taskbar uses this app's icon instead of python.exe."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
