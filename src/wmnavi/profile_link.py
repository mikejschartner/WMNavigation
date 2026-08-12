"""Link tarkov.dev account IDs and Tarkov Tracker progress tokens."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import __version__

USER_AGENT = f"WMNavigation/{__version__} (https://github.com; desktop companion)"

# https://tarkov.dev/players/regular/1234567
# https://players.tarkov.dev/profile/1234567.json
PROFILE_URL_RE = re.compile(
    r"(?ix)"
    r"(?:"
    r"https?://(?:www\.)?tarkov\.dev/players/(?P<mode>regular|pve|pvp-season|pvp)/(?P<aid>\d+)"
    r"|https?://players\.tarkov\.dev/(?P<pmode>profile|pve|pvp-season)/(?P<paid>\d+)"
    r")"
)

BARE_AID_RE = re.compile(r"^\s*(\d{5,10})\s*$")
TRACKER_TOKEN_RE = re.compile(r"\b((?:PVP|PVE|SZN)_[A-Za-z0-9_-]{16,})\b")

MODE_TO_PROFILE_PATH = {
    "regular": "profile",
    "pvp": "profile",
    "pve": "pve",
    "pvp-season": "pvp-season",
}

PROFILE_PATH_TO_MODE = {
    "profile": "regular",
    "pve": "pve",
    "pvp-season": "pvp-season",
}

TRACKER_PREFIX_TO_MODE = {
    "PVP": "regular",
    "PVE": "pve",
    "SZN": "pvp-season",
}


@dataclass
class LinkedProfile:
    account_id: str
    game_mode: str = "regular"  # regular | pve | pvp-season
    nickname: str = ""


@dataclass
class TrackerProgress:
    display_name: str = ""
    player_level: int = 1
    game_mode: str = "regular"
    completed_ids: set[str] = field(default_factory=set)
    failed_ids: set[str] = field(default_factory=set)
    # Tasks explicitly marked incomplete / in-progress in the API payload.
    tracked_incomplete_ids: set[str] = field(default_factory=set)
    objective_counts: dict[str, int] = field(default_factory=dict)
    objective_complete: dict[str, bool] = field(default_factory=dict)


def parse_profile_from_text(text: str) -> LinkedProfile | None:
    if not text:
        return None
    m = PROFILE_URL_RE.search(text)
    if m:
        if m.group("aid"):
            mode = m.group("mode") or "regular"
            if mode == "pvp":
                mode = "regular"
            return LinkedProfile(account_id=m.group("aid"), game_mode=mode)
        path = m.group("pmode") or "profile"
        return LinkedProfile(
            account_id=m.group("paid"),
            game_mode=PROFILE_PATH_TO_MODE.get(path, "regular"),
        )
    m2 = BARE_AID_RE.match(text.strip())
    if m2:
        return LinkedProfile(account_id=m2.group(1), game_mode="regular")
    return None


def parse_tracker_token(text: str) -> str | None:
    if not text:
        return None
    m = TRACKER_TOKEN_RE.search(text.strip())
    return m.group(1) if m else None


def tracker_mode_from_token(token: str) -> str:
    prefix = (token or "").split("_", 1)[0].upper()
    return TRACKER_PREFIX_TO_MODE.get(prefix, "regular")


def players_json_url(account_id: str, game_mode: str) -> str:
    path = MODE_TO_PROFILE_PATH.get(game_mode, "profile")
    return f"https://players.tarkov.dev/{path}/{account_id}.json"


def tarkov_dev_players_url() -> str:
    return "https://tarkov.dev/players"


def tarkov_tracker_settings_url() -> str:
    return "https://tarkovtracker.org/settings"


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    req_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_profile_summary(account_id: str, game_mode: str = "regular") -> LinkedProfile:
    """Fetch public tarkov.dev cached profile (no live quests in current dumps)."""
    url = players_json_url(account_id, game_mode)
    try:
        data = _http_get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                "Profile not found yet. Open your page on tarkov.dev first, then try again."
            ) from exc
        raise RuntimeError(f"Could not fetch profile ({exc.code}).") from exc
    except Exception as exc:
        raise RuntimeError(f"Could not fetch profile: {exc}") from exc

    info = data.get("info") or data.get("Info") or {}
    nick = info.get("nickname") or info.get("Nickname") or ""
    return LinkedProfile(account_id=str(account_id), game_mode=game_mode, nickname=str(nick))


def extract_started_quest_ids(profile_payload: dict) -> set[str]:
    """
    If a profile dump still includes Quests, return started / ready-to-turn-in ids.
    Status: 2=Started, 3=AvailableForFinish.
    """
    quests = profile_payload.get("Quests") or profile_payload.get("quests")
    if not isinstance(quests, list):
        # Rare nested shapes
        for key in ("pmc", "characters", "data"):
            node = profile_payload.get(key)
            if isinstance(node, dict):
                found = extract_started_quest_ids(node)
                if found:
                    return found
        return set()

    out: set[str] = set()
    for entry in quests:
        if not isinstance(entry, dict):
            continue
        qid = entry.get("qid") or entry.get("id") or ""
        try:
            status = int(entry.get("status", -1))
        except (TypeError, ValueError):
            continue
        if qid and status in (2, 3):
            out.add(str(qid))
    return out


def fetch_started_quest_ids(account_id: str, game_mode: str = "regular") -> set[str]:
    url = players_json_url(account_id, game_mode)
    data = _http_get_json(url)
    return extract_started_quest_ids(data)


def fetch_tracker_progress(token: str) -> TrackerProgress:
    url = "https://api.tarkovtracker.org/progress"
    try:
        payload = _http_get_json(
            url,
            headers={"Authorization": f"Bearer {token.strip()}"},
        )
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        if exc.code == 401:
            raise RuntimeError("Tarkov Tracker token rejected. Create a new token and copy it again.") from exc
        raise RuntimeError(f"Tarkov Tracker error ({exc.code}): {body or exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Tarkov Tracker request failed: {exc}") from exc

    if not payload.get("success", True) and "data" not in payload:
        raise RuntimeError(payload.get("error") or "Unexpected Tracker response")

    data = payload.get("data") or {}
    meta = payload.get("meta") or {}
    mode = meta.get("gameMode") or tracker_mode_from_token(token)
    if mode == "pvp":
        mode = "regular"

    completed: set[str] = set()
    failed: set[str] = set()
    tracked_incomplete: set[str] = set()
    for entry in data.get("tasksProgress") or []:
        if not isinstance(entry, dict):
            continue
        tid = str(entry.get("id") or "")
        if not tid:
            continue
        if entry.get("complete"):
            completed.add(tid)
        elif entry.get("failed"):
            failed.add(tid)
        else:
            tracked_incomplete.add(tid)

    obj_counts: dict[str, int] = {}
    obj_complete: dict[str, bool] = {}
    for entry in data.get("taskObjectivesProgress") or []:
        if not isinstance(entry, dict):
            continue
        oid = str(entry.get("id") or "")
        if not oid:
            continue
        obj_complete[oid] = bool(entry.get("complete"))
        try:
            obj_counts[oid] = int(entry.get("count") or 0)
        except (TypeError, ValueError):
            obj_counts[oid] = 0

    return TrackerProgress(
        display_name=str(data.get("displayName") or ""),
        player_level=int(data.get("playerLevel") or 1),
        game_mode=mode,
        completed_ids=completed,
        failed_ids=failed,
        tracked_incomplete_ids=tracked_incomplete,
        objective_counts=obj_counts,
        objective_complete=obj_complete,
    )


def active_ids_from_tracker(progress: TrackerProgress, task_objective_index: dict[str, str]) -> set[str]:
    """
    Prefer explicitly incomplete tracked tasks + tasks with objective progress.
    task_objective_index: objective_id -> task_id
    """
    active = set(progress.tracked_incomplete_ids)
    for oid, count in progress.objective_counts.items():
        if count > 0:
            tid = task_objective_index.get(oid)
            if tid:
                active.add(tid)
    for oid, done in progress.objective_complete.items():
        if done:
            continue
        # incomplete objective row present → treat as in-progress if counted
        if progress.objective_counts.get(oid, 0) > 0:
            tid = task_objective_index.get(oid)
            if tid:
                active.add(tid)

    active -= progress.completed_ids
    active -= progress.failed_ids
    return active
