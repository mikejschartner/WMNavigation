"""World-space ping state. One normal ping per owner; death markers stay separate."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

PING_NORMAL = "normal"
PING_DEATH = "death_last_ping"


@dataclass
class MapPing:
    ping_id: str
    ping_type: str
    owner_id: str
    owner_name: str
    map_slug: str
    x: float
    y: float
    z: float
    created_at: float
    expires_at: float  # 0 = until cleared / raid end
    color: str = "#fbbf24"
    predicted_origin: bool = False
    confidence: float = 1.0
    distance_m: float = 0.0

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at <= 0:
            return False
        now = time.time() if now is None else now
        return now >= self.expires_at

    def remaining_s(self, now: float | None = None) -> float:
        if self.expires_at <= 0:
            return 0.0
        now = time.time() if now is None else now
        return max(0.0, self.expires_at - now)


class PingManager:
    def __init__(self, owner_id: str):
        self.owner_id = owner_id
        self._pings: dict[str, MapPing] = {}
        self.last_valid: MapPing | None = None

    def active(self, map_slug: str | None = None) -> list[MapPing]:
        now = time.time()
        dead = [pid for pid, p in self._pings.items() if p.expired(now)]
        for pid in dead:
            self._pings.pop(pid, None)
        out = list(self._pings.values())
        if map_slug:
            out = [p for p in out if p.map_slug == map_slug]
        return out

    def upsert(self, ping: MapPing) -> MapPing:
        if ping.ping_type == PING_NORMAL:
            for pid, existing in list(self._pings.items()):
                if existing.owner_id == ping.owner_id and existing.ping_type == PING_NORMAL:
                    self._pings.pop(pid, None)
        self._pings[ping.ping_id] = ping
        if ping.owner_id == self.owner_id and ping.ping_type == PING_NORMAL:
            self.last_valid = ping
        return ping

    def remove(self, ping_id: str) -> MapPing | None:
        return self._pings.pop(ping_id, None)

    def clear_map(self, map_slug: str):
        for pid in [i for i, p in self._pings.items() if p.map_slug == map_slug]:
            self._pings.pop(pid, None)

    def raid_reset(self):
        self._pings.clear()
        self.last_valid = None

    def make_normal(
        self,
        *,
        owner_name: str,
        map_slug: str,
        x: float,
        y: float,
        z: float,
        duration_s: float,
        color: str,
        predicted_origin: bool,
        confidence: float,
        distance_m: float,
    ) -> MapPing:
        now = time.time()
        expires = 0.0 if duration_s <= 0 else now + float(duration_s)
        return MapPing(
            ping_id=uuid.uuid4().hex[:12],
            ping_type=PING_NORMAL,
            owner_id=self.owner_id,
            owner_name=owner_name,
            map_slug=map_slug,
            x=x,
            y=y,
            z=z,
            created_at=now,
            expires_at=expires,
            color=color,
            predicted_origin=predicted_origin,
            confidence=confidence,
            distance_m=distance_m,
        )

    def make_death(
        self,
        *,
        duration_s: float,
        until_raid_end: bool,
    ) -> MapPing | None:
        src = self.last_valid
        if src is None:
            return None
        now = time.time()
        expires = 0.0 if until_raid_end or duration_s <= 0 else now + float(duration_s)
        ping = MapPing(
            ping_id=uuid.uuid4().hex[:12],
            ping_type=PING_DEATH,
            owner_id=self.owner_id,
            owner_name=src.owner_name,
            map_slug=src.map_slug,
            x=src.x,
            y=src.y,
            z=src.z,
            created_at=now,
            expires_at=expires,
            color="#ef4444",
            predicted_origin=src.predicted_origin,
            confidence=src.confidence,
            distance_m=src.distance_m,
        )
        self._pings[ping.ping_id] = ping
        return ping


def ping_distance(ax, ay, az, bx, by, bz) -> float:
    dx, dy, dz = float(ax) - float(bx), float(ay) - float(by), float(az) - float(bz)
    return (dx * dx + dy * dy + dz * dz) ** 0.5
