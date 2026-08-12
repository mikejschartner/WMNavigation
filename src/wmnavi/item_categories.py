"""Quick-toggle loot categories for map item hunt."""

from __future__ import annotations

import re

from .models import ItemInfo

# Known rare loose valuables (bitcoin / LEDX / Virtex / GPU / Tetriz).
RARE_LOOT_IDS = {
    "59faff1d86f7746c51718c9c",  # Physical Bitcoin
    "5c0530ee86f774697952d952",  # LEDX
    "5c05308086f7746b2101e90b",  # Virtex
    "57347ca924597744596b4e71",  # Graphics card
    "5c12620d86f7743f8b198b72",  # Tetriz
    "5c12613b86f7743bbe2c3f76",  # Intelligence folder (common id; also matched by name)
}

RARE_LOOT_NAME_RE = re.compile(
    r"(physical bitcoin|ledx|virtex|graphics card|\bgpu\b|tetriz|"
    r"intelligence folder|prokill|golden neck|chainlet|"
    r"bronze lion|cat figurine|horse figurine|rooster|"
    r"raven figurine|wooden clock|tea pot|vase|fireklean|"
    r"axiom|portable dvd|video card)",
    re.IGNORECASE,
)

MARKED_KEY_NAME_RE = re.compile(r"marked key", re.IGNORECASE)
LABS_COLOR_KEYCARD_RE = re.compile(
    r"(terragroup labs keycard|\bkeycard\b).*(red|yellow|black|green|blue|violet)|"
    r"(red|yellow|black|green|blue|violet).*(terragroup labs keycard|keycard)",
    re.IGNORECASE,
)

CATEGORY_ORDER = ("marked_keys", "rare_loot")

CATEGORY_META = {
    "marked_keys": {
        "label": "Marked Keys",
        "tip": "Marked keys + Labs color keycards (Red/Yellow/Black/Green/Blue/Violet)",
    },
    "rare_loot": {
        "label": "Rare Loot",
        "tip": "Bitcoin, LEDX, Virtex, GPU, Tetriz, and similar high-end valuables",
    },
}


def is_marked_key(item: ItemInfo) -> bool:
    blob = f"{item.name} {item.short_name}"
    if MARKED_KEY_NAME_RE.search(blob):
        return True
    if LABS_COLOR_KEYCARD_RE.search(blob):
        return True
    # Short names for Labs cards
    if item.short_name.strip().lower() in {"red", "yellow", "black", "green", "blue", "violet"}:
        if "keycard" in item.name.lower() or "labs" in item.name.lower():
            return True
    return False


def is_rare_loot(item: ItemInfo) -> bool:
    if item.id in RARE_LOOT_IDS:
        return True
    blob = f"{item.name} {item.short_name}"
    return bool(RARE_LOOT_NAME_RE.search(blob))


_MATCHERS = {
    "marked_keys": is_marked_key,
    "rare_loot": is_rare_loot,
}


def item_in_category(item: ItemInfo, category_id: str) -> bool:
    fn = _MATCHERS.get(category_id)
    return bool(fn and fn(item))


def ids_for_categories(
    map_items: dict[str, ItemInfo],
    active_categories: set[str] | list[str],
) -> set[str]:
    active = set(active_categories)
    if not active:
        return set()
    out: set[str] = set()
    for iid, item in map_items.items():
        for cat in active:
            if item_in_category(item, cat):
                out.add(iid)
                break
    return out
