"""Floor height ranges from tarkov.dev map metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FloorOption:
    label: str
    min_y: float
    max_y: float


def _extent_range(extents: list[dict]) -> tuple[float, float] | None:
    mins: list[float] = []
    maxs: list[float] = []
    for ext in extents or []:
        height = ext.get("height")
        if not height or len(height) < 2:
            continue
        mins.append(float(height[0]))
        maxs.append(float(height[1]))
    if not mins:
        return None
    return min(mins), max(maxs)


def build_floor_options(map_meta: dict | None) -> list[FloorOption]:
    options = [FloorOption("All Floors", -10000.0, 10000.0)]
    if not map_meta:
        options.append(FloorOption("Floor 0", -10000.0, 10000.0))
        return options

    layers = map_meta.get("layers") or []
    underground: tuple[float, float] | None = None
    upper: list[tuple[str, float, float]] = []

    for layer in layers:
        name = layer.get("name") or "Floor"
        span = _extent_range(layer.get("extents") or [])
        if not span:
            continue
        low, high = span
        if name.lower() == "underground":
            underground = (low, high)
            continue
        upper.append((name, low, high))

    if underground:
        options.append(FloorOption("Underground", underground[0], underground[1]))
        ground_max = underground[1]
        options.append(FloorOption("Floor 0", ground_max, 2.8))
    else:
        options.append(FloorOption("Floor 0", -10000.0, 2.8))

    for name, low, high in upper:
        options.append(FloorOption(name, low, high))

    return options


def marker_on_floor(y: float, floor: FloorOption) -> bool:
    return floor.min_y <= y <= floor.max_y


def floor_for_y(y: float, floors: list[FloorOption]) -> FloorOption | None:
    """Pick the named floor containing Y (skips 'All Floors')."""
    named = [f for f in floors if f.label.lower() != "all floors"]
    # Prefer the tightest matching band.
    matches = [f for f in named if marker_on_floor(y, f)]
    if not matches:
        return None
    return min(matches, key=lambda f: (f.max_y - f.min_y))
