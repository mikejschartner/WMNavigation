"""Find Tarkov's hover name chip anywhere around the cursor and read it with Windows OCR."""

from __future__ import annotations

import asyncio

import numpy as np

from .win_capture import capture_around_cursor_bgr

_engine = None
_loop: asyncio.AbstractEventLoop | None = None
_engine_failed = False


def warmup_ocr() -> bool:
    return _ocr_engine() is not None


def ocr_available() -> bool:
    return _ocr_engine() is not None


def read_tooltip_name(cx: int, cy: int, client_w: int, client_h: int) -> tuple[str, float]:
    texts = read_tooltip_texts(cx, cy, client_w, client_h)
    if not texts:
        return "", 0.0
    return texts[0]


def read_tooltip_texts(cx: int, cy: int, client_w: int, client_h: int) -> list[tuple[str, float]]:
    """Every readable name-chip string near the cursor (any side)."""
    frame = capture_around_cursor_bgr(cx, cy, client_w, client_h)
    if frame is None:
        return []
    engine = _ocr_engine()
    if engine is None:
        return []
    chips = find_name_chips(frame)
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for crop, score in (chips or [])[:8]:
        text = _ocr_image(engine, crop)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((text, score))
    if not out:
        text = _ocr_image(engine, frame)
        if text:
            out.append((text, 0.3))
    return out


def find_title_bands(bgr: np.ndarray) -> list[tuple[np.ndarray, float]]:
    return find_name_chips(bgr)


def find_name_chips(bgr: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """Black name chips with white text — position does not matter."""
    import cv2

    if bgr is None or bgr.size == 0:
        return []
    h, w = bgr.shape[:2]
    if h < 16 or w < 40:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    chips: list[tuple[np.ndarray, float, tuple[int, int, int, int]]] = []
    chips.extend(_chips_from_edges(bgr, gray))
    chips.extend(_chips_from_dark_fill(bgr, gray))
    # Dedup overlapping boxes; keep the higher score.
    chips.sort(key=lambda item: item[1], reverse=True)
    kept: list[tuple[np.ndarray, float]] = []
    used: list[tuple[int, int, int, int]] = []
    for crop, score, box in chips:
        if any(_overlap(box, other) > 0.45 for other in used):
            continue
        used.append(box)
        kept.append((crop, score))
        if len(kept) >= 8:
            break
    if kept:
        return kept
    return _fallback_text_strip(bgr, gray)


def _chips_from_edges(bgr: np.ndarray, gray: np.ndarray) -> list[tuple[np.ndarray, float, tuple[int, int, int, int]]]:
    import cv2

    edges = cv2.Canny(gray, 30, 100)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return _chips_from_contours(bgr, gray, contours, score_bonus=0.08)


def _chips_from_dark_fill(bgr: np.ndarray, gray: np.ndarray) -> list[tuple[np.ndarray, float, tuple[int, int, int, int]]]:
    import cv2

    dark = (gray < 38).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
    closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return _chips_from_contours(bgr, gray, contours, score_bonus=0.0)


def _chips_from_contours(bgr, gray, contours, score_bonus: float):
    h, w = gray.shape[:2]
    found = []
    for cnt in contours:
        x, y, bw, bh = cv2_bounding(cnt)
        if bw < 44 or bh < 12 or bh > 78:
            continue
        if bw < bh * 1.35:
            continue
        if bw > w * 0.98 and bh > h * 0.8:
            continue
        inset = 2 if bh >= 18 and bw >= 50 else 1
        xa, ya = x + inset, y + inset
        xb, yb = x + bw - inset, y + bh - inset
        if xb - xa < 32 or yb - ya < 8:
            continue
        inner = gray[ya:yb, xa:xb]
        if inner.size == 0:
            continue
        if float(np.mean(inner)) > 58:
            continue
        bright = float(np.mean(inner > 145))
        if bright < 0.014 or bright > 0.62:
            continue
        crop = bgr[ya:yb, xa:xb]
        score = 0.4 + min(0.35, bw / 380.0) + min(0.2, bright * 3.0) + score_bonus
        found.append((crop, score, (x, y, bw, bh)))
    return found


def cv2_bounding(cnt):
    import cv2

    return cv2.boundingRect(cnt)


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    area = min(aw * ah, bw * bh) or 1
    return inter / area


def _fallback_text_strip(bgr: np.ndarray, gray: np.ndarray) -> list[tuple[np.ndarray, float]]:
    h, w = gray.shape[:2]
    if h < 14 or w < 40:
        return []
    bright = gray > 150
    if float(np.mean(bright)) < 0.008:
        return []
    ys = np.where(bright.any(axis=1))[0]
    xs = np.where(bright.any(axis=0))[0]
    if ys.size == 0 or xs.size == 0:
        return []
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    x0, x1 = int(xs[0]), int(xs[-1]) + 1
    if y1 - y0 > 80:
        # Too tall — likely whole stash. Keep a band of white rows near mid/top.
        row_counts = bright.sum(axis=1)
        peak = int(np.argmax(row_counts))
        y0, y1 = max(0, peak - 18), min(h, peak + 18)
    pad = 6
    y0, x0 = max(0, y0 - pad), max(0, x0 - pad)
    y1, x1 = min(h, y1 + pad), min(w, x1 + pad)
    if y1 - y0 < 12 or x1 - x0 < 36:
        return []
    return [(bgr[y0:y1, x0:x1], 0.35)]


def _ocr_engine():
    global _engine, _engine_failed
    if _engine_failed:
        return None
    if _engine is not None:
        return _engine
    try:
        from winrt.windows.media.ocr import OcrEngine

        eng = OcrEngine.try_create_from_user_profile_languages()
        if eng is None:
            _engine_failed = True
            return None
        _engine = eng
        return _engine
    except Exception:
        _engine_failed = True
        return None


def _ocr_image(engine, bgr: np.ndarray) -> str:
    import cv2
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    if bgr is None or bgr.size == 0:
        return ""
    h, w = bgr.shape[:2]
    if h < 10 or w < 24:
        return ""
    pad = 8
    padded = cv2.copyMakeBorder(bgr, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(8, 8, 8))
    scale = 3.0 if h < 36 else 2.0
    up = cv2.resize(padded, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    bgra = np.ascontiguousarray(cv2.cvtColor(up, cv2.COLOR_BGR2BGRA))
    uh, uw = bgra.shape[:2]
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    buf = writer.detach_buffer()
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, uw, uh, BitmapAlphaMode.IGNORE)
    SoftwareBitmap.copy_from_buffer(bmp, buf)

    async def _run():
        result = await engine.recognize_async(bmp)
        return str(result.text or "").strip()

    try:
        text = _run_async(_run())
    except Exception:
        return ""
    return " ".join(text.split())


def _run_async(coro) -> str:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coro)
