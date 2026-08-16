"""Hover-first item recognition against the existing icon index."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from .loot_index import ItemIconIndex
from .win_capture import capture_eft_patch_bgr
from .win_input import cursor_in_eft_client

HOVER_STILL_PX = 10
HOVER_MS = 90
HIDE_MS = 280
PATCH = 176
HIGH_CONF = 0.88
SHOW_CONF = 0.74
LOCK_HITS = 2
CACHE_MAX = 220


@dataclass
class LootMatch:
    item_id: str = ""
    name: str = ""
    short_name: str = ""
    price: int = 0
    confidence: float = 0.0
    hamming: int = 99
    cache_hit: bool = False
    candidates: list[tuple[str, float, int]] = field(default_factory=list)
    crop_hash: int = 0
    latency_ms: float = 0.0
    cursor: tuple[int, int] = (0, 0)
    reason: str = ""


class HoverRecognizer:
    def __init__(self, index: ItemIconIndex):
        self.index = index
        self._last_xy: tuple[int, int] | None = None
        self._still_since = 0.0
        self._lock_id = ""
        self._lock_hits = 0
        self._cache: OrderedDict[int, tuple[str, float, float]] = OrderedDict()
        self.debug = LootMatch(reason="idle")

    def reset(self):
        self._last_xy = None
        self._still_since = 0.0
        self._lock_id = ""
        self._lock_hits = 0
        self.debug = LootMatch(reason="idle")

    def tick(self, catalog_lookup) -> LootMatch | None:
        """None = still moving / hide. LootMatch with empty id = hovered but no display."""
        t0 = time.perf_counter()
        loc = cursor_in_eft_client()
        if loc is None:
            self.reset()
            self.debug = LootMatch(reason="cursor not over Tarkov")
            return None
        _hwnd, cx, cy, _w, _h = loc
        now = time.perf_counter()
        xy = (cx, cy)
        if self._last_xy is None:
            self._last_xy = xy
            self._still_since = now
            self.debug = LootMatch(cursor=xy, reason="arming hover")
            return None
        dist = abs(cx - self._last_xy[0]) + abs(cy - self._last_xy[1])
        if dist > HOVER_STILL_PX:
            self._last_xy = xy
            self._still_since = now
            self._lock_id = ""
            self._lock_hits = 0
            self.debug = LootMatch(cursor=xy, reason="moving")
            return None
        self._last_xy = xy
        if (now - self._still_since) * 1000.0 < HOVER_MS:
            self.debug = LootMatch(cursor=xy, reason="hover wait")
            return None

        patch = capture_eft_patch_bgr(cx, cy, PATCH)
        if patch is None:
            self.debug = LootMatch(cursor=xy, reason="capture empty/black")
            return LootMatch(cursor=xy, reason="capture empty/black")

        crop = _center_icon(patch)
        from .loot_index import dhash64, _prepare_gray

        ch = dhash64(_prepare_gray(crop))
        cached = self._cache.get(ch)
        if cached and (now - cached[2]) < 8.0:
            item_id, conf, _ts = cached
            item = catalog_lookup(item_id)
            self.debug = LootMatch(
                item_id=item_id,
                name=getattr(item, "name", "") if item else "",
                short_name=getattr(item, "short_name", "") if item else "",
                price=int(getattr(item, "best_price", 0) or 0) if item else 0,
                confidence=conf,
                cache_hit=True,
                crop_hash=ch,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                cursor=xy,
                reason="cache",
            )
            return self.debug if conf >= SHOW_CONF else LootMatch(cursor=xy, reason="cache low")

        cands = self.index.query(crop, top_k=8)
        best_id, best_ncc, ham = ("", 0.0, 99)
        if cands:
            best_id, best_ncc, ham = cands[0]
        conf = float(best_ncc)
        if ham > 22:
            conf *= 0.82
        if ham > 28:
            conf *= 0.5

        if best_id and conf >= SHOW_CONF:
            if best_id == self._lock_id:
                self._lock_hits += 1
            else:
                self._lock_id = best_id
                self._lock_hits = 1
            if conf < HIGH_CONF and self._lock_hits < LOCK_HITS:
                self.debug = LootMatch(
                    item_id=best_id,
                    confidence=conf,
                    hamming=ham,
                    candidates=cands[:5],
                    crop_hash=ch,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    cursor=xy,
                    reason="await lock",
                )
                return LootMatch(cursor=xy, reason="await lock")
            self._remember(ch, best_id, conf, now)
            item = catalog_lookup(best_id)
            match = LootMatch(
                item_id=best_id,
                name=getattr(item, "name", best_id) if item else best_id,
                short_name=getattr(item, "short_name", "") if item else "",
                price=int(getattr(item, "best_price", 0) or 0) if item else 0,
                confidence=conf,
                hamming=ham,
                candidates=cands[:5],
                crop_hash=ch,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                cursor=xy,
                reason="match",
            )
            self.debug = match
            return match

        self._lock_id = ""
        self._lock_hits = 0
        self.debug = LootMatch(
            confidence=conf,
            hamming=ham,
            candidates=cands[:5],
            crop_hash=ch,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            cursor=xy,
            reason="low confidence" if cands else "no candidates",
        )
        return LootMatch(cursor=xy, reason=self.debug.reason)

    def _remember(self, ch: int, item_id: str, conf: float, now: float):
        self._cache[ch] = (item_id, conf, now)
        self._cache.move_to_end(ch)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)


def _center_icon(patch: np.ndarray) -> np.ndarray:
    h, w = patch.shape[:2]
    side = min(h, w)
    # Tarkov tooltips sit below/right of the cursor; the icon is near the center of the patch.
    x0 = max(0, (w - side) // 2)
    y0 = max(0, (h - side) // 2)
    crop = patch[y0 : y0 + side, x0 : x0 + side]
    # Slightly tighter inner crop removes hover glow/borders.
    m = max(2, side // 12)
    if side - 2 * m >= 24:
        crop = crop[m : side - m, m : side - m]
    return crop
