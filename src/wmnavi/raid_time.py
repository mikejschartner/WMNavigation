"""Raid remaining-time estimate from log start + Questie raidDuration."""

from __future__ import annotations

from datetime import datetime

# Keep a buffer so the loot planner leaves time to extract.
EXTRACT_SAFETY_BUFFER_S = 90.0
# Approximate raid travel including looting pauses (m/s).
TRAVEL_SPEED_MPS = 5.2
LOOT_STOP_SECONDS = 7.0


def remaining_seconds(
    *,
    in_raid: bool,
    raid_started_at: datetime | None,
    duration_min: int | None,
    now: datetime | None = None,
) -> float | None:
    """Seconds left in the raid, or None when duration/start are unknown.

    Does not invent a timer if Questie has no raidDuration or no raid start.
    """
    if not in_raid or raid_started_at is None:
        return None
    if not duration_min or duration_min <= 0:
        return None
    now = now or datetime.now()
    elapsed = (now - raid_started_at).total_seconds()
    return max(0.0, float(duration_min) * 60.0 - elapsed)


def usable_loot_seconds(remaining: float | None, extract_travel_s: float) -> float | None:
    if remaining is None:
        return None
    return max(0.0, remaining - extract_travel_s - EXTRACT_SAFETY_BUFFER_S)


def travel_seconds(distance_m: float) -> float:
    return max(0.0, float(distance_m) / TRAVEL_SPEED_MPS)
