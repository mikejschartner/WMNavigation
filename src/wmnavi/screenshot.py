"""Parse EFT screenshot filenames for position + facing."""

from __future__ import annotations

import re
from pathlib import Path

from .coords import PlayerState, quaternion_to_pitch_deg, quaternion_to_yaw_deg

# Example:
# 2025-03-30[21-04]_175.30, 1.37, 150.68_-0.01464, 0.98439, -0.14329, -0.10113_9.53 (0).png
SCREENSHOT_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})\[(?P<time>\d{2}-\d{2})\]_"
    r"(?P<x>-?\d+(?:\.\d+)?),\s*(?P<y>-?\d+(?:\.\d+)?),\s*(?P<z>-?\d+(?:\.\d+)?)_"
    r"(?P<qx>-?\d+(?:\.\d+)?),\s*(?P<qy>-?\d+(?:\.\d+)?),\s*(?P<qz>-?\d+(?:\.\d+)?),\s*(?P<qw>-?\d+(?:\.\d+)?)"
    r"(?:_(?P<extra>-?\d+(?:\.\d+)?))?",
    re.IGNORECASE,
)


def parse_screenshot(path: Path) -> PlayerState | None:
    match = SCREENSHOT_RE.search(path.name)
    if not match:
        return None
    groups = match.groupdict()
    qx = float(groups["qx"])
    qy = float(groups["qy"])
    qz = float(groups["qz"])
    qw = float(groups["qw"])
    return PlayerState(
        x=float(groups["x"]),
        y=float(groups["y"]),
        z=float(groups["z"]),
        yaw_deg=quaternion_to_yaw_deg(qx, qy, qz, qw),
        pitch_deg=quaternion_to_pitch_deg(qx, qy, qz, qw),
    )


def candidate_screenshot_dirs() -> list[Path]:
    """Possible Tarkov Screenshots folders (OneDrive layouts included)."""
    home = Path.home()
    roots = [
        home / "Documents" / "Escape from Tarkov",
        home / "OneDrive" / "Documents" / "Escape from Tarkov",
        home / "Pictures" / "Escape from Tarkov",
        home / "OneDrive" / "Pictures" / "Escape from Tarkov",
        # Common redirected Documents under OneDrive\Pictures\Documents
        home / "OneDrive" / "Pictures" / "Documents" / "Escape from Tarkov",
        home / "OneDrive" / "Documents" / "Pictures" / "Escape from Tarkov",
    ]
    dirs: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for path in (root / "Screenshots", root):
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            dirs.append(path)
    return dirs


def default_screenshot_dir() -> Path:
    """Pick the best existing Tarkov Screenshots folder."""
    best: Path | None = None
    best_count = -1
    for path in candidate_screenshot_dirs():
        if not path.is_dir():
            continue
        # Prefer folders that already contain coordinate screenshots.
        try:
            count = sum(
                1
                for f in path.iterdir()
                if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg"} and SCREENSHOT_RE.search(f.name)
            )
        except OSError:
            continue
        if count > best_count:
            best = path
            best_count = count
        elif best is None:
            best = path
            best_count = 0
    if best:
        return best
    # Fallback — create under local Documents when nothing exists yet.
    fallback = Path.home() / "Documents" / "Escape from Tarkov" / "Screenshots"
    return fallback


def is_eft_screenshot_name(name: str) -> bool:
    return bool(SCREENSHOT_RE.search(name))
