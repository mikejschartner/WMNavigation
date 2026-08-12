"""Convert EFT world coordinates to map scene coordinates."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class PlayerState:
    x: float
    y: float
    z: float
    yaw_deg: float


def quaternion_to_yaw_deg(qx: float, qy: float, qz: float, qw: float) -> float:
    """Convert Unity camera quaternion to compass yaw (0° = north / +Z)."""
    # Forward vector from quaternion (Unity: camera looks along +Z local).
    forward_x = 2.0 * (qx * qz + qw * qy)
    forward_z = 1.0 - 2.0 * (qx * qx + qy * qy)
    heading = math.degrees(math.atan2(forward_x, forward_z))
    if heading < 0:
        heading += 360.0
    return heading


def _apply_rotation(x: float, z: float, rotation: int) -> tuple[float, float]:
    """Match tarkov.dev applyRotation(): lng=x, lat=z, returns (lng, lat)."""
    if not rotation:
        return x, z
    rad = math.radians(rotation)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    rotated_x = x * cos_a - z * sin_a
    rotated_y = x * sin_a + z * cos_a
    return rotated_x, rotated_y


def game_to_map(
    x: float,
    z: float,
    coordinate_rotation: int = 0,
    transform: list[float] | None = None,
) -> tuple[float, float]:
    """Map game X/Z to tarkov.dev CRS pixel coordinates."""
    lng, lat = _apply_rotation(x, z, coordinate_rotation)
    if transform and len(transform) >= 4:
        scale_x = transform[0]
        margin_x = transform[1]
        scale_y = -transform[2]
        margin_y = transform[3]
        return scale_x * lng + margin_x, scale_y * lat + margin_y
    return lng, lat


def crs_bounds_from_map(bounds, coordinate_rotation: int, transform: list[float] | None) -> tuple[float, float, float, float]:
    """Pixel bounds used by tarkov.dev when overlaying the SVG."""
    if not bounds or len(bounds) < 2 or not transform:
        return 0.0, 1062.0, 0.0, 535.0
    lng_values = [bounds[0][0], bounds[1][0]]
    lat_values = [bounds[0][1], bounds[1][1]]
    corners = [
        (lng_values[0], lat_values[0]),
        (lng_values[1], lat_values[1]),
        (lng_values[0], lat_values[1]),
        (lng_values[1], lat_values[0]),
    ]
    pixels = [game_to_map(x, z, coordinate_rotation, transform) for x, z in corners]
    xs = [p[0] for p in pixels]
    ys = [p[1] for p in pixels]
    return min(xs), max(xs), min(ys), max(ys)


def point_in_crs_bounds(
    x: float,
    z: float,
    bounds,
    coordinate_rotation: int,
    transform: list[float] | None,
    pad_ratio: float = 0.08,
) -> bool:
    """True if game X/Z maps inside CRS bounds (with padding)."""
    if not bounds or not transform:
        return True
    mx, my = game_to_map(x, z, coordinate_rotation, transform)
    min_x, max_x, min_y, max_y = crs_bounds_from_map(bounds, coordinate_rotation, transform)
    pad_x = (max_x - min_x) * pad_ratio
    pad_y = (max_y - min_y) * pad_ratio
    return (min_x - pad_x) <= mx <= (max_x + pad_x) and (min_y - pad_y) <= my <= (max_y + pad_y)
