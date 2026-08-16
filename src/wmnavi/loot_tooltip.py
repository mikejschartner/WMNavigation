"""Find Tarkov's hover name chip (above-right of cursor) and read it with Windows OCR."""

from __future__ import annotations

import asyncio

import numpy as np

from .win_capture import capture_eft_tooltip_bgr

_engine = None
_loop: asyncio.AbstractEventLoop | None = None
_engine_failed = False


def warmup_ocr() -> bool:
    return _ocr_engine() is not None


def read_tooltip_name(cx: int, cy: int, client_w: int, client_h: int) -> tuple[str, float]:
    """Return (ocr_text, score). Empty text if the name chip is not readable yet."""
    frame = capture_eft_tooltip_bgr(cx, cy, client_w, client_h)
    if frame is None:
        return "", 0.0
    chips = find_name_chips(frame)
    engine = _ocr_engine()
    if engine is None:
        return "", 0.0
    best_text = ""
    best_score = 0.0
    for crop, score in (chips or [])[:3]:
        text = _ocr_image(engine, crop)
        if not text:
            continue
        if score >= best_score:
            best_text = text
            best_score = score
    if best_text:
        return best_text, best_score
    # ROI is already the above-right strip — OCR it if no border was found.
    text = _ocr_image(engine, frame)
    return text, 0.35 if text else 0.0


def find_title_bands(bgr: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """Back-compat alias used by tests."""
    return find_name_chips(bgr)


def find_name_chips(bgr: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """Stash hover chip: black fill, thin gray border, one line of white text."""
    import cv2

    if bgr is None or bgr.size == 0:
        return []
    h, w = bgr.shape[:2]
    if h < 16 or w < 40:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # Light-gray 1px outline on a dark stash background.
    edges = cv2.Canny(gray, 35, 110)
    edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    chips: list[tuple[np.ndarray, float]] = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw < 48 or bh < 14 or bh > 72:
            continue
        if bw < bh * 1.5:
            continue
        if bw > w * 0.98 and bh > h * 0.85:
            continue
        inset = 2 if bh >= 20 and bw >= 60 else 1
        xa, ya = x + inset, y + inset
        xb, yb = x + bw - inset, y + bh - inset
        if xb - xa < 36 or yb - ya < 10:
            continue
        inner = gray[ya:yb, xa:xb]
        if inner.size == 0:
            continue
        mean = float(np.mean(inner))
        if mean > 55:
            continue
        bright = float(np.mean(inner > 150))
        if bright < 0.018 or bright > 0.55:
            continue
        # Border itself should be lighter than the fill.
        ring = gray[max(0, y) : min(h, y + bh), max(0, x) : min(w, x + bw)]
        if ring.size and float(np.max(ring)) < 80:
            continue
        crop = bgr[ya:yb, xa:xb]
        score = 0.45 + min(0.35, bw / 400.0) + min(0.2, bright * 3.0)
        chips.append((crop, score))
    chips.sort(key=lambda item: item[1], reverse=True)
    if chips:
        return chips[:4]
    return _fallback_text_strip(bgr, gray)


def _fallback_text_strip(bgr: np.ndarray, gray: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """If the gray border is too thin to contour, keep the darkest strip with white glyphs."""
    h, w = gray.shape[:2]
    if h < 14 or w < 40:
        return []
    dark = gray < 40
    bright = gray > 150
    if float(np.mean(dark)) < 0.08 or float(np.mean(bright)) < 0.01:
        return []
    ys = np.where(bright.any(axis=1))[0]
    xs = np.where(bright.any(axis=0))[0]
    if ys.size == 0 or xs.size == 0:
        return []
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    x0, x1 = int(xs[0]), int(xs[-1]) + 1
    pad = 4
    y0, x0 = max(0, y0 - pad), max(0, x0 - pad)
    y1, x1 = min(h, y1 + pad), min(w, x1 + pad)
    if y1 - y0 < 12 or x1 - x0 < 36:
        return []
    crop = bgr[y0:y1, x0:x1]
    return [(crop, 0.4)]


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
    # Pad so OCR has margin; upscale the tiny stash chip.
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


def ocr_available() -> bool:
    return _ocr_engine() is not None
