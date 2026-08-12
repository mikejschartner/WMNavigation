"""Filter items and loot spots by price + selection."""

from __future__ import annotations

from .models import ItemInfo, LootSpot


def item_best_price(item: ItemInfo) -> int:
    return max(item.flea_price, item.trader_price)


def item_passes_price(item: ItemInfo, min_price: int) -> bool:
    return item_best_price(item) >= min_price


def visible_map_items(
    map_items: dict[str, ItemInfo],
    price_enabled: bool,
    min_price: int,
) -> dict[str, ItemInfo]:
    if not price_enabled:
        return dict(map_items)
    return {
        iid: item
        for iid, item in map_items.items()
        if item_passes_price(item, min_price)
    }


def best_item_at_spot(
    spot: LootSpot,
    selected_ids: set[str],
    map_items: dict[str, ItemInfo],
) -> ItemInfo | None:
    matches = [map_items[iid] for iid in spot.item_ids if iid in selected_ids and iid in map_items]
    if not matches:
        return None
    return max(matches, key=item_best_price)


def spots_for_selection(
    spots: list[LootSpot],
    selected_ids: set[str],
) -> list[LootSpot]:
    if not selected_ids:
        return []
    return [spot for spot in spots if any(iid in selected_ids for iid in spot.item_ids)]


def spots_passing_price(
    spots: list[LootSpot],
    allowed_ids: set[str] | None,
) -> list[LootSpot]:
    """If allowed_ids is None, price filter is off — return all spots."""
    if allowed_ids is None:
        return spots
    if not allowed_ids:
        return []
    return [spot for spot in spots if any(iid in allowed_ids for iid in spot.item_ids)]


def items_at_spot(
    spot: LootSpot,
    pool: dict[str, ItemInfo],
) -> list[ItemInfo]:
    found = [pool[iid] for iid in spot.item_ids if iid in pool]
    return sorted(found, key=item_best_price, reverse=True)


def spot_is_super_rare(
    spot: LootSpot,
    map_items: dict[str, ItemInfo],
    allowed_ids: set[str] | None = None,
) -> bool:
    """True if this spawn can roll bitcoin / LEDX / other ≥ SUPER_RARE_MIN_PRICE loot."""
    from .loot_loader import SUPER_RARE_ITEM_IDS, SUPER_RARE_MIN_PRICE

    for iid in spot.item_ids:
        if allowed_ids is not None and iid not in allowed_ids:
            continue
        if iid in SUPER_RARE_ITEM_IDS:
            return True
        item = map_items.get(iid)
        if item and item_best_price(item) >= SUPER_RARE_MIN_PRICE:
            return True
    return False
