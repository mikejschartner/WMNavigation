"""Match OCR / tooltip text to catalog item names, then use existing prices."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import ItemInfo

_NON_NAME = re.compile(r"[^a-z0-9+./'x -]+")
_SPACE = re.compile(r"\s+")
_JUNK = {
    "found in raid",
    "fir",
    "inspect",
    "use",
    "drop",
    "equip",
    "durability",
    "item",
    "loot",
    "value",
    "kg",
    "hp",
    "searching",
}


def normalize_item_name(text: str) -> str:
    t = (text or "").lower().replace("\u2019", "'").replace("×", "x")
    t = _NON_NAME.sub(" ", t)
    return _SPACE.sub(" ", t).strip()


def _word_overlap(a: str, b: str) -> bool:
    aw = {w for w in a.split() if len(w) >= 4}
    bw = {w for w in b.split() if len(w) >= 4}
    return bool(aw & bw)


class ItemNameIndex:
    def __init__(self, catalog: dict[str, ItemInfo] | None = None):
        self.by_name: dict[str, list[str]] = {}
        self.by_short: dict[str, list[str]] = {}
        self._entries: list[tuple[str, str, str]] = []  # norm, id, kind
        if catalog:
            self.build(catalog)

    def build(self, catalog: dict[str, ItemInfo]) -> int:
        self.by_name = {}
        self.by_short = {}
        self._entries = []
        for item in catalog.values():
            iid = item.id
            name_n = normalize_item_name(item.name)
            short_n = normalize_item_name(item.short_name)
            if name_n and name_n not in _JUNK:
                self.by_name.setdefault(name_n, []).append(iid)
                self._entries.append((name_n, iid, "name"))
            if short_n and short_n not in _JUNK and short_n != name_n:
                self.by_short.setdefault(short_n, []).append(iid)
                self._entries.append((short_n, iid, "short"))
        return len(self._entries)

    def lookup(self, text: str) -> tuple[str, float, str] | None:
        """Return (item_id, score, reason) or None."""
        best: tuple[str, float, str] | None = None
        for chunk in _chunks(text):
            hit = self._lookup_one(chunk)
            if hit and (best is None or hit[1] > best[1]):
                best = hit
        return best

    def _lookup_one(self, q: str) -> tuple[str, float, str] | None:
        q = normalize_item_name(q)
        if len(q) < 3 or q in _JUNK:
            return None
        if q in self.by_name:
            ids = self.by_name[q]
            if len(ids) == 1:
                return ids[0], 1.0, "exact"
            return ids[0], 0.9, "exact-ambiguous"
        if q in self.by_short:
            ids = self.by_short[q]
            if len(ids) == 1:
                return ids[0], 0.97, "short"

        scored: list[tuple[float, str, str]] = []
        prefix = q[:4] if len(q) >= 4 else q[:3]
        for norm, iid, kind in self._entries:
            if len(norm) < 3:
                continue
            contained = (len(norm) >= 8 and norm in q) or (len(q) >= 8 and q in norm)
            if not contained and prefix not in norm and not _word_overlap(q, norm):
                continue
            if contained:
                ratio = 0.94 if norm in q or q in norm else 0.0
            else:
                ratio = SequenceMatcher(None, q, norm).ratio()
            if ratio >= 0.86:
                scored.append((ratio, iid, kind))
        if not scored:
            return None
        scored.sort(reverse=True)
        top_r, top_id, top_kind = scored[0]
        second = scored[1][0] if len(scored) > 1 else 0.0
        if top_r < 0.92 and (top_r - second) < 0.08:
            return None
        if top_kind == "short" and top_r < 0.95:
            return None
        return top_id, float(top_r), f"fuzzy-{top_kind}"


def _chunks(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    out = [raw.replace("\n", " ")]
    out.extend(lines[:4])
    if len(lines) >= 2:
        out.append(" ".join(lines[:2]))
    seen = set()
    uniq = []
    for item in out:
        key = normalize_item_name(item)
        if key and key not in seen:
            seen.add(key)
            uniq.append(item)
    return uniq
