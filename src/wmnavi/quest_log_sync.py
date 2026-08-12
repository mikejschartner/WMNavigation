"""Sync quest accept/complete/fail from BSG client logs (Questie-style)."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .log_watcher import find_log_dir, _latest_session_dir
from .paths import cache_dir

# Push notification after AcceptQuest / FinishQuest:
#   "text": "quest started"
#   "templateId": "<questId> description"           → accepted
#   "templateId": "<questId> successMessageText"  → completed
#   "templateId": "<questId> failMessageText"     → failed
TEMPLATE_RE = re.compile(
    r'"templateId"\s*:\s*"(?P<qid>[a-f0-9]{24})\s+(?P<kind>\S+)',
    re.I,
)
TEXT_RE = re.compile(r'"text"\s*:\s*"(?P<text>[^"]*)"', re.I)
TYPE_RE = re.compile(r'"type"\s*:\s*(?P<typ>\d+)')
MODE_URL_RE = re.compile(
    r"https://gw-(?P<mode>pvp-season|pve|prod|pvp)[a-z0-9.-]*\.escapefromtarkov\.com",
    re.I,
)

KIND_ACCEPT = "description"
KIND_SUCCESS = "successMessageText"
KIND_FAIL = "failMessageText"


def _normalize_mode(token: str) -> str:
    t = (token or "").lower()
    if t in {"pvp-season", "season", "szn"}:
        return "pvp-season"
    if t in {"pve"}:
        return "pve"
    if t in {"prod", "pvp", "regular"}:
        return "regular"
    return "regular"


def detect_mode_in_text(text: str, default: str = "regular") -> str:
    matches = MODE_URL_RE.findall(text)
    if not matches:
        if "pvp-season" in text.lower():
            return "pvp-season"
        if "gw-pve" in text.lower():
            return "pve"
        return default
    # Prefer the most specific / last seen gateway in the chunk.
    return _normalize_mode(matches[-1])


@dataclass
class QuestEvent:
    quest_id: str
    kind: str  # accept | complete | fail
    mode: str
    source: str = ""


@dataclass
class QuestLogState:
    """Per-mode quest status derived from logs."""

    accepted: set[str] = field(default_factory=set)
    completed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)

    def apply(self, kind: str, quest_id: str):
        if kind == "accept":
            self.failed.discard(quest_id)
            self.completed.discard(quest_id)
            self.accepted.add(quest_id)
        elif kind == "complete":
            self.accepted.discard(quest_id)
            self.failed.discard(quest_id)
            self.completed.add(quest_id)
        elif kind == "fail":
            self.accepted.discard(quest_id)
            self.completed.discard(quest_id)
            self.failed.add(quest_id)

    def active_ids(self) -> set[str]:
        return set(self.accepted) - self.completed - self.failed

    def to_dict(self) -> dict:
        return {
            "accepted": sorted(self.accepted),
            "completed": sorted(self.completed),
            "failed": sorted(self.failed),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "QuestLogState":
        data = data or {}
        return cls(
            accepted=set(data.get("accepted") or []),
            completed=set(data.get("completed") or []),
            failed=set(data.get("failed") or []),
        )


def _kind_from_template(kind: str) -> str | None:
    base = (kind or "").split()[0]
    if base == KIND_ACCEPT:
        return "accept"
    if base == KIND_SUCCESS:
        return "complete"
    if base == KIND_FAIL:
        return "fail"
    return None


def parse_quest_events_from_text(text: str, *, default_mode: str = "regular", source: str = "") -> list[QuestEvent]:
    """
    Extract quest state changes from log text.

    Prefer full ChatMessageReceived JSON blocks; fall back to nearby
    templateId + quest started lines.
    """
    events: list[QuestEvent] = []
    mode = detect_mode_in_text(text, default_mode)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "Got notification | ChatMessageReceived" in line:
            j = i + 1
            while j < len(lines) and not lines[j].lstrip().startswith("{"):
                j += 1
            if j >= len(lines):
                i += 1
                continue
            buf: list[str] = []
            depth = 0
            for k in range(j, min(j + 120, len(lines))):
                buf.append(lines[k])
                depth += lines[k].count("{") - lines[k].count("}")
                if depth <= 0 and k > j:
                    break
            raw = "\n".join(buf)
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                i = j + 1
                continue
            msg = obj.get("message") or {}
            msg_text = str(msg.get("text") or "").lower()
            tpl = str(msg.get("templateId") or "")
            m = re.match(r"([a-f0-9]{24})\s+(\S+)", tpl, re.I)
            if m and msg_text == "quest started":
                mapped = _kind_from_template(m.group(2))
                # Accept notifications are type 10; completions often type 12.
                # Still trust template kind when text is quest started.
                if mapped:
                    events.append(
                        QuestEvent(
                            quest_id=m.group(1).lower(),
                            kind=mapped,
                            mode=mode,
                            source=source,
                        )
                    )
            i = j + 1
            continue
        i += 1

    # Fallback for truncated / alternate formats: templateId lines after "quest started"
    if not events:
        for idx, line in enumerate(lines):
            tm = TEMPLATE_RE.search(line)
            if not tm:
                continue
            mapped = _kind_from_template(tm.group("kind"))
            if not mapped:
                continue
            window = "\n".join(lines[max(0, idx - 12) : idx + 1])
            if "quest started" not in window.lower():
                continue
            events.append(
                QuestEvent(
                    quest_id=tm.group("qid").lower(),
                    kind=mapped,
                    mode=detect_mode_in_text(window, mode),
                    source=source,
                )
            )
    return events


def _log_files(root: Path) -> list[Path]:
    files: list[Path] = []
    try:
        sessions = [
            p for p in root.iterdir() if p.is_dir() and p.name.lower().startswith("log_")
        ]
    except OSError:
        return []
    sessions.sort(key=lambda p: p.stat().st_mtime)
    for session in sessions:
        for pattern in ("*output*.log", "*push-notifications*.log", "*application*.log"):
            files.extend(sorted(session.glob(pattern), key=lambda p: p.stat().st_mtime))
    return files


def import_quest_states_from_logs(
    log_root: Path | None = None,
    *,
    prefer_mode: str | None = None,
) -> dict[str, QuestLogState]:
    """Full historical scan → per-mode QuestLogState."""
    root = log_root or find_log_dir()
    states: dict[str, QuestLogState] = {
        "regular": QuestLogState(),
        "pve": QuestLogState(),
        "pvp-season": QuestLogState(),
    }
    if not root:
        return states

    for path in _log_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        default_mode = prefer_mode or detect_mode_in_text(text, "regular")
        for event in parse_quest_events_from_text(text, default_mode=default_mode, source=path.name):
            bucket = states.setdefault(event.mode, QuestLogState())
            bucket.apply(event.kind, event.quest_id)
    return states


def state_cache_path(mode: str) -> Path:
    return cache_dir() / f"quest_log_state_{mode}.json"


def save_states(states: dict[str, QuestLogState]):
    cache_dir().mkdir(parents=True, exist_ok=True)
    for mode, state in states.items():
        state_cache_path(mode).write_text(
            json.dumps(state.to_dict(), indent=2),
            encoding="utf-8",
        )


def load_cached_state(mode: str) -> QuestLogState | None:
    path = state_cache_path(mode)
    if not path.exists():
        return None
    try:
        return QuestLogState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None


class QuestLogWatcher(threading.Thread):
    """Tail logs for live accept/complete/fail (same source as Questie)."""

    daemon = True

    def __init__(self, on_events, on_status=None):
        super().__init__()
        self.on_events = on_events  # callable(list[QuestEvent])
        self.on_status = on_status
        self._stop = threading.Event()
        self._offsets: dict[str, int] = {}
        self._session: Path | None = None
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

    def _watch_files(self, session: Path) -> list[Path]:
        files: list[Path] = []
        for pattern in ("*output*.log", "*push-notifications*.log"):
            files.extend(session.glob(pattern))
        uniq: dict[str, Path] = {}
        for f in files:
            try:
                uniq[str(f.resolve()).lower()] = f
            except OSError:
                uniq[str(f).lower()] = f
        return sorted(uniq.values(), key=lambda p: p.stat().st_mtime, reverse=True)

    def run(self):
        while not self._stop.is_set():
            root = find_log_dir()
            if not root:
                self._set_status("Quest log sync: log folder not found")
                time.sleep(3)
                continue
            session = _latest_session_dir(root)
            if not session:
                time.sleep(2)
                continue
            if self._session != session:
                self._session = session
                self._offsets.clear()
                self._set_status(f"Quest log sync → {session.name}")
                # Start at EOF for live-only; historical import is separate.
                for path in self._watch_files(session):
                    try:
                        self._offsets[str(path)] = path.stat().st_size
                    except OSError:
                        pass

            for path in self._watch_files(session):
                key = str(path)
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                offset = self._offsets.get(key, size)
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
                if not chunk:
                    continue
                events = parse_quest_events_from_text(chunk, source=path.name)
                if events:
                    try:
                        self.on_events(events)
                    except Exception:
                        pass
            time.sleep(0.6)
