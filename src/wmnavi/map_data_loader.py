"""Load all map layer data (extracts, containers, loot, usables)."""

from __future__ import annotations

import json
from collections import defaultdict

import requests

from .coords import point_in_crs_bounds
from .locks import resolve_lock_label
from .loot_loader import _item_from_raw, _load_items_dump, _spot_from_dict, _spot_to_dict
from .models import ContainerTypeInfo, ItemInfo, LootSpot, MapLayerData, MapPoint
from .paths import cache_dir
from .questie_source import find_map_entry, load_questie_labels, resolve_label

API_URL = "https://api.tarkov.dev/graphql"

FULL_MAP_QUERY = """
query MapLayers($name: String!, $gameMode: GameMode) {
  maps(name: [$name], gameMode: $gameMode) {
    normalizedName
    extracts {
      id name faction transferItem
      position { x y z }
    }
    transits {
      id description position { x y z }
    }
    lootLoose {
      id position { x y z }
      items { id }
    }
    lootContainers {
      lootContainer { id name normalizedName iconLink }
      position { x y z }
    }
    locks {
      id lockType key needsPower
      position { x y z }
    }
    switches {
      id name switchType
      position { x y z }
    }
    stationaryWeapons {
      stationaryWeapon { id name shortName iconLink }
      position { x y z }
    }
  }
}
"""


def _point_from_pos(entry_id: str, pos: dict, label: str = "", kind: str = "", meta=None) -> MapPoint:
    return MapPoint(
        id=entry_id,
        x=float(pos.get("x", 0)),
        y=float(pos.get("y", 0)),
        z=float(pos.get("z", 0)),
        label=label,
        kind=kind,
        meta=meta or {},
    )


def _classify_extract(raw: dict, labels: dict) -> tuple[list[str], MapPoint]:
    """Return faction buckets for an extract.

    tarkov.dev / Questie factions:
      - pmc / scav: that faction only
      - shared: either faction can use it alone (e.g. Crossroads) — NOT co-op
      - transferItem: payment/key/code requirement, NOT a faction

    Co-op (PMC + Scav together) is identified by name, e.g. Boiler Room Basement (Co-op).
    """
    pos = raw.get("position") or {}
    name = resolve_label(labels, raw.get("name") or raw.get("id") or "Extract")
    point = _point_from_pos(raw.get("id") or name, pos, name, "extract", dict(raw))
    faction = (raw.get("faction") or "").strip().lower()
    name_l = name.lower()

    if "co-op" in name_l or "(coop)" in name_l or faction in {"coop", "co-op"}:
        return ["coop"], point
    if faction == "scav":
        return ["scav"], point
    if faction == "pmc":
        return ["pmc"], point
    if faction == "shared":
        return ["pmc", "scav"], point

    # Fallback by well-known Customs scav-only names if faction is missing.
    scav_names = {
        "military base cp",
        "passage between rocks",
        "railroad to military base",
        "old road gate",
        "sniper roadblock",
        "railroad to port",
        "trailer park workers' shack",
        "railroad to tarkov",
        "warehouse 17",
        "factory shacks",
        "warehouse 4",
        "old gas station gate",
        "factory far corner",
        "administration gate",
        "scav checkpoint",
    }
    if name_l in scav_names or any(name_l.startswith(n) for n in scav_names):
        return ["scav"], point
    return ["pmc"], point


def _append_extract(data: MapLayerData, buckets: list[str], point: MapPoint):
    if "pmc" in buckets:
        data.extracts_pmc.append(point)
    if "scav" in buckets:
        # Shared extracts need a separate point instance for scav list.
        if "pmc" in buckets:
            data.extracts_scav.append(
                MapPoint(
                    id=f"{point.id}_scav",
                    x=point.x,
                    y=point.y,
                    z=point.z,
                    label=point.label,
                    kind=point.kind,
                    meta=dict(point.meta),
                )
            )
        else:
            data.extracts_scav.append(point)
    if "coop" in buckets:
        data.extracts_coop.append(point)


def _load_from_questie(map_slug: str, mode: str) -> MapLayerData | None:
    entry = find_map_entry(mode, map_slug)
    if not entry:
        return None

    labels = load_questie_labels(mode)
    items_dump = _load_items_dump(mode)
    data = MapLayerData(map_items={})

    for raw in entry.get("extracts") or []:
        buckets, point = _classify_extract(raw, labels)
        _append_extract(data, buckets, point)

    for idx, raw in enumerate(entry.get("transits") or []):
        pos = raw.get("position") or {}
        desc = resolve_label(labels, raw.get("description") or f"Transit {idx + 1}")
        data.transits.append(_point_from_pos(raw.get("id") or f"transit_{idx}", pos, desc, "transit", dict(raw)))

    container_groups: dict[str, list[MapPoint]] = defaultdict(list)
    for idx, raw in enumerate(entry.get("lootContainers") or []):
        cid = raw.get("lootContainer")
        if isinstance(cid, dict):
            cid = cid.get("id")
        cid = str(cid or f"container_{idx}")
        pos = raw.get("position") or {}
        name = resolve_label(labels, cid)
        container_groups[cid].append(_point_from_pos(f"{cid}_{idx}", pos, name, "container", {"container_id": cid}))

    for cid, spots in container_groups.items():
        name = spots[0].label if spots else cid
        data.containers[cid] = ContainerTypeInfo(id=cid, name=name, spots=spots)

    loose_ids: set[str] = set()
    for idx, raw in enumerate(entry.get("lootLoose") or []):
        pos = raw.get("position") or {}
        item_ids = [str(i) for i in (raw.get("items") or [])]
        loose_ids.update(item_ids)
        data.loose_loot.append(
            LootSpot(
                id=raw.get("id") or f"loot_{idx}",
                x=float(pos.get("x", 0)),
                y=float(pos.get("y", 0)),
                z=float(pos.get("z", 0)),
                item_ids=item_ids,
            )
        )

    data.map_items = {iid: items_dump[iid] for iid in loose_ids if iid in items_dump}
    data.raid_duration_min = _raid_duration(entry)

    for idx, raw in enumerate(entry.get("locks") or []):
        data.locks.append(_lock_point(raw, idx, items_dump))

    for idx, raw in enumerate(entry.get("switches") or []):
        pos = raw.get("position") or {}
        name = raw.get("name") or f"Switch {idx + 1}"
        data.switches.append(_point_from_pos(raw.get("id") or f"switch_{idx}", pos, name, "switch", dict(raw)))

    for idx, raw in enumerate(entry.get("stationaryWeapons") or []):
        pos = raw.get("position") or {}
        weapon = raw.get("stationaryWeapon")
        if isinstance(weapon, dict):
            wid = weapon.get("id") or ""
            label = weapon.get("shortName") or weapon.get("name") or "Gun"
        else:
            wid = str(weapon or "")
            label = items_dump[wid].short_name if wid in items_dump else "Stationary gun"
        data.stationary_weapons.append(
            _point_from_pos(raw.get("id") or f"gun_{idx}", pos, label, "stationary", dict(raw))
        )

    return data


def _load_from_api(map_slug: str, mode: str) -> MapLayerData | None:
    api_mode = mode if mode in {"regular", "pve"} else "regular"
    try:
        response = requests.post(
            API_URL,
            json={"query": FULL_MAP_QUERY, "variables": {"name": map_slug, "gameMode": api_mode}},
            timeout=60,
        )
        payload = response.json()
        if payload.get("errors"):
            return None
        maps = payload.get("data", {}).get("maps") or []
        if not maps:
            return None
        entry = maps[0]
    except Exception:
        return None

    labels = load_questie_labels(mode)
    items_dump = _load_items_dump(mode)
    data = MapLayerData(map_items={})

    for raw in entry.get("extracts") or []:
        buckets, point = _classify_extract(raw, labels)
        _append_extract(data, buckets, point)

    for idx, raw in enumerate(entry.get("transits") or []):
        pos = raw.get("position") or {}
        desc = resolve_label(labels, raw.get("description") or f"Transit {idx + 1}")
        data.transits.append(_point_from_pos(raw.get("id") or f"transit_{idx}", pos, desc, "transit", dict(raw)))

    container_groups: dict[str, list[MapPoint]] = defaultdict(list)
    for idx, raw in enumerate(entry.get("lootContainers") or []):
        container = raw.get("lootContainer") or {}
        cid = str(container.get("id") or f"container_{idx}")
        pos = raw.get("position") or {}
        name = container.get("name") or resolve_label(labels, cid)
        container_groups[cid].append(_point_from_pos(f"{cid}_{idx}", pos, name, "container", {"container_id": cid}))

    for cid, spots in container_groups.items():
        name = spots[0].label if spots else cid
        data.containers[cid] = ContainerTypeInfo(id=cid, name=name, spots=spots)

    loose_ids: set[str] = set()
    for idx, raw in enumerate(entry.get("lootLoose") or []):
        pos = raw.get("position") or {}
        item_ids = []
        for item in raw.get("items") or []:
            iid = item["id"] if isinstance(item, dict) else str(item)
            item_ids.append(iid)
            if isinstance(item, dict) and iid not in items_dump:
                items_dump[iid] = _item_from_raw(item)
        loose_ids.update(item_ids)
        data.loose_loot.append(
            LootSpot(
                id=raw.get("id") or f"loot_{idx}",
                x=float(pos.get("x", 0)),
                y=float(pos.get("y", 0)),
                z=float(pos.get("z", 0)),
                item_ids=item_ids,
            )
        )

    data.map_items = {iid: items_dump[iid] for iid in loose_ids if iid in items_dump}
    data.raid_duration_min = _raid_duration(entry)

    for idx, raw in enumerate(entry.get("locks") or []):
        data.locks.append(_lock_point(raw, idx, items_dump))

    for idx, raw in enumerate(entry.get("switches") or []):
        pos = raw.get("position") or {}
        name = raw.get("name") or f"Switch {idx + 1}"
        data.switches.append(_point_from_pos(raw.get("id") or f"switch_{idx}", pos, name, "switch", dict(raw)))

    for idx, raw in enumerate(entry.get("stationaryWeapons") or []):
        pos = raw.get("position") or {}
        weapon = raw.get("stationaryWeapon")
        if isinstance(weapon, dict):
            label = weapon.get("shortName") or weapon.get("name") or "Gun"
        else:
            label = "Stationary gun"
        data.stationary_weapons.append(
            _point_from_pos(raw.get("id") or f"gun_{idx}", pos, label, "stationary", dict(raw))
        )
    return data


def _raid_duration(entry: dict | None) -> int | None:
    if not entry:
        return None
    raw = entry.get("raidDuration")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _lock_point(raw: dict, idx: int, items_dump: dict) -> MapPoint:
    pos = raw.get("position") or {}
    key_name = resolve_lock_label(raw, items_dump)
    meta = dict(raw)
    meta["key_name"] = key_name
    key = raw.get("key")
    if isinstance(key, dict) and key.get("id"):
        meta["key"] = key.get("id")
    return _point_from_pos(raw.get("id") or f"lock_{idx}", pos, key_name, "lock", meta)


LAYER_CACHE_VERSION = 5


def _point_ok(point: MapPoint | LootSpot, map_meta: dict | None) -> bool:
    if not map_meta:
        return True
    return point_in_crs_bounds(
        point.x,
        point.z,
        map_meta.get("bounds"),
        int(map_meta.get("coordinateRotation") or 0),
        map_meta.get("transform"),
    )


def _filter_out_of_bounds(data: MapLayerData, map_meta: dict | None) -> MapLayerData:
    """Drop markers with bad upstream coordinates (e.g. Factory loot outliers)."""
    if not map_meta or not map_meta.get("bounds") or not map_meta.get("transform"):
        return data

    data.extracts_pmc = [p for p in data.extracts_pmc if _point_ok(p, map_meta)]
    data.extracts_scav = [p for p in data.extracts_scav if _point_ok(p, map_meta)]
    data.extracts_coop = [p for p in data.extracts_coop if _point_ok(p, map_meta)]
    data.transits = [p for p in data.transits if _point_ok(p, map_meta)]
    data.locks = [p for p in data.locks if _point_ok(p, map_meta)]
    data.switches = [p for p in data.switches if _point_ok(p, map_meta)]
    data.stationary_weapons = [p for p in data.stationary_weapons if _point_ok(p, map_meta)]
    data.loose_loot = [s for s in data.loose_loot if _point_ok(s, map_meta)]

    cleaned: dict[str, ContainerTypeInfo] = {}
    for cid, info in data.containers.items():
        spots = [p for p in info.spots if _point_ok(p, map_meta)]
        if spots:
            cleaned[cid] = ContainerTypeInfo(id=info.id, name=info.name, spots=spots)
    data.containers = cleaned

    # Keep only item ids that still appear on the map.
    keep_ids: set[str] = set()
    for spot in data.loose_loot:
        keep_ids.update(spot.item_ids)
    data.map_items = {iid: item for iid, item in data.map_items.items() if iid in keep_ids}
    return data


def _cache_path(map_slug: str, mode: str) -> str:
    return str(cache_dir() / f"{mode}_{map_slug}_layers.json")


def _serialize(data: MapLayerData) -> dict:
    def point_dict(p: MapPoint) -> dict:
        return {"id": p.id, "x": p.x, "y": p.y, "z": p.z, "label": p.label, "kind": p.kind, "meta": p.meta}

    def loot_dict(s: LootSpot) -> dict:
        return _spot_to_dict(s)

    return {
        "version": LAYER_CACHE_VERSION,
        "extracts_pmc": [point_dict(p) for p in data.extracts_pmc],
        "extracts_scav": [point_dict(p) for p in data.extracts_scav],
        "extracts_coop": [point_dict(p) for p in data.extracts_coop],
        "transits": [point_dict(p) for p in data.transits],
        "containers": {
            cid: {
                "id": info.id,
                "name": info.name,
                "spots": [point_dict(p) for p in info.spots],
            }
            for cid, info in data.containers.items()
        },
        "loose_loot": [loot_dict(s) for s in data.loose_loot],
        "locks": [point_dict(p) for p in data.locks],
        "switches": [point_dict(p) for p in data.switches],
        "stationary_weapons": [point_dict(p) for p in data.stationary_weapons],
        "map_item_ids": sorted(data.map_items.keys()),
        "raid_duration_min": data.raid_duration_min,
    }


def _deserialize(raw: dict, items_dump: dict[str, ItemInfo]) -> MapLayerData:
    def point_from(raw_p: dict) -> MapPoint:
        return MapPoint(
            id=raw_p["id"],
            x=float(raw_p["x"]),
            y=float(raw_p["y"]),
            z=float(raw_p["z"]),
            label=raw_p.get("label") or "",
            kind=raw_p.get("kind") or "",
            meta=raw_p.get("meta") or {},
        )

    data = MapLayerData()
    data.extracts_pmc = [point_from(p) for p in raw.get("extracts_pmc") or []]
    data.extracts_scav = [point_from(p) for p in raw.get("extracts_scav") or []]
    data.extracts_coop = [point_from(p) for p in raw.get("extracts_coop") or []]
    data.transits = [point_from(p) for p in raw.get("transits") or []]
    for cid, info in (raw.get("containers") or {}).items():
        data.containers[cid] = ContainerTypeInfo(
            id=info.get("id") or cid,
            name=info.get("name") or cid,
            spots=[point_from(p) for p in info.get("spots") or []],
        )
    data.loose_loot = [_spot_from_dict(s) for s in raw.get("loose_loot") or []]
    data.locks = [point_from(p) for p in raw.get("locks") or []]
    data.switches = [point_from(p) for p in raw.get("switches") or []]
    data.stationary_weapons = [point_from(p) for p in raw.get("stationary_weapons") or []]
    item_ids = raw.get("map_item_ids") or []
    data.map_items = {iid: items_dump[iid] for iid in item_ids if iid in items_dump}
    duration = raw.get("raid_duration_min")
    try:
        data.raid_duration_min = int(duration) if duration else None
    except (TypeError, ValueError):
        data.raid_duration_min = None
    return data


def load_map_layers(map_slug: str, mode: str, map_meta: dict | None = None) -> MapLayerData:
    cache_file = cache_dir() / f"{mode}_{map_slug}_layers.json"
    items_dump = _load_items_dump(mode)

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached and cached.get("version") == LAYER_CACHE_VERSION:
                return _deserialize(cached, items_dump)
        except json.JSONDecodeError:
            pass

    api_data = _load_from_api(map_slug, mode)
    questie_data = _load_from_questie(map_slug, mode)
    data = api_data or questie_data or MapLayerData()
    if api_data and questie_data:
        if not api_data.locks:
            api_data.locks = questie_data.locks
        if not api_data.switches:
            api_data.switches = questie_data.switches
        if not api_data.stationary_weapons:
            api_data.stationary_weapons = questie_data.stationary_weapons
        if not api_data.raid_duration_min:
            api_data.raid_duration_min = questie_data.raid_duration_min
        data = api_data
    if not data.raid_duration_min:
        entry = find_map_entry(mode, map_slug)
        data.raid_duration_min = _raid_duration(entry)
    if not data.map_items and data.loose_loot:
        loose_ids: set[str] = set()
        for spot in data.loose_loot:
            loose_ids.update(spot.item_ids)
        data.map_items = {iid: items_dump[iid] for iid in loose_ids if iid in items_dump}

    data = _filter_out_of_bounds(data, map_meta)
    cache_file.write_text(json.dumps(_serialize(data), indent=2), encoding="utf-8")
    return data
