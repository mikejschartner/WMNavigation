"""Map-relative heading math. Reuses PlayerState.yaw_deg from screenshots.

0° = north = top of the current map (same convention as the player marker).
"""

from __future__ import annotations

import math
import time

from .coords import game_to_map


def wrap_deg(deg: float) -> float:
    return deg % 360.0


def shortest_delta(from_deg: float, to_deg: float) -> float:
    """Signed shortest turn from -> to in (-180, 180]."""
    return (to_deg - from_deg + 180.0) % 360.0 - 180.0


def map_heading(yaw_deg: float, map_rotation: int) -> float:
    """Facing relative to the map: 0° looks toward the top of the map."""
    return wrap_deg(float(yaw_deg) + float(map_rotation or 0))


def map_facing_deg(
    x: float,
    z: float,
    yaw_deg: float,
    map_rotation: int,
    transform: list[float] | None,
) -> float:
    """Qt rotation for the player arrow: project 1m forward, then measure on the map.

    yaw + map_rotation is wrong on 90°/270° maps (Factory, Labs) — the marker
    ends up backwards. This follows the same CRS as position.
    """
    rad = math.radians(float(yaw_deg))
    fx = float(x) + math.sin(rad)
    fz = float(z) + math.cos(rad)
    return map_bearing(x, z, fx, fz, map_rotation, transform)


def map_bearing(
    from_x: float,
    from_z: float,
    to_x: float,
    to_z: float,
    map_rotation: int,
    transform: list[float] | None,
) -> float:
    """Compass bearing from A to B in map space (0° = top of map / north)."""
    ax, ay = game_to_map(from_x, from_z, map_rotation, transform)
    bx, by = game_to_map(to_x, to_z, map_rotation, transform)
    # Scene Y grows downward; north (up) is -Y. East is +X.
    return wrap_deg(math.degrees(math.atan2(bx - ax, -(by - ay))))


class HeadingTracker:
    """Authoritative heading from screenshots, displayed heading for the HUD.

    Screenshot samples arrive every few seconds. The compass ticks this at
    60–120 FPS so rendering stays smooth; we do not invent extra orientation
    sources. New samples take the shortest angular path (358° → 2° is +4°).
    """

    # Catch ~90° in ~100ms. Fast enough to avoid lag, slow enough to hide jumps.
    LERP_RATE = 18.0
    SNAP_DEG = 0.2

    def __init__(self):
        self.authoritative = 0.0
        self.display = 0.0
        self.has_heading = False
        self._last_sample_at = 0.0
        self._last_tick = time.perf_counter()

    def set_authoritative(
        self,
        yaw_deg: float,
        map_rotation: int = 0,
        *,
        x: float | None = None,
        z: float | None = None,
        transform: list[float] | None = None,
    ):
        if x is not None and z is not None:
            heading = map_facing_deg(x, z, yaw_deg, map_rotation, transform)
        else:
            heading = map_heading(yaw_deg, map_rotation)
        self.authoritative = heading
        self._last_sample_at = time.perf_counter()
        if not self.has_heading:
            self.display = heading
            self.has_heading = True

    def tick(self) -> float:
        now = time.perf_counter()
        dt = max(0.0, min(0.05, now - self._last_tick))
        self._last_tick = now
        if not self.has_heading:
            return self.display
        delta = shortest_delta(self.display, self.authoritative)
        if abs(delta) <= self.SNAP_DEG:
            self.display = self.authoritative
            return self.display
        self.display = wrap_deg(self.display + delta * min(1.0, dt * self.LERP_RATE))
        return self.display

    @property
    def heading(self) -> float:
        return self.display if self.has_heading else self.authoritative
