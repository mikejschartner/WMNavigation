"""Read bundled TarkovQuestie data when the API is unavailable."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


def questie_data_dir(mode: str) -> Path | None:
    base = Path.home() / "AppData/Local/Programs/TarkovQuestie/_internal/data"
    folder = base / mode
    if folder.exists():
        return folder
    return None


@lru_cache(maxsize=4)
def load_questie_maps(mode: str) -> dict:
    folder = questie_data_dir(mode)
    if not folder:
        return {}
    path = folder / "maps.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def load_questie_labels(mode: str) -> dict:
    folder = questie_data_dir(mode)
    if not folder:
        return {}
    path = folder / "maps_en.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("data") or {}


def resolve_label(labels: dict, key: str) -> str:
    if not key:
        return "Unknown"
    return labels.get(key) or labels.get(f"{key} Name") or key.replace("_", " ")


def find_map_entry(mode: str, map_slug: str) -> dict | None:
    payload = load_questie_maps(mode)
    maps = payload.get("data", {}).get("maps") or {}
    for entry in maps.values():
        if entry.get("normalizedName") == map_slug:
            return entry
    return None
