"""Show/hide named groups inside tarkov.dev SVG maps for the active floor."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

# Maps.json svgLayer names → extra group ids used inside the SVG files.
LAYER_ALIASES: dict[str, tuple[str, ...]] = {
    "Ground_Level": ("Ground_Level", "Ground_Floor"),
    "Ground_Floor": ("Ground_Floor", "Ground_Level"),
    "First_Floor": ("First_Floor", "Floor-1"),
    "Second_Floor": ("Second_Floor", "Floor-2"),
    "Third_Floor": ("Third_Floor", "Floor-3"),
    "Fourth_Floor": ("Fourth_Floor", "Floor-4"),
    "Fifth_Floor": ("Fifth_Floor", "Floor-5"),
    "Underground_Level": ("Underground_Level", "Floor-U", "Basement"),
    "Basement": ("Basement", "Underground_Level", "Floor-U"),
    "Bunkers": ("Bunkers", "Underground_Level"),
    "Second_Level": ("Second_Level", "Second_Floor", "Floor-2"),
}

# Interior drawings that belong with ground unless a map uses them as an overlay.
GROUND_EXTRAS = ("First_Floor", "Floor-1")


def aliases_for(layer_id: str) -> set[str]:
    if not layer_id:
        return set()
    extra = LAYER_ALIASES.get(layer_id, ())
    return {layer_id, *extra}


def overlay_layer_ids(map_meta: dict | None) -> set[str]:
    """svgLayer names that are explicit floor overlays on this map."""
    out: set[str] = set()
    if not map_meta:
        return out
    for layer in map_meta.get("layers") or []:
        name = str(layer.get("svgLayer") or "").strip()
        if name:
            out |= aliases_for(name)
    return out


def map_has_floor_art(map_meta: dict | None) -> bool:
    """True if this map has extra SVG groups or tile sheets for other floors."""
    for layer in (map_meta or {}).get("layers") or []:
        if layer.get("svgLayer") or layer.get("tilePath"):
            return True
    return False


def ground_layer_ids(map_meta: dict | None) -> set[str]:
    primary = str((map_meta or {}).get("svgLayer") or "Ground_Level")
    ids = aliases_for(primary)
    overlays = overlay_layer_ids(map_meta)
    # Only fold First_Floor into ground on maps that actually have upper overlays
    # (Streets). Single-level maps like Lighthouse must stay Ground_Level only.
    if overlays:
        for extra in GROUND_EXTRAS:
            if extra not in overlays:
                ids |= aliases_for(extra)
    return ids


def all_toggle_ids(map_meta: dict | None) -> set[str]:
    """Only groups that belong to THIS map — never a global alias dump."""
    return ground_layer_ids(map_meta) | overlay_layer_ids(map_meta)


def apply_svg_floor(
    source: Path,
    dest: Path,
    *,
    map_meta: dict | None,
    active_layer: str,
    kind: str,
) -> bool:
    """Write a copy of the SVG with inactive floor groups hidden.

    Ground stays visible (dimmed on upper/underground) so streets remain a base layer.
    Returns False if the file could not be parsed.
    """
    try:
        tree = ET.parse(source)
        root = tree.getroot()
    except ET.ParseError:
        return False

    ground = ground_layer_ids(map_meta)
    overlays = overlay_layer_ids(map_meta)
    toggle = all_toggle_ids(map_meta)
    active = aliases_for(active_layer) if active_layer else set()

    show: set[str] = set()
    dim_ground = False
    if kind in {"all", "main"} or not active_layer:
        show |= ground
    elif kind == "underground":
        show |= active
        show |= ground
        dim_ground = True
    else:
        show |= ground
        show |= active
        dim_ground = True

    changed = False
    for el in root.iter():
        eid = el.attrib.get("id") or ""
        if not eid or eid not in toggle:
            continue
        if eid in show:
            el.attrib.pop("display", None)
            if dim_ground and eid in ground and eid not in active:
                el.set("opacity", "0.40")
            else:
                if el.attrib.get("opacity") in {"0.40", "0.4", "0.35"}:
                    el.attrib.pop("opacity", None)
            changed = True
        elif eid in overlays or eid in toggle:
            el.set("display", "none")
            changed = True

    dest.parent.mkdir(parents=True, exist_ok=True)
    tree.write(dest, encoding="utf-8", xml_declaration=True)
    return changed or dest.exists()
