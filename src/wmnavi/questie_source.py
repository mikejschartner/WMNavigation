"""Resolve TarkovQuestie-compatible data dumps (bundled or installed)."""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

from .paths import app_root, is_frozen


def _folder_has_questie_data(folder: Path) -> bool:
    """True if folder looks usable for layers and/or quests."""
    if not folder.is_dir():
        return False
    # Layers need maps.json; quests need tasks.json. Accept either so partial packs still resolve.
    return (folder / "maps.json").exists() or (folder / "tasks.json").exists()


def questie_data_dir(mode: str) -> Path | None:
    """Locate mode data: bundled → next-to-exe → TarkovQuestie → LocalAppData override."""
    candidates: list[Path] = [
        app_root() / "data" / "questie" / mode,
    ]
    if is_frozen():
        candidates.append(Path(sys.executable).resolve().parent / "data" / "questie" / mode)
    candidates.append(
        Path.home() / "AppData/Local/Programs/TarkovQuestie/_internal/data" / mode
    )
    candidates.append(Path.home() / "AppData/Local/WMNavigation/questie" / mode)

    for folder in candidates:
        try:
            if _folder_has_questie_data(folder):
                return folder
        except OSError:
            continue

    # Season / Kord often reuses regular item dumps if mode folder is incomplete.
    if mode != "regular":
        return questie_data_dir("regular")
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
