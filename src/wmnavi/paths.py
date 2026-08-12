"""Resolve project paths for dev and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def user_data_dir() -> Path:
    path = Path.home() / "AppData" / "Local" / "WMNavigation"
    path.mkdir(parents=True, exist_ok=True)
    return path


def maps_json_path() -> Path:
    bundled = app_root() / "data" / "maps.json"
    if bundled.exists():
        return bundled
    return app_root() / "data" / "maps.json"


def cache_dir() -> Path:
    path = user_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path
