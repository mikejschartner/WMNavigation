"""Floor height ranges from tarkov.dev map metadata.

Numbering matches Tarkov:
  Floor 0 = underground / basement
  Floor 1 = main / ground
  Floor 2+ = upper floors

Each Y belongs to exactly one floor so auto-select changes when you
go from 2nd to 3rd. svg_layer / tile_path drive the visible map plan.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FloorOption:
    label: str
    min_y: float
    max_y: float
    svg_layer: str = ""
    tile_path: str = ""
    kind: str = "main"  # all | underground | main | upper


def _primary_extent(extents: list[dict]) -> tuple[float, float] | None:
    """Use the lowest-starting height band, not the union of overlay extents."""
    ranges: list[tuple[float, float]] = []
    for ext in extents or []:
        height = ext.get("height")
        if not height or len(height) < 2:
            continue
        try:
            ranges.append((float(height[0]), float(height[1])))
        except (TypeError, ValueError):
            continue
    if not ranges:
        return None
    return min(ranges, key=lambda r: r[0])


def _is_underground_name(name: str) -> bool:
    key = (name or "").strip().lower()
    return any(
        token in key
        for token in ("underground", "basement", "bunker", "tunnel", "garage", "technical")
    )


def build_floor_options(map_meta: dict | None) -> list[FloorOption]:
    ground_svg = str((map_meta or {}).get("svgLayer") or "")
    ground_tiles = str((map_meta or {}).get("tilePath") or "")
    options = [
        FloorOption(
            "All Floors",
            -10000.0,
            10000.0,
            svg_layer=ground_svg,
            tile_path=ground_tiles,
            kind="all",
        )
    ]
    if not map_meta:
        options.append(FloorOption("Floor 1 (Main)", -10000.0, 10000.0, kind="main"))
        return options

    underground: list[tuple[str, float, float, str, str]] = []
    uppers: list[tuple[str, float, float, str, str]] = []
    for layer in map_meta.get("layers") or []:
        name = str(layer.get("name") or "Floor")
        span = _primary_extent(layer.get("extents") or [])
        if not span:
            continue
        low, high = span
        svg = str(layer.get("svgLayer") or "")
        tiles = str(layer.get("tilePath") or "")
        row = (name, low, high, svg, tiles)
        if _is_underground_name(name):
            underground.append(row)
        else:
            uppers.append(row)

    uppers.sort(key=lambda b: b[1])

    ug_high = -10000.0
    if underground:
        ug_name, _lo, ug_high, ug_svg, ug_tiles = underground[0]
        ug_high = max(b[2] for b in underground)
        if uppers and uppers[0][1] < ug_high:
            ug_high = uppers[0][1]
        extra = ug_name if ug_name.lower() not in {"underground", "basement"} else ""
        label = f"Floor 0 ({extra})" if extra else "Floor 0 (Underground)"
        options.append(
            FloorOption(
                label,
                -10000.0,
                ug_high,
                svg_layer=ug_svg or underground[0][3],
                tile_path=ug_tiles or underground[0][4],
                kind="underground",
            )
        )

    start_n = 2
    main_low = ug_high if underground else -10000.0
    if uppers and (uppers[0][1] - main_low) > 0.2:
        options.append(
            FloorOption(
                "Floor 1 (Main)",
                main_low,
                uppers[0][1],
                svg_layer=ground_svg,
                tile_path=ground_tiles,
                kind="main",
            )
        )
        start_n = 2
    elif not uppers:
        options.append(
            FloorOption(
                "Floor 1 (Main)",
                main_low,
                10000.0,
                svg_layer=ground_svg,
                tile_path=ground_tiles,
                kind="main",
            )
        )
        return options
    else:
        start_n = 1

    for i, (_name, low, _high, svg, tiles) in enumerate(uppers):
        n = start_n + i
        band_low = low if i else options[-1].max_y
        band_high = uppers[i + 1][1] if i + 1 < len(uppers) else 10000.0
        if band_high <= band_low:
            continue
        label = "Floor 1 (Main)" if n == 1 else f"Floor {n}"
        kind = "main" if n == 1 else "upper"
        options.append(
            FloorOption(
                label,
                band_low,
                band_high,
                svg_layer=svg if kind == "upper" else (svg or ground_svg),
                tile_path=tiles if kind == "upper" else (tiles or ground_tiles),
                kind=kind,
            )
        )

    return options


def marker_on_floor(y: float, floor: FloorOption) -> bool:
    if floor.max_y >= 9999:
        return floor.min_y <= y <= floor.max_y
    return floor.min_y <= y < floor.max_y


def floor_for_y(y: float, floors: list[FloorOption]) -> FloorOption | None:
    """Pick the named floor containing Y (skips 'All Floors')."""
    named = [f for f in floors if f.label.lower() != "all floors"]
    for floor in named:
        if marker_on_floor(y, floor):
            return floor
    if not named:
        return None
    return min(named, key=lambda f: min(abs(y - f.min_y), abs(y - f.max_y)))
