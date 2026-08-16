"""Find Tarkov's hover name tooltip and read the item name with Windows OCR."""

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
    """Return (ocr_text, band_score). Empty text if the name box is not readable yet."""
    frame = capture_eft_tooltip_bgr(cx, cy, client_w, client_h)
    if frame is None:
        return "", 0.0
    bands = find_title_bands(frame)
    if not bands:
        return "", 0.0
    engine = _ocr_engine()
    if engine is None:
        return "", 0.0
    best_text = ""
    best_score = 0.0
    for crop, score in bands[:3]:
        text = _ocr_image(engine, crop)
        if text and score >= best_score:
            best_text = text
            best_score = score
    return best_text, best_score


def find_title_bands(bgr: np.ndarray) -> list[tuple[np.ndarray, float]]:
    """Dark UI panels with a bright title strip — Tarkov hover tooltips."""
    import cv2

    if bgr is None or bgr.size == 0:
        return []
    h, w = bgr.shape[:2]
    if h < 40 or w < 80:
        return []
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    dark = (gray < 58).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 13))
    closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bands: list[tuple[np.ndarray, float]] = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw < 150 or bh < 28 or bw > w * 0.98:
            continue
        if bh > h * 0.9 and bw > w * 0.9:
            continue
        title_h = min(72, max(28, int(bh * 0.34)))
        y1 = y + title_h
        pad = 4
        xa, ya = max(0, x - pad), max(0, y - pad)
        xb, yb = min(w, x + bw + pad), min(h, y1 + pad)
        crop = bgr[ya:yb, xa:xb]
        if crop.size == 0:
            continue
        cg = gray[ya:yb, xa:xb]
        bright = float(np.mean(cg > 140))
        if bright < 0.012 or bright > 0.45:
            continue
        # Title text sits on a dark header.
        if float(np.mean(cg)) > 90:
            continue
        score = min(1.0, bw / 280.0) * 0.5 + min(1.0, bright * 8.0) * 0.5
        # Prefer panels offset from the cursor (left-center of this crop).
        if x > 40:
            score += 0.08
        bands.append((crop, score))
    bands.sort(key=lambda item: item[1], reverse=True)
    return bands[:4]


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

    if bgr.shape[0] < 18 or bgr.shape[1] < 40:
        return ""
    up = cv2.resize(bgr, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    bgra = np.ascontiguousarray(cv2.cvtColor(up, cv2.COLOR_BGR2BGRA))
    h, w = bgra.shape[:2]
    writer = DataWriter()
    writer.write_bytes(bgra.tobytes())
    buf = writer.detach_buffer()
    bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.IGNORE)
    SoftwareBitmap.copy_from_buffer(bmp, buf)

    async def _run():
        result = await engine.recognize_async(bmp)
        return str(result.text or "").strip()

    try:
        return _run_async(_run())
    except Exception:
        return ""


def _run_async(coro) -> str:
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coro)


def ocr_available() -> bool:
    return _ocr_engine() is not None
