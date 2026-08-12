"""Tail BSG client logs for automatic map detection."""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

# EFT location tokens / display names -> tarkov.dev normalized map names.
LOCATION_TO_MAP = {
    "bigmap": "customs",
    "customs": "customs",
    "factory4_day": "factory",
    "factory4_night": "factory",
    "factory4": "factory",
    "factory": "factory",
    "nightfactory": "factory",
    "woods": "woods",
    "shoreline": "shoreline",
    "lighthouse": "lighthouse",
    "laboratory": "the-lab",
    "laboratory_area": "the-lab",
    "labs": "the-lab",
    "thelab": "the-lab",
    "the-lab": "the-lab",
    "rezervbase": "reserve",
    "reservebase": "reserve",
    "reserve": "reserve",
    "interchange": "interchange",
    "tarkovstreets": "streets-of-tarkov",
    "streets": "streets-of-tarkov",
    "streetsoftarkov": "streets-of-tarkov",
    "streets-of-tarkov": "streets-of-tarkov",
    "city": "streets-of-tarkov",
    "suburbs": "streets-of-tarkov",
    "sandbox": "ground-zero",
    "sandbox_high": "ground-zero",
    "groundzero": "ground-zero",
    "ground-zero": "ground-zero",
    "develop": "ground-zero",
    "terminal": "terminal",
    "labyrinth": "the-labyrinth",
    "labyrinth_area": "the-labyrinth",
    "the-labyrinth": "the-labyrinth",
}

# Modern + legacy log line formats.
LOCATION_PATTERNS = [
    # 2026+ : scene preset path:maps/lighthouse_preset.bundle rcid:lighthouse.scenespreset.asset
    re.compile(r"scene preset path:maps/(?P<loc>[A-Za-z0-9_]+)_preset", re.I),
    re.compile(r"rcid:(?P<loc>[A-Za-z0-9_]+)\.scenespreset", re.I),
    # TRACE-NetworkGameCreate ... Location: Shoreline, Sid: ...
    re.compile(r"Location:\s*(?P<loc>[A-Za-z0-9_ \-]+?)(?=,|\s+Sid:|\s+GameMode:|$)", re.I),
    re.compile(r"RaidLocation:\s*(?P<loc>[A-Za-z0-9_ \-]+)", re.I),
    # [Transit] ... Locations:Lighthouse ->
    re.compile(r"Locations:\s*(?P<loc>[A-Za-z0-9_]+)\s*->", re.I),
    re.compile(r'"Location"\s*:\s*"(?P<loc>[A-Za-z0-9_ \-]+)"', re.I),
    re.compile(r"arenaName['\"]?\s*[:=]\s*['\"]?(?P<loc>[A-Za-z0-9_]+)", re.I),
    re.compile(r"mapName['\"]?\s*[:=]\s*['\"]?(?P<loc>[A-Za-z0-9_]+)", re.I),
]

RAID_END_PATTERNS = [
    re.compile(r"SessionEnd", re.I),
    re.compile(r"SessionEndUIScene", re.I),
    re.compile(r"LeftGame", re.I),
    re.compile(r"ExitStatus", re.I),
    re.compile(r"EndOfRaid", re.I),
    re.compile(r"Returned to menu", re.I),
    # Common post-raid returns to stash/hideout (2024–2026 client logs).
    re.compile(r"StartLoadHideoutBundles", re.I),
    re.compile(r"client/game/profile/select", re.I),
]


def normalize_location(token: str) -> str | None:
    key = re.sub(r"[^a-z0-9\-]+", "", (token or "").strip().lower().replace(" ", ""))
    # Keep hyphens for keys like ground-zero after stripping spaces.
    key2 = re.sub(r"[^a-z0-9]+", "", (token or "").strip().lower())
    return LOCATION_TO_MAP.get(key) or LOCATION_TO_MAP.get(key2) or LOCATION_TO_MAP.get(
        (token or "").strip().lower()
    )


def _game_install_log_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path):
        try:
            if path.is_dir():
                key = str(path.resolve()).lower()
                if key not in seen:
                    seen.add(key)
                    roots.append(path)
        except OSError:
            pass

    for drive in ("C", "D", "E", "F", "G"):
        add(Path(f"{drive}:/Battlestate Games/Escape from Tarkov/Logs"))
        add(Path(f"{drive}:/Games/Escape from Tarkov/Logs"))
        add(Path(f"{drive}:/Escape from Tarkov/Logs"))

    local = Path.home() / "AppData" / "Local" / "Battlestate Games"
    if local.exists():
        for pattern in ("EscapeFromTarkov*/Logs", "Escape from Tarkov/Logs"):
            for path in local.glob(pattern):
                add(path)

    # Launcher may store install path.
    launcher = Path.home() / "AppData" / "Local" / "Battlestate Games" / "BsgLauncher"
    if launcher.exists():
        for conf in launcher.rglob("*.json"):
            try:
                text = conf.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in re.finditer(r'"(?:InstallPath|GamePath|installDir)"\s*:\s*"([^"]+)"', text, re.I):
                raw = match.group(1).replace("\\\\", "\\")
                add(Path(raw) / "Logs")
                add(Path(raw) / "Escape from Tarkov" / "Logs")

    return roots


def find_log_dir() -> Path | None:
    roots = _game_install_log_roots()
    if not roots:
        return None
    # Prefer the root whose newest session folder is most recent.
    best: Path | None = None
    best_mtime = -1.0
    for root in roots:
        try:
            mtime = root.stat().st_mtime
            for child in root.iterdir():
                if child.is_dir() and child.name.lower().startswith("log_"):
                    mtime = max(mtime, child.stat().st_mtime)
            if mtime > best_mtime:
                best = root
                best_mtime = mtime
        except OSError:
            continue
    return best


def _latest_session_dir(log_root: Path) -> Path | None:
    try:
        sessions = [
            p
            for p in log_root.iterdir()
            if p.is_dir() and p.name.lower().startswith("log_")
        ]
    except OSError:
        return None
    if not sessions:
        return log_root if log_root.exists() else None
    return max(sessions, key=lambda p: p.stat().st_mtime)


def _watch_files(session: Path) -> list[Path]:
    """Prefer application + output logs that carry Location / scene preset lines."""
    files: list[Path] = []
    for pattern in ("*application*.log", "*output*.log"):
        files.extend(session.glob(pattern))
    if not files:
        files = list(session.glob("*.log"))
    # Unique, newest first for initial scan preference.
    uniq: dict[str, Path] = {}
    for f in files:
        uniq[str(f.resolve()).lower()] = f
    return sorted(uniq.values(), key=lambda p: p.stat().st_mtime, reverse=True)


class LogWatcher(threading.Thread):
    daemon = True

    def __init__(self, on_map, on_raid_end, on_status=None):
        super().__init__()
        self.on_map = on_map
        self.on_raid_end = on_raid_end
        self.on_status = on_status
        self._stop = threading.Event()
        self._offsets: dict[str, int] = {}
        self._session: Path | None = None
        self._last_map: str | None = None
        self._last_status = ""

    def stop(self):
        self._stop.set()

    def _set_status(self, text: str):
        if text == self._last_status:
            return
        self._last_status = text
        if self.on_status:
            try:
                self.on_status(text)
            except Exception:
                pass

    def _emit_map(self, slug: str):
        if slug == self._last_map:
            return
        self._last_map = slug
        self.on_map(slug)

    def _parse_chunk(self, chunk: str, *, allow_raid_end: bool):
        for line in chunk.splitlines():
            for pattern in LOCATION_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                mapped = normalize_location(match.group("loc"))
                if mapped:
                    self._emit_map(mapped)
                    break
            if allow_raid_end and any(p.search(line) for p in RAID_END_PATTERNS):
                # Don't treat menu spam as raid end unless we were in a raid map.
                if self._last_map:
                    self.on_raid_end()
                    self._last_map = None

    def _bootstrap_current_map(self, files: list[Path]):
        """On attach, scan the tail of logs so mid-raid launches still switch maps."""
        for path in files:
            try:
                data = path.read_bytes()
            except OSError:
                continue
            # Keep offset at EOF so we only process new lines after bootstrap.
            self._offsets[str(path)] = len(data)
            # Read last ~512KB for recent Location / scene preset.
            tail = data[-512_000:].decode("utf-8", errors="ignore")
            found: str | None = None
            for line in tail.splitlines():
                for pattern in LOCATION_PATTERNS:
                    match = pattern.search(line)
                    if not match:
                        continue
                    mapped = normalize_location(match.group("loc"))
                    if mapped:
                        found = mapped
            if found:
                self._emit_map(found)
                return

    def run(self):
        while not self._stop.is_set():
            log_root = find_log_dir()
            if not log_root:
                self._set_status("Log folder not found — map auto-detect offline")
                time.sleep(3)
                continue

            session = _latest_session_dir(log_root)
            if not session:
                self._set_status(f"No log sessions in {log_root}")
                time.sleep(2)
                continue

            if self._session != session:
                self._session = session
                self._offsets.clear()
                files = _watch_files(session)
                self._set_status(f"Watching raid logs → {session.name}")
                self._bootstrap_current_map(files)

            files = _watch_files(session)
            for path in files:
                key = str(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                offset = self._offsets.get(key, 0)
                if size < offset:
                    offset = 0
                if size == offset:
                    continue
                try:
                    with path.open("r", encoding="utf-8", errors="ignore") as handle:
                        handle.seek(offset)
                        chunk = handle.read()
                        self._offsets[key] = handle.tell()
                except OSError:
                    continue
                if chunk:
                    self._parse_chunk(chunk, allow_raid_end=True)

            time.sleep(0.5)
