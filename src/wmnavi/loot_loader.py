"""Load loot spawns and item prices for a map + game mode."""

from __future__ import annotations

import json
from pathlib import Path

import requests

from .models import ItemInfo, LootSpot
from .paths import cache_dir
from .questie_source import questie_data_dir as _questie_data_dir

API_URL = "https://api.tarkov.dev/graphql"
ITEMS_CACHE_VERSION = 4

GAME_MODES = {
    "Regular PvP": "regular",
    "PvE": "pve",
    "Kord Breach": "pvp-season",
}

# Questie dumps often lack trader sell prices (and sometimes flea). Force known valuables.
# Physical Bitcoin sells to Therapist for ~₽400,000.
ITEM_PRICE_OVERRIDES: dict[str, dict] = {
    "59faff1d86f7746c51718c9c": {
        "trader_price": 400_000,
        "trader_name": "Therapist",
        "name": "Physical Bitcoin",
        "short_name": "0.2BTC",
    },
}

# Spots that can roll these (or anything ≥ SUPER_RARE_MIN_PRICE) get a red star.
SUPER_RARE_ITEM_IDS = {
    "59faff1d86f7746c51718c9c",  # Physical Bitcoin
    "5c0530ee86f774697952d952",  # LEDX Skin Transilluminator
}
SUPER_RARE_MIN_PRICE = 400_000

LOOT_QUERY = """
query MapLoot($name: String!, $gameMode: GameMode) {
  maps(name: [$name], gameMode: $gameMode) {
    normalizedName
    lootLoose {
      id
      position { x y z }
      items { id name shortName iconLink avg24hPrice sellFor { price source vendor { name } } }
    }
  }
}
"""

ITEMS_QUERY = """
query Items($gameMode: GameMode) {
  items(gameMode: $gameMode, limit: 10000) {
    id
    name
    shortName
    iconLink
    avg24hPrice
    sellFor { price source vendor { name } }
  }
}
"""


def _best_trader(sell_for: list[dict]) -> tuple[int, str]:
    best = 0
    name = ""
    for entry in sell_for or []:
        source = (entry.get("source") or "").lower()
        if "flea" in source:
            continue
        price = int(entry.get("price") or 0)
        if price > best:
            best = price
            vendor = entry.get("vendor") or {}
            name = vendor.get("name") or source
    return best, name


def _looks_like_placeholder(text: str, item_id: str) -> bool:
    if not text:
        return True
    return text.endswith(" Name") or text.endswith(" ShortName") or text == item_id


def _icon_for_id(item_id: str, explicit: str = "") -> str:
    if item_id:
        return f"https://assets.tarkov.dev/{item_id}-512.webp"
    if explicit and explicit.startswith("http"):
        return explicit
    return ""


def _item_from_raw(raw: dict, labels: dict | None = None) -> ItemInfo:
    item_id = raw.get("id") or ""
    labels = labels or {}
    name = raw.get("name") or ""
    short = raw.get("shortName") or ""
    if labels:
        name = labels.get(f"{item_id} Name") or name
        short = labels.get(f"{item_id} ShortName") or short
    if _looks_like_placeholder(name, item_id):
        name = labels.get(f"{item_id} Name") or short or item_id
    if _looks_like_placeholder(short, item_id):
        short = labels.get(f"{item_id} ShortName") or name or item_id
    trader_price, trader_name = _best_trader(raw.get("sellFor") or [])
    explicit = (
        raw.get("image512pxLink")
        or raw.get("gridImageLink")
        or raw.get("iconLink")
        or ""
    )
    return ItemInfo(
        id=item_id,
        name=name or item_id,
        short_name=short or name or item_id,
        icon_url=_icon_for_id(item_id, explicit),
        flea_price=int(raw.get("avg24hPrice") or 0),
        trader_price=trader_price,
        trader_name=trader_name,
    )


def _load_questie_item_labels(mode: str) -> dict:
    questie = _questie_data_dir(mode)
    if not questie:
        return {}
    path = questie / "items_en.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("data") or {}
    except json.JSONDecodeError:
        return {}


def _load_items_from_questie(mode: str) -> dict[str, ItemInfo]:
    questie = _questie_data_dir(mode)
    if not questie or not (questie / "items.json").exists():
        return {}
    data = json.loads((questie / "items.json").read_text(encoding="utf-8"))
    items = data.get("data", {}).get("items", {})
    labels = _load_questie_item_labels(mode)
    return {iid: _item_from_raw(item, labels) for iid, item in items.items()}


def _cache_is_usable(raw: dict) -> bool:
    if not raw or raw.get("_version") != ITEMS_CACHE_VERSION:
        return False
    # Reject caches with unresolved Questie placeholders.
    for item in list(raw.values())[:20]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        if _looks_like_placeholder(name, item.get("id") or ""):
            return False
    return True


def _apply_price_overrides(items: dict[str, ItemInfo]) -> None:
    for iid, patch in ITEM_PRICE_OVERRIDES.items():
        item = items.get(iid)
        if not item:
            # Still inject so map_items can resolve bitcoin even if dump missed it.
            items[iid] = ItemInfo(
                id=iid,
                name=patch.get("name") or "Physical Bitcoin",
                short_name=patch.get("short_name") or "0.2BTC",
                icon_url=_icon_for_id(iid),
                flea_price=int(patch.get("flea_price") or 0),
                trader_price=int(patch.get("trader_price") or 0),
                trader_name=str(patch.get("trader_name") or ""),
            )
            continue
        if patch.get("name") and _looks_like_placeholder(item.name, iid):
            item.name = patch["name"]
        if patch.get("short_name") and _looks_like_placeholder(item.short_name, iid):
            item.short_name = patch["short_name"]
        if "trader_price" in patch:
            item.trader_price = max(item.trader_price, int(patch["trader_price"]))
        if patch.get("trader_name") and not item.trader_name:
            item.trader_name = str(patch["trader_name"])
        if "flea_price" in patch:
            item.flea_price = max(item.flea_price, int(patch["flea_price"]))


def _fetch_api_items(mode: str) -> dict[str, ItemInfo]:
    try:
        response = requests.post(
            API_URL,
            json={
                "query": ITEMS_QUERY,
                "variables": {"gameMode": mode if mode != "pvp-season" else "regular"},
            },
            timeout=60,
        )
        payload = response.json()
        if payload.get("errors"):
            return {}
        items = payload.get("data", {}).get("items") or []
        return {item["id"]: _item_from_raw(item) for item in items if item.get("id")}
    except Exception:
        return {}


def _merge_prices(dest: dict[str, ItemInfo], source: dict[str, ItemInfo]) -> None:
    """Fill missing flea/trader prices from another dump (usually tarkov.dev)."""
    for iid, src in source.items():
        cur = dest.get(iid)
        if not cur:
            continue
        if src.flea_price > cur.flea_price:
            cur.flea_price = src.flea_price
        if src.trader_price > cur.trader_price:
            cur.trader_price = src.trader_price
            if src.trader_name:
                cur.trader_name = src.trader_name


def load_items_catalog(mode: str) -> dict[str, ItemInfo]:
    """Full item dump for the game mode (prices + names + icon URLs)."""
    return _load_items_dump(mode)


def _load_items_dump(mode: str) -> dict[str, ItemInfo]:
    cache_file = cache_dir() / f"{mode}_items.json"
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            if _cache_is_usable(raw):
                payload = {k: v for k, v in raw.items() if k != "_version" and isinstance(v, dict)}
                result = {iid: _item_from_raw(item) for iid, item in payload.items()}
                _apply_price_overrides(result)
                return result
        except json.JSONDecodeError:
            pass

    # Prefer Questie dump: reliable icons + local translations.
    result = _load_items_from_questie(mode)
    if result:
        # Questie has almost no trader sellFor — merge prices from tarkov.dev.
        api_items = _fetch_api_items(mode)
        if api_items:
            _merge_prices(result, api_items)
        _apply_price_overrides(result)
        _write_items_cache(cache_file, result)
        return result

    # Try API alone
    result = _fetch_api_items(mode)
    if result:
        _apply_price_overrides(result)
        _write_items_cache(cache_file, result)
        return result

    _apply_price_overrides(result)
    return result


def _write_items_cache(cache_file: Path, result: dict[str, ItemInfo]):
    payload = {"_version": ITEMS_CACHE_VERSION}
    payload.update({iid: _as_dict(info) for iid, info in result.items()})
    cache_file.write_text(json.dumps(payload), encoding="utf-8")


def _as_dict(info: ItemInfo) -> dict:
    return {
        "id": info.id,
        "name": info.name,
        "shortName": info.short_name,
        "iconLink": info.icon_url,
        "avg24hPrice": info.flea_price,
        "sellFor": [{"price": info.trader_price, "source": info.trader_name, "vendor": {"name": info.trader_name}}]
        if info.trader_price
        else [],
    }


def _spot_from_dict(raw: dict) -> LootSpot:
    return LootSpot(
        id=raw["id"],
        x=float(raw["x"]),
        y=float(raw["y"]),
        z=float(raw["z"]),
        item_ids=list(raw.get("item_ids") or []),
    )


def _spot_to_dict(spot: LootSpot) -> dict:
    return {
        "id": spot.id,
        "x": spot.x,
        "y": spot.y,
        "z": spot.z,
        "item_ids": list(spot.item_ids),
    }


def _load_loot_dump(map_slug: str, mode: str) -> list[LootSpot]:
    cache_file = cache_dir() / f"{mode}_{map_slug}_loot.json"
    if cache_file.exists():
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
            return [_spot_from_dict(s) for s in raw]
        except json.JSONDecodeError:
            pass

    # Try API
    api_mode = mode if mode in {"regular", "pve"} else "regular"
    try:
        response = requests.post(
            API_URL,
            json={"query": LOOT_QUERY, "variables": {"name": map_slug, "gameMode": api_mode}},
            timeout=60,
        )
        payload = response.json()
        if not payload.get("errors"):
            maps = payload.get("data", {}).get("maps") or []
            if maps:
                spots = []
                for entry in maps[0].get("lootLoose") or []:
                    pos = entry.get("position") or {}
                    items = entry.get("items") or []
                    item_ids = [i["id"] if isinstance(i, dict) else i for i in items]
                    spots.append(
                        LootSpot(
                            id=entry.get("id") or f"{pos.get('x')}_{pos.get('z')}",
                            x=float(pos.get("x", 0)),
                            y=float(pos.get("y", 0)),
                            z=float(pos.get("z", 0)),
                            item_ids=item_ids,
                        )
                    )
                cache_file.write_text(json.dumps([_spot_to_dict(s) for s in spots], indent=2), encoding="utf-8")
                return spots
    except Exception:
        pass

    return []
