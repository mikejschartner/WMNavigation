"""Hover-first item recognition against the existing icon index."""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from .loot_index import ItemIconIndex
from .loot_names import ItemNameIndex
from .loot_tooltip import read_tooltip_name
from .win_capture import capture_eft_patch_bgr
from .win_input import cursor_in_eft_client

HOVER_STILL_PX = 12
HOVER_MS = 70
HIDE_MS = 450
HOLD_PX = 54  # ~one Tarkov stash cell — keep the last find while the tooltip covers the icon
PATCH = 176
HIGH_CONF = 0.86
SHOW_CONF = 0.70
NAME_CONF = 0.86
OCR_EVERY = 0.22
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
    status: str = "away"  # away | searching | identifying | found | no_match
    ocr_text: str = ""
    source: str = ""  # name | picture | cache


class HoverRecognizer:
    def __init__(self, index: ItemIconIndex, names: ItemNameIndex | None = None):
        self.index = index
        self.names = names or ItemNameIndex()
        self._last_xy: tuple[int, int] | None = None
        self._still_since = 0.0
        self._held: LootMatch | None = None
        self._held_xy: tuple[int, int] | None = None
        self._cache: OrderedDict[int, tuple[str, float, float]] = OrderedDict()
        self._last_ocr_at = 0.0
        self._client_wh = (0, 0)
        self.debug = LootMatch(reason="idle", status="away")

    def reset(self):
        self._last_xy = None
        self._still_since = 0.0
        self._held = None
        self._held_xy = None
        self._last_ocr_at = 0.0
        self.debug = LootMatch(reason="idle", status="away")

    def tick(self, catalog_lookup) -> LootMatch | None:
        """None = cursor left Tarkov. Otherwise always a displayable status match."""
        t0 = time.perf_counter()
        loc = cursor_in_eft_client()
        if loc is None:
            self.reset()
            self.debug = LootMatch(reason="cursor not over Tarkov", status="away")
            return None
        _hwnd, cx, cy, client_w, client_h = loc
        now = time.perf_counter()
        xy = (cx, cy)
        self._client_wh = (int(client_w), int(client_h))

        if self._held and self._held_xy:
            if abs(cx - self._held_xy[0]) + abs(cy - self._held_xy[1]) <= HOLD_PX:
                if self._held.source != "name":
                    named = self._try_name(xy, cx, cy, t0, catalog_lookup)
                    if named is not None:
                        return named
                held = self._held
                held.cursor = xy
                self._last_xy = xy
                self.debug = held
                return held
            self._held = None
            self._held_xy = None

        if self._last_xy is None:
            self._last_xy = xy
            self._still_since = now
            return self._status(xy, "searching", "arming hover", t0)
        dist = abs(cx - self._last_xy[0]) + abs(cy - self._last_xy[1])
        if dist > HOVER_STILL_PX:
            self._last_xy = xy
            self._still_since = now
            return self._status(xy, "searching", "moving", t0)
        self._last_xy = xy
        if (now - self._still_since) * 1000.0 < HOVER_MS:
            return self._status(xy, "searching", "hover wait", t0)

        return self._identify(xy, cx, cy, now, t0, catalog_lookup)

    def _try_name(self, xy, cx, cy, t0: float, catalog_lookup) -> LootMatch | None:
        if not self.names or not self.names._entries:
            return None
        now = time.perf_counter()
        if now - self._still_since < 0.22:
            return None
        if now - self._last_ocr_at < OCR_EVERY:
            return None
        self._last_ocr_at = now
        cw, ch = self._client_wh
        text, _band = read_tooltip_name(cx, cy, cw, ch)
        if not text:
            return None
        hit = self.names.lookup(text)
        if not hit or hit[1] < NAME_CONF:
            return None
        item_id, score, why = hit
        return self._hold(
            xy,
            item_id,
            score,
            0,
            [],
            0,
            t0,
            catalog_lookup,
            cache=False,
            source="name",
            reason=f"name {why}",
            ocr_text=text,
        )

    def _status(self, xy, status: str, reason: str, t0: float) -> LootMatch:
        m = LootMatch(
            cursor=xy,
            reason=reason,
            status=status,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )
        self.debug = m
        return m

    def _identify(self, xy, cx, cy, now: float, t0: float, catalog_lookup) -> LootMatch:
        named = self._try_name(xy, cx, cy, t0, catalog_lookup)
        if named is not None:
            return named

        patch = capture_eft_patch_bgr(cx, cy, PATCH)
        if patch is None:
            return self._status(xy, "searching", "capture empty/black", t0)

        crop = _center_icon(patch)
        from .loot_index import dhash64, _prepare_gray

        ch = dhash64(_prepare_gray(crop))
        cached = self._cache.get(ch)
        if cached and (now - cached[2]) < 8.0:
            item_id, conf, _ts = cached
            if conf >= SHOW_CONF:
                return self._hold(xy, item_id, conf, 0, [], ch, t0, catalog_lookup, cache=True)

        identifying = self._status(xy, "identifying", "matching picture", t0)
        cands = self.index.query(crop, top_k=8)
        best_id, best_ncc, ham = ("", 0.0, 99)
        if cands:
            best_id, best_ncc, ham = cands[0]
        conf = float(best_ncc)
        if ham > 22:
            conf *= 0.82
        if ham > 28:
            conf *= 0.5
        identifying.candidates = cands[:5]
        identifying.confidence = conf
        identifying.hamming = ham

        if best_id and conf >= SHOW_CONF:
            self._remember(ch, best_id, conf, now)
            return self._hold(xy, best_id, conf, ham, cands[:5], ch, t0, catalog_lookup, cache=False)

        identifying.status = "no_match"
        identifying.reason = "low confidence" if cands else "waiting for name tooltip"
        self.debug = identifying
        return identifying

    def _hold(
        self,
        xy,
        item_id: str,
        conf: float,
        ham: int,
        cands,
        ch: int,
        t0: float,
        catalog_lookup,
        *,
        cache: bool,
        source: str = "",
        reason: str = "",
        ocr_text: str = "",
    ) -> LootMatch:
        item = catalog_lookup(item_id)
        if cache:
            src = "cache"
            why = "cache"
        elif source == "name":
            src = "name"
            why = reason or "name"
        else:
            src = "picture"
            why = "picture"
        match = LootMatch(
            item_id=item_id,
            name=getattr(item, "name", item_id) if item else item_id,
            short_name=getattr(item, "short_name", "") if item else "",
            price=int(getattr(item, "best_price", 0) or 0) if item else 0,
            confidence=conf,
            hamming=ham,
            cache_hit=cache,
            candidates=cands,
            crop_hash=ch,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            cursor=xy,
            reason=why,
            status="found",
            ocr_text=ocr_text,
            source=src,
        )
        self._held = match
        self._held_xy = xy
        self.debug = match
        return match

    def _remember(self, ch: int, item_id: str, conf: float, now: float):
        self._cache[ch] = (item_id, conf, now)
        self._cache.move_to_end(ch)
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)


def _center_icon(patch: np.ndarray) -> np.ndarray:
    h, w = patch.shape[:2]
    side = min(h, w)
    x0 = max(0, (w - side) // 2)
    y0 = max(0, (h - side) // 2)
    crop = patch[y0 : y0 + side, x0 : x0 + side]
    m = max(2, side // 12)
    if side - 2 * m >= 24:
        crop = crop[m : side - m, m : side - m]
    return crop
