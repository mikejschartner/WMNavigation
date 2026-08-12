"""Load Tarkov quests/tasks for a map from Questie dumps."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .models import MapPoint
from .questie_source import questie_data_dir

# ground-zero-21 / tutorial share the same playable map as ground-zero.
MAP_SLUG_ALIASES = {
    "ground-zero-21": "ground-zero",
    "ground-zero-tutorial": "ground-zero",
    "night-factory": "factory",
    "the-lab-dark": "the-lab",
}


@dataclass
class QuestInfo:
    id: str
    name: str
    trader: str = ""
    wiki_link: str = ""
    requires_key: bool = False
    key_ids: list[str] = field(default_factory=list)
    objectives: list[dict] = field(default_factory=list)
    spots: list[MapPoint] = field(default_factory=list)
    # True when this quest has coordinate objectives on the requested map.
    on_map: bool = True
    # Multi-line objective summary for hover tooltips.
    requirements_text: str = ""


@lru_cache(maxsize=4)
def _load_tasks_payload(mode: str) -> dict:
    folder = questie_data_dir(mode)
    if not folder:
        return {}
    path = folder / "tasks.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=4)
def _load_task_labels(mode: str) -> dict[str, str]:
    folder = questie_data_dir(mode)
    if not folder:
        return {}
    path = folder / "tasks_en.json"
    if not path.exists():
        return {}
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("data") or {})
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=4)
def _load_item_labels(mode: str) -> dict[str, str]:
    folder = questie_data_dir(mode)
    if not folder:
        return {}
    path = folder / "items_en.json"
    if not path.exists():
        return {}
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("data") or {})
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=4)
def _load_map_labels(mode: str) -> dict[str, str]:
    folder = questie_data_dir(mode)
    if not folder:
        return {}
    path = folder / "maps_en.json"
    if not path.exists():
        return {}
    try:
        return (json.loads(path.read_text(encoding="utf-8")).get("data") or {})
    except json.JSONDecodeError:
        return {}


@lru_cache(maxsize=4)
def _map_id_to_slug(mode: str) -> dict[str, str]:
    folder = questie_data_dir(mode)
    if not folder:
        return {}
    path = folder / "maps.json"
    if not path.exists():
        # shared maps file one level up
        path = folder.parent / "maps.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    maps = (payload.get("data") or {}).get("maps") or {}
    out: dict[str, str] = {}
    if isinstance(maps, dict):
        for mid, entry in maps.items():
            if not isinstance(entry, dict):
                continue
            slug = entry.get("normalizedName") or ""
            rid = entry.get("id") or mid
            if slug:
                out[str(rid)] = slug
    return out


def _label(labels: dict[str, str], key: str, fallback: str = "") -> str:
    if not key:
        return fallback
    return (
        labels.get(key)
        or labels.get(f"{key} Name")
        or labels.get(f"{key} name")
        or labels.get(f"{key} Nickname")
        or fallback
        or key
    )


def _item_display_name(labels: dict[str, str], item_id: str) -> str:
    if not item_id:
        return ""
    return (
        labels.get(f"{item_id} ShortName")
        or labels.get(f"{item_id} Name")
        or labels.get(item_id)
        or ""
    )


def _map_display_name(labels: dict[str, str], map_id: str, id_to_slug: dict[str, str]) -> str:
    if not map_id:
        return ""
    named = labels.get(f"{map_id} Name") or labels.get(map_id)
    if named:
        return named
    slug = id_to_slug.get(map_id) or ""
    if slug:
        return slug.replace("-", " ").title()
    return map_id


def _trader_name(labels: dict[str, str], trader_id: str) -> str:
    if not trader_id:
        return ""
    return (
        labels.get(f"{trader_id} Nickname")
        or labels.get(f"{trader_id} Name")
        or labels.get(f"{trader_id} name")
        or labels.get(trader_id)
        or trader_id
    )


def _map_ids_for_slug(mode: str, map_slug: str) -> set[str]:
    id_to_slug = _map_id_to_slug(mode)
    aliases = {map_slug}
    for raw, canon in MAP_SLUG_ALIASES.items():
        if canon == map_slug or raw == map_slug:
            aliases.add(raw)
            aliases.add(canon)
    return {mid for mid, slug in id_to_slug.items() if slug in aliases}


def _as_map_id(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("id") or value.get("map")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _objective_map_ids(obj: dict) -> set[str]:
    """Map ids referenced by an objective (maps / zones / possibleLocations)."""
    out: set[str] = set()
    for m in obj.get("maps") or []:
        mid = _as_map_id(m)
        if mid:
            out.add(mid)
    for zone in obj.get("zones") or []:
        if not isinstance(zone, dict):
            continue
        mid = _as_map_id(zone.get("map"))
        if mid:
            out.add(mid)
    for loc in obj.get("possibleLocations") or []:
        if isinstance(loc, dict):
            mid = _as_map_id(loc.get("map") or loc.get("id"))
        else:
            mid = _as_map_id(loc)
        if mid:
            out.add(mid)
    return out


def _task_has_work_on_map(task: dict, map_ids: set[str]) -> bool:
    """True if any objective has maps/zones/possibleLocations on these map ids.

    Does not count neededKeys or bare task.map alone.
    """
    if not map_ids:
        return False
    for obj in task.get("objectives") or []:
        if not isinstance(obj, dict):
            continue
        if _objective_map_ids(obj) & map_ids:
            return True
    return False


def _task_has_anywhere_work(task: dict) -> bool:
    """True if any objective is unbound to a specific map (any-map work)."""
    for obj in task.get("objectives") or []:
        if not isinstance(obj, dict):
            continue
        if not _objective_map_ids(obj):
            return True
    return False


def _task_touches_map(task: dict, map_ids: set[str]) -> bool:
    """Legacy touch check (includes neededKeys / task.map). Prefer work helpers."""
    mid = task.get("map")
    if isinstance(mid, dict):
        mid = mid.get("id")
    if mid and str(mid) in map_ids:
        return True
    if _task_has_work_on_map(task, map_ids):
        return True
    for entry in task.get("neededKeys") or []:
        if not isinstance(entry, dict):
            continue
        km = entry.get("map")
        if km and str(km) in map_ids:
            return True
    return False


def _objective_item_ids(obj: dict) -> list[str]:
    ids: list[str] = []
    for key in ("items",):
        for raw in obj.get(key) or []:
            if isinstance(raw, str):
                ids.append(raw)
            elif raw is not None:
                ids.append(str(raw))
    for key in ("item", "questItem", "target"):
        raw = obj.get(key)
        if isinstance(raw, str) and raw:
            ids.append(raw)
        elif isinstance(raw, dict):
            rid = raw.get("id")
            if rid:
                ids.append(str(rid))
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for iid in ids:
        if iid not in seen:
            seen.add(iid)
            out.append(iid)
    return out


def _requirements_text(
    task: dict,
    labels: dict[str, str],
    item_labels: dict[str, str],
    map_labels: dict[str, str],
    id_to_slug: dict[str, str],
) -> str:
    lines: list[str] = []
    for obj in task.get("objectives") or []:
        if not isinstance(obj, dict):
            continue
        desc = _label(labels, obj.get("description") or "", obj.get("type") or "Objective")
        flags: list[str] = []
        count = obj.get("count")
        if isinstance(count, (int, float)) and count > 0:
            flags.append(f"×{int(count)}")
        if obj.get("foundInRaid"):
            flags.append("FIR")
        if obj.get("optional"):
            flags.append("optional")

        item_names: list[str] = []
        for iid in _objective_item_ids(obj):
            name = _item_display_name(item_labels, iid)
            if name and name not in item_names and name.lower() not in desc.lower():
                item_names.append(name)

        map_names: list[str] = []
        for mid in sorted(_objective_map_ids(obj)):
            mname = _map_display_name(map_labels, mid, id_to_slug)
            if mname and mname not in map_names:
                map_names.append(mname)

        line = f"• {desc}"
        if flags:
            line += f" ({', '.join(flags)})"
        if item_names:
            line += f" — {', '.join(item_names)}"
        if map_names:
            line += f" [{', '.join(map_names)}]"
        lines.append(line)
    return "\n".join(lines)


def _keys_for_map(task: dict, map_ids: set[str]) -> list[str]:
    keys: list[str] = []
    for entry in task.get("neededKeys") or []:
        if not isinstance(entry, dict):
            continue
        km = entry.get("map")
        if km and str(km) not in map_ids:
            continue
        for kid in entry.get("keys") or []:
            keys.append(kid if isinstance(kid, str) else str(kid))
    for obj in task.get("objectives") or []:
        if not isinstance(obj, dict):
            continue
        for kid in obj.get("requiredKeys") or []:
            if isinstance(kid, list):
                for nested in kid:
                    keys.append(nested if isinstance(nested, str) else str(nested))
            else:
                keys.append(kid if isinstance(kid, str) else str(kid))
    return sorted(set(keys))


def _spots_for_task(
    task: dict,
    labels: dict[str, str],
    map_ids: set[str],
    quest_name: str,
    requires_key: bool,
) -> list[MapPoint]:
    spots: list[MapPoint] = []
    for obj in task.get("objectives") or []:
        if not isinstance(obj, dict):
            continue
        desc = _label(labels, obj.get("description") or "", obj.get("type") or "Objective")
        obj_id = obj.get("id") or ""
        obj_type = obj.get("type") or ""
        zones = obj.get("zones") or []
        for idx, zone in enumerate(zones):
            if not isinstance(zone, dict):
                continue
            zm = zone.get("map")
            if zm and str(zm) not in map_ids:
                continue
            pos = zone.get("position") or {}
            try:
                x = float(pos.get("x", 0))
                y = float(pos.get("y", 0))
                z = float(pos.get("z", 0))
            except (TypeError, ValueError):
                continue
            spots.append(
                MapPoint(
                    id=f"{task.get('id')}_{obj_id}_{idx}",
                    x=x,
                    y=y,
                    z=z,
                    label=quest_name,
                    kind="quest",
                    meta={
                        "quest_id": task.get("id") or "",
                        "quest_name": quest_name,
                        "objective_id": obj_id,
                        "description": desc,
                        "type": obj_type,
                        "optional": bool(obj.get("optional")),
                        "requires_key": requires_key,
                    },
                )
            )
    return spots


def load_quests_for_map(map_slug: str, mode: str = "regular") -> list[QuestInfo]:
    """All quests that have objectives / keys on this map."""
    payload = _load_tasks_payload(mode)
    tasks_raw = (payload.get("data") or {}).get("tasks") or {}
    if isinstance(tasks_raw, list):
        tasks = tasks_raw
    elif isinstance(tasks_raw, dict):
        tasks = list(tasks_raw.values())
    else:
        return []

    labels = _load_task_labels(mode)
    item_labels = _load_item_labels(mode)
    map_labels = _load_map_labels(mode)
    id_to_slug = _map_id_to_slug(mode)
    map_ids = _map_ids_for_slug(mode, map_slug)
    if not map_ids:
        # Fallback: still try slug-only matching via aliases later
        return []

    traders_labels = _traders_labels(mode)

    out: list[QuestInfo] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if not _task_has_work_on_map(task, map_ids):
            continue
        out.append(
            _build_quest_info(
                task,
                labels,
                traders_labels,
                map_ids,
                item_labels=item_labels,
                map_labels=map_labels,
                id_to_slug=id_to_slug,
            )
        )

    out.sort(key=lambda q: q.name.lower())
    return out


def _iter_tasks(mode: str) -> list[dict]:
    payload = _load_tasks_payload(mode)
    tasks_raw = (payload.get("data") or {}).get("tasks") or {}
    if isinstance(tasks_raw, list):
        return [t for t in tasks_raw if isinstance(t, dict)]
    if isinstance(tasks_raw, dict):
        return [t for t in tasks_raw.values() if isinstance(t, dict)]
    return []


def objective_to_task_index(mode: str = "regular") -> dict[str, str]:
    """objective id -> task id"""
    out: dict[str, str] = {}
    for task in _iter_tasks(mode):
        tid = task.get("id") or ""
        if not tid:
            continue
        for obj in task.get("objectives") or []:
            if isinstance(obj, dict) and obj.get("id"):
                out[str(obj["id"])] = str(tid)
    return out


def _build_quest_info(
    task: dict,
    labels: dict[str, str],
    traders_labels: dict,
    map_ids: set[str],
    *,
    item_labels: dict[str, str] | None = None,
    map_labels: dict[str, str] | None = None,
    id_to_slug: dict[str, str] | None = None,
) -> QuestInfo:
    tid = task.get("id") or ""
    name = _label(labels, task.get("name") or "", tid)
    key_ids = _keys_for_map(task, map_ids) if map_ids else []
    # Also gather keys not map-filtered when map_ids empty
    if not map_ids:
        key_ids = []
        for entry in task.get("neededKeys") or []:
            if isinstance(entry, dict):
                for kid in entry.get("keys") or []:
                    key_ids.append(kid if isinstance(kid, str) else str(kid))
        for obj in task.get("objectives") or []:
            if isinstance(obj, dict):
                for kid in obj.get("requiredKeys") or []:
                    if isinstance(kid, list):
                        for nested in kid:
                            key_ids.append(nested if isinstance(nested, str) else str(nested))
                    else:
                        key_ids.append(kid if isinstance(kid, str) else str(kid))
        key_ids = sorted(set(key_ids))
    requires_key = bool(key_ids)
    trader_id = task.get("trader") or ""
    if isinstance(trader_id, dict):
        trader_id = trader_id.get("id") or ""
    trader_name = _trader_name(traders_labels, trader_id)
    spots = _spots_for_task(task, labels, map_ids, name, requires_key) if map_ids else []
    on_map = bool(map_ids) and _task_has_work_on_map(task, map_ids)
    req = _requirements_text(
        task,
        labels,
        item_labels or {},
        map_labels or {},
        id_to_slug or {},
    )
    return QuestInfo(
        id=tid,
        name=name,
        trader=trader_name,
        wiki_link=task.get("wikiLink") or "",
        requires_key=requires_key,
        key_ids=key_ids,
        objectives=[o for o in (task.get("objectives") or []) if isinstance(o, dict)],
        spots=spots,
        on_map=on_map,
        requirements_text=req,
    )


def _traders_labels(mode: str) -> dict:
    folder = questie_data_dir(mode)
    if folder and (folder / "traders_en.json").exists():
        try:
            return json.loads((folder / "traders_en.json").read_text(encoding="utf-8")).get("data") or {}
        except json.JSONDecodeError:
            return {}
    return {}


def load_quests_split(
    map_slug: str,
    mode: str = "regular",
    *,
    only_ids: set[str] | None = None,
) -> tuple[list[QuestInfo], list[QuestInfo]]:
    """
    Return (on_this_map, anywhere) quests.

    If only_ids is set (active-player mode):
      - on_map: objectives with work on this map (maps/zones/possibleLocations)
      - anywhere: true any-map objectives (no map binding), not other-map-only
      - other-map-only active quests are omitted
    If only_ids is None (catalog): only quests with work on this map.
    """
    labels = _load_task_labels(mode)
    item_labels = _load_item_labels(mode)
    map_labels = _load_map_labels(mode)
    id_to_slug = _map_id_to_slug(mode)
    traders = _traders_labels(mode)
    map_ids = _map_ids_for_slug(mode, map_slug)
    on_map: list[QuestInfo] = []
    anywhere: list[QuestInfo] = []

    for task in _iter_tasks(mode):
        tid = str(task.get("id") or "")
        if not tid:
            continue
        if only_ids is not None and tid not in only_ids:
            continue

        has_on_map = bool(map_ids) and _task_has_work_on_map(task, map_ids)
        has_anywhere = _task_has_anywhere_work(task)

        if only_ids is None:
            # Catalog mode: only map-work quests.
            if not has_on_map:
                continue
            info = _build_quest_info(
                task,
                labels,
                traders,
                map_ids,
                item_labels=item_labels,
                map_labels=map_labels,
                id_to_slug=id_to_slug,
            )
            info.on_map = True
            on_map.append(info)
            continue

        # Active-player mode: on-map, anywhere, or skip (other-map-only).
        if has_on_map:
            info = _build_quest_info(
                task,
                labels,
                traders,
                map_ids,
                item_labels=item_labels,
                map_labels=map_labels,
                id_to_slug=id_to_slug,
            )
            info.on_map = True
            on_map.append(info)
        elif has_anywhere:
            info = _build_quest_info(
                task,
                labels,
                traders,
                set(),
                item_labels=item_labels,
                map_labels=map_labels,
                id_to_slug=id_to_slug,
            )
            info.on_map = False
            info.spots = []
            anywhere.append(info)
        # else: other-map-only → hidden for this map view

    on_map.sort(key=lambda q: q.name.lower())
    anywhere.sort(key=lambda q: q.name.lower())
    return on_map, anywhere
