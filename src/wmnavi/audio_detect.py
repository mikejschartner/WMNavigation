"""Gunshot detection, stereo direction, and shot clustering (no UI / no capture)."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np

from .heading import shortest_delta, wrap_deg

SHOT_LIFETIME_S = 1.65
CLUSTER_DT_S = 0.95
CLUSTER_DEG = 28.0
MAX_ACTIVE = 4


@dataclass
class ShotEvent:
    t0: float
    rel_deg: float
    gunshot_prob: float
    dir_conf: float
    world_bearing: float | None = None
    latency_ms: float = 0.0
    count: int = 1
    last_t: float = 0.0
    hemisphere: str = "unknown"  # unknown | front | rear


@dataclass
class DetectDebug:
    rms_l: float = 0.0
    rms_r: float = 0.0
    peak: float = 0.0
    crest: float = 0.0
    high_ratio: float = 0.0
    onset: float = 0.0
    gunshot_prob: float = 0.0
    rel_deg: float = 0.0
    dir_conf: float = 0.0
    ild_db: float = 0.0
    itd_ms: float = 0.0
    analyze_ms: float = 0.0


def _band_energy(spec: np.ndarray, freqs: np.ndarray, lo: float, hi: float) -> float:
    mask = (freqs >= lo) & (freqs < hi)
    if not np.any(mask):
        return 0.0
    return float(np.sum(spec[mask]))


def ild_to_deg(rms_l: float, rms_r: float) -> tuple[float, float, float]:
    """Map stereo level difference to view-relative degrees. Positive = right."""
    eps = 1e-9
    ild_db = 10.0 * math.log10((rms_r * rms_r + eps) / (rms_l * rms_l + eps))
    # ±10 dB covers most game-mix panning; clip beyond that.
    deg = max(-90.0, min(90.0, ild_db * (90.0 / 10.0)))
    conf = min(1.0, abs(ild_db) / 8.0)
    if abs(ild_db) < 1.2:
        conf *= 0.45
        deg *= 0.4
    return deg, conf, ild_db


def itd_to_deg(left: np.ndarray, right: np.ndarray, sr: int) -> tuple[float, float, float]:
    """GCC-PHAT delay → rough azimuth. Positive delay (right earlier) = right."""
    n = int(min(len(left), len(right)))
    if n < 64:
        return 0.0, 0.0, 0.0
    left = left[:n] * np.hanning(n)
    right = right[:n] * np.hanning(n)
    L = np.fft.rfft(left)
    R = np.fft.rfft(right)
    x = L * np.conj(R)
    x /= np.abs(x) + 1e-9
    corr = np.fft.irfft(x, n)
    corr = np.fft.fftshift(corr)
    max_lag = max(4, int(0.00075 * sr))
    mid = n // 2
    window = corr[mid - max_lag : mid + max_lag + 1]
    lag = int(np.argmax(window)) - max_lag
    itd_s = lag / float(sr)
    # Interaural path ~0.66 ms at 90°.
    span = 0.00066
    deg = max(-90.0, min(90.0, (itd_s / span) * 90.0))
    peak = float(np.max(window))
    conf = min(1.0, max(0.0, (peak - 0.12) / 0.5))
    if abs(lag) < 2:
        conf *= 0.5
    return deg, conf, itd_s * 1000.0


def detect_gunshot(left: np.ndarray, right: np.ndarray, sr: int, noise_rms: float) -> tuple[float, DetectDebug]:
    """Return gunshot probability 0..1 and debug features."""
    t0 = time.perf_counter()
    dbg = DetectDebug()
    if left.size < 32 or right.size < 32:
        return 0.0, dbg
    stereo = np.stack([left.astype(np.float64), right.astype(np.float64)], axis=0)
    peak = float(np.max(np.abs(stereo)))
    rms_l = float(np.sqrt(np.mean(left * left) + 1e-12))
    rms_r = float(np.sqrt(np.mean(right * right) + 1e-12))
    rms = math.sqrt(0.5 * (rms_l * rms_l + rms_r * rms_r))
    crest = peak / (rms + 1e-9)
    mix = 0.5 * (left + right)
    spec = np.abs(np.fft.rfft(mix * np.hanning(len(mix)))) ** 2
    freqs = np.fft.rfftfreq(len(mix), 1.0 / sr)
    total = float(np.sum(spec) + 1e-12)
    high = _band_energy(spec, freqs, 2200.0, 9000.0)
    low = _band_energy(spec, freqs, 80.0, 400.0)
    voice = _band_energy(spec, freqs, 300.0, 1400.0)
    high_ratio = high / total
    onset = rms / (noise_rms + 1e-4)
    # Gunshots: sharp crest, strong high-mid crack, not rumble-dominated, not speech-like.
    score = 0.0
    if peak > 0.045:
        score += 0.18
    if crest > 5.5:
        score += min(0.28, (crest - 5.5) * 0.04)
    if high_ratio > 0.22:
        score += min(0.28, (high_ratio - 0.18) * 0.9)
    if onset > 3.2:
        score += min(0.22, (onset - 3.0) * 0.06)
    if low / total > 0.55:
        score -= 0.25
    if voice / total > 0.42 and high_ratio < 0.28:
        score -= 0.2
    if crest < 3.5:
        score -= 0.2
    prob = max(0.0, min(1.0, score))
    dbg.rms_l = rms_l
    dbg.rms_r = rms_r
    dbg.peak = peak
    dbg.crest = crest
    dbg.high_ratio = high_ratio
    dbg.onset = onset
    dbg.gunshot_prob = prob
    dbg.analyze_ms = (time.perf_counter() - t0) * 1000.0
    return prob, dbg


def estimate_direction(left: np.ndarray, right: np.ndarray, sr: int) -> tuple[float, float, str, DetectDebug]:
    rms_l = float(np.sqrt(np.mean(left * left) + 1e-12))
    rms_r = float(np.sqrt(np.mean(right * right) + 1e-12))
    ild_deg, ild_conf, ild_db = ild_to_deg(rms_l, rms_r)
    itd_deg, itd_conf, itd_ms = itd_to_deg(left, right, sr)
    if ild_conf >= 0.25 and itd_conf >= 0.25 and abs(ild_deg - itd_deg) < 50:
        w = ild_conf + itd_conf
        deg = (ild_deg * ild_conf + itd_deg * itd_conf) / w
        conf = min(1.0, 0.55 * ild_conf + 0.45 * itd_conf + 0.1)
    elif ild_conf >= itd_conf:
        deg, conf = ild_deg, ild_conf
    else:
        deg, conf = itd_deg, itd_conf * 0.85
    # Front/back from stereo mix is unreliable — keep hemisphere unknown unless HF is very dull.
    mix = 0.5 * (left + right)
    spec = np.abs(np.fft.rfft(mix * np.hanning(len(mix)))) ** 2
    freqs = np.fft.rfftfreq(len(mix), 1.0 / sr)
    hf = _band_energy(spec, freqs, 6000.0, 11000.0)
    mf = _band_energy(spec, freqs, 1500.0, 4500.0) + 1e-12
    hemi = "unknown"
    if hf / mf < 0.08 and abs(deg) > 25:
        hemi = "rear"
        conf *= 0.55
    dbg = DetectDebug(
        rms_l=rms_l,
        rms_r=rms_r,
        rel_deg=deg,
        dir_conf=conf,
        ild_db=ild_db,
        itd_ms=itd_ms,
    )
    return deg, conf, hemi, dbg


class ShotEventManager:
    def __init__(self, lifetime_s: float = SHOT_LIFETIME_S):
        self.lifetime_s = lifetime_s
        self.events: list[ShotEvent] = []

    def ingest(self, event: ShotEvent) -> ShotEvent:
        now = event.t0
        event.last_t = now
        self.prune(now)
        for existing in self.events:
            dt = now - existing.last_t
            ddeg = abs(shortest_delta(existing.rel_deg, event.rel_deg))
            if existing.world_bearing is not None and event.world_bearing is not None:
                ddeg = abs(shortest_delta(existing.world_bearing, event.world_bearing))
            if dt <= CLUSTER_DT_S and ddeg <= CLUSTER_DEG:
                existing.count += 1
                existing.last_t = now
                existing.gunshot_prob = max(existing.gunshot_prob, event.gunshot_prob)
                existing.dir_conf = max(existing.dir_conf, event.dir_conf)
                existing.latency_ms = event.latency_ms
                existing.rel_deg += shortest_delta(existing.rel_deg, event.rel_deg) * 0.25
                existing.rel_deg = max(-90.0, min(90.0, existing.rel_deg))
                if existing.world_bearing is not None and event.world_bearing is not None:
                    existing.world_bearing = wrap_deg(
                        existing.world_bearing + shortest_delta(existing.world_bearing, event.world_bearing) * 0.25
                    )
                return existing
        self.events.append(event)
        if len(self.events) > MAX_ACTIVE:
            self.events.sort(key=lambda e: e.last_t)
            self.events = self.events[-MAX_ACTIVE:]
        return event

    def prune(self, now: float | None = None):
        now = time.perf_counter() if now is None else now
        self.events = [e for e in self.events if (now - e.last_t) < self.lifetime_s]

    def display_rel(self, event: ShotEvent, game_yaw: float | None) -> float:
        """View-relative angle to draw. Uses world bearing when heading is live."""
        if event.world_bearing is not None and game_yaw is not None:
            return shortest_delta(game_yaw, event.world_bearing)
        return event.rel_deg
