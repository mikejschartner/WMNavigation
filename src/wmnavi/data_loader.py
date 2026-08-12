"""Load map metadata and markers from tarkov.dev."""

from __future__ import annotations

import json
from pathlib import Path

import requests

from .paths import app_root, cache_dir, maps_json_path
from .questie_source import find_map_entry

ROOT = app_root()
DATA_DIR = ROOT / "data"
MAPS_JSON = maps_json_path()
CACHE_DIR = cache_dir()
API_URL = "https://api.tarkov.dev/graphql"

# Maps with no usable Questie layer data yet (empty extracts/loot).
UNUSABLE_MAPS = {"icebreaker", "terminal", "transits", "openworld"}

MAP_QUERY = """
query MapData($name: String!) {
  maps(name: [$name]) {
    normalizedName
    name
    extracts {
      name
      faction
      id
      position { x y z }
    }
  }
}
"""


def load_maps_index() -> list[dict]:
    raw = json.loads(MAPS_JSON.read_text(encoding="utf-8"))
    return raw


def get_interactive_map(map_slug: str) -> dict | None:
    for entry in load_maps_index():
        if entry.get("normalizedName") == map_slug:
            maps = entry.get("maps") or []
            # Prefer interactive layer that has either SVG or tiles.
            for layer in maps:
                if layer.get("projection") != "interactive":
                    continue
                if layer.get("svgPath") or layer.get("tilePath"):
                    return layer
            for layer in maps:
                if layer.get("projection") == "interactive":
                    return layer
            if maps:
                return maps[0]
    return None


def _map_has_displayable_layer(entry: dict) -> bool:
    slug = entry.get("normalizedName")
    if not slug or slug in UNUSABLE_MAPS:
        return False
    for layer in entry.get("maps") or []:
        if layer.get("projection") != "interactive":
            continue
        if layer.get("svgPath") or layer.get("tilePath"):
            return True
    return False


def list_map_names() -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    for entry in load_maps_index():
        if not _map_has_displayable_layer(entry):
            continue
        slug = entry["normalizedName"]
        # Prefer maps that actually have layer markers in Questie when available.
        questie = find_map_entry("regular", slug) or find_map_entry("pvp-season", slug)
        if questie is not None:
            extracts = questie.get("extracts") or []
            containers = questie.get("lootContainers") or []
            loose = questie.get("lootLoose") or []
            if not extracts and not containers and not loose:
                continue
        label = slug.replace("-", " ").title()
        names.append((label, slug))
    return sorted(names, key=lambda item: item[0])


def fetch_extracts(map_slug: str) -> list[dict]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{map_slug}_extracts.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached:
                return cached
        except json.JSONDecodeError:
            pass
    try:
        response = requests.post(
            API_URL,
            json={"query": MAP_QUERY, "variables": {"name": map_slug}},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            return []
        maps = payload.get("data", {}).get("maps") or []
        if not maps:
            return []
        extracts = maps[0].get("extracts") or []
        cache_file.write_text(json.dumps(extracts, indent=2), encoding="utf-8")
        return extracts
    except Exception:
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        return []
