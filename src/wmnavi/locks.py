"""Locked-door / keyed-room helpers. Display vs routing stay independent."""

from __future__ import annotations

from .models import LootSpot, MapPoint

# Loot this close to a keyed *door* (same floor) is treated as behind the lock.
LOCKED_ROOM_RADIUS_M = 12.0
LOCKED_ROOM_Y_PAD = 4.0
DOOR_TYPES = {"door", ""}


def lock_key_id(point: MapPoint) -> str:
    meta = point.meta or {}
    key = meta.get("key")
    if isinstance(key, dict):
        return str(key.get("id") or "")
    return str(key or meta.get("key_id") or "")


def lock_type(point: MapPoint) -> str:
    return str((point.meta or {}).get("lockType") or "").strip().lower()


def is_door_lock(point: MapPoint) -> bool:
    kind = lock_type(point)
    return kind in DOOR_TYPES or not kind


def lock_key_name(point: MapPoint) -> str:
    """Human-readable key name; never empty (map rendering fallback)."""
    meta = point.meta or {}
    for key in ("key_name", "keyName"):
        text = str(meta.get(key) or "").strip()
        if text and text.lower() not in {"lock", "unknown", "unknown key"}:
            return text
    key = meta.get("key")
    if isinstance(key, dict):
        text = str(key.get("name") or key.get("shortName") or "").strip()
        if text:
            return text
    label = (point.label or "").strip()
    if label and label.lower() not in {"lock", "unknown"}:
        return label
    kid = lock_key_id(point)
    if kid:
        return f"Unknown key ({kid[:8]})"
    return "Unknown key"


def door_locks(locks: list[MapPoint]) -> list[MapPoint]:
    return [p for p in locks if is_door_lock(p)]


def _lock_y_range(lock: MapPoint) -> tuple[float, float]:
    meta = lock.meta or {}
    try:
        bottom = float(meta["bottom"]) if meta.get("bottom") is not None else lock.y - 1.0
        top = float(meta["top"]) if meta.get("top") is not None else lock.y + 3.0
    except (TypeError, ValueError):
        bottom, top = lock.y - 1.0, lock.y + 3.0
    return bottom - 1.0, top + LOCKED_ROOM_Y_PAD


def _xz_dist2(ax: float, az: float, bx: float, bz: float) -> float:
    dx = ax - bx
    dz = az - bz
    return dx * dx + dz * dz


def locked_loot_ids(
    spots: list[LootSpot],
    locks: list[MapPoint],
    radius: float = LOCKED_ROOM_RADIUS_M,
) -> set[str]:
    """Spot ids that sit behind a keyed door. Trunk/container locks are ignored."""
    doors = door_locks(locks)
    if not doors or not spots:
        return set()
    r2 = radius * radius
    hidden: set[str] = set()
    for spot in spots:
        for lock in doors:
            lo, hi = _lock_y_range(lock)
            if not (lo <= spot.y <= hi) and abs(spot.y - lock.y) > LOCKED_ROOM_Y_PAD:
                continue
            if _xz_dist2(spot.x, spot.z, lock.x, lock.z) <= r2:
                hidden.add(spot.id)
                break
    return hidden


def resolve_lock_label(raw: dict, items_dump: dict) -> str:
    key = raw.get("key")
    key_id = ""
    if isinstance(key, dict):
        name = str(key.get("name") or key.get("shortName") or "").strip()
        if name:
            return name
        key_id = str(key.get("id") or "")
    else:
        key_id = str(key or "")
    item = items_dump.get(key_id) if key_id else None
    if item is not None:
        name = (item.name or "").strip()
        if name and not name.endswith(" Name") and name != key_id:
            return name
        short = (item.short_name or "").strip()
        if short and not short.endswith(" ShortName"):
            return short
    return "Unknown key"
