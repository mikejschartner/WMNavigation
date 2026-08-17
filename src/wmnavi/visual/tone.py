"""Tone curve + preview filter. Shared by the display LUT and the Original|Filtered preview."""

from __future__ import annotations

import math

import numpy as np

from .profiles import VisualSettings

_REF_TEMP_RGB: tuple[float, float, float] | None = None


def kelvin_to_rgb(kelvin: float) -> tuple[float, float, float]:
    k = max(1000.0, min(40000.0, float(kelvin))) / 100.0
    if k <= 66:
        red = 255.0
        green = np.clip(99.4708 * math.log(k) - 161.1196, 0.0, 255.0)
        blue = 0.0 if k <= 19 else np.clip(138.5177 * math.log(k - 10.0) - 305.0448, 0.0, 255.0)
    else:
        red = np.clip(329.6987 * ((k - 60.0) ** -0.13320476), 0.0, 255.0)
        green = np.clip(288.1222 * ((k - 60.0) ** -0.07551485), 0.0, 255.0)
        blue = 255.0
    return (red / 255.0, green / 255.0, blue / 255.0)


def _temp_scale(kelvin: int) -> tuple[float, float, float]:
    global _REF_TEMP_RGB
    if _REF_TEMP_RGB is None:
        _REF_TEMP_RGB = kelvin_to_rgb(6500)
    ref = _REF_TEMP_RGB
    rgb = kelvin_to_rgb(kelvin)
    return (
        (rgb[0] / max(ref[0], 1e-6)),
        (rgb[1] / max(ref[1], 1e-6)),
        (rgb[2] / max(ref[2], 1e-6)),
    )


def tone_curve_1d(settings: VisualSettings, samples: int = 256) -> np.ndarray:
    """0..1 luminance curve used by the per-channel display LUT."""
    x = np.linspace(0.0, 1.0, samples, dtype=np.float64)
    return _apply_tone(x, settings)


def _apply_tone(x: np.ndarray, settings: VisualSettings) -> np.ndarray:
    y = np.clip(x.astype(np.float64), 0.0, 1.0)
    y *= 2.0 ** float(settings.exposure)
    black = min(0.45, max(0.0, float(settings.black_level)))
    if black > 1e-6:
        y = (y - black) / max(1.0 - black, 1e-6)
    y = np.clip(y, 0.0, 4.0)

    shadow = min(1.0, max(0.0, float(settings.shadow_boost)))
    if shadow > 1e-6:
        lift = (1.0 - np.clip(y / 0.38, 0.0, 1.0)) ** 2 * shadow * 0.85
        y = y + lift

    hi = min(1.0, max(0.0, float(settings.highlight_reduction)))
    if hi > 1e-6:
        t = np.clip((y - 0.52) / 0.48, 0.0, 1.0) * hi
        compressed = y / (1.0 + y * (0.65 + hi))
        y = y * (1.0 - t) + compressed * t

    gamma = min(5.0, max(0.30, float(settings.gamma)))
    y = np.power(np.clip(y, 0.0, 8.0), 1.0 / gamma)

    contrast = min(3.0, max(0.20, float(settings.contrast)))
    y = (y - 0.5) * contrast + 0.5
    y = y + float(settings.brightness)
    return np.clip(y, 0.0, 1.0)


def build_gamma_ramp(settings: VisualSettings) -> tuple[list[int], list[int], list[int]]:
    """256-entry 0..65535 ramps for SetDeviceGammaRamp."""
    if settings.is_identity():
        linear = [min(65535, i * 257) for i in range(256)]
        return linear, linear[:], linear[:]
    curve = tone_curve_1d(settings, 256)
    tr, tg, tb = _temp_scale(int(settings.temperature))
    red = [int(round(min(65535.0, max(0.0, v * tr * 65535.0)))) for v in curve]
    green = [int(round(min(65535.0, max(0.0, v * tg * 65535.0)))) for v in curve]
    blue = [int(round(min(65535.0, max(0.0, v * tb * 65535.0)))) for v in curve]
    return red, green, blue


def apply_preview_bgr(bgr: np.ndarray, settings: VisualSettings) -> np.ndarray:
    """CPU preview of the live tone curve plus saturation/sharpness."""
    if bgr is None or bgr.size == 0:
        return bgr
    img = bgr.astype(np.float32) / 255.0
    if not settings.is_identity() or settings.needs_spatial():
        for c, scale in enumerate(_temp_scale(int(settings.temperature))):
            channel = _apply_tone(img[:, :, c], settings).astype(np.float32) * scale
            img[:, :, c] = channel
        sat = min(2.5, max(0.0, float(settings.saturation)))
        if abs(sat - 1.0) > 1e-4:
            luma = img[:, :, 2] * 0.2126 + img[:, :, 1] * 0.7152 + img[:, :, 0] * 0.0722
            for c in range(3):
                img[:, :, c] = luma + (img[:, :, c] - luma) * sat
        sharp = min(1.0, max(0.0, float(settings.sharpness)))
        if sharp > 1e-4:
            blur = img.copy()
            blur[1:-1, 1:-1] = (
                img[:-2, 1:-1] + img[2:, 1:-1] + img[1:-1, :-2] + img[1:-1, 2:]
            ) * 0.25
            img = np.clip(img + (img - blur) * (sharp * 2.2), 0.0, 1.0)
    return np.clip(img * 255.0, 0, 255).astype(np.uint8)


def identity_ramp() -> tuple[list[int], list[int], list[int]]:
    linear = [min(65535, i * 257) for i in range(256)]
    return linear, linear[:], linear[:]
