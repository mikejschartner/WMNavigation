"""Download and cache remote map assets."""

from __future__ import annotations

from pathlib import Path

import requests

from .paths import cache_dir


def cache_remote_file(url: str, filename: str) -> Path:
    target = cache_dir() / filename
    if target.exists() and target.stat().st_size > 0:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=30, headers={"User-Agent": "WMNavigation/0.3.4"})
    response.raise_for_status()
    target.write_bytes(response.content)
    return target
