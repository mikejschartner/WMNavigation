"""Background WASAPI loopback → gunshot events. Independent of screenshot localization."""

from __future__ import annotations

import math
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, Signal

from .audio_capture import CaptureInfo, find_wasapi_loopback
from .audio_detect import (
    DetectDebug,
    ShotEvent,
    ShotEventManager,
    detect_gunshot,
    estimate_direction,
)
from .heading import wrap_deg

GUNSHOT_THRESHOLD = 0.58
DIR_SHOW_THRESHOLD = 0.18
HOP_S = 0.012
WINDOW_S = 0.032


class AudioShotService(QObject):
    shot = Signal(object)
    started = Signal(object)  # CaptureInfo
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = ShotEventManager()
        self.debug = DetectDebug()
        self.capture = CaptureInfo(ok=False)
        self.last_latency_ms = 0.0
        self.last_shot_at = 0.0
        self._running = False
        self._noise = 0.02
        self._mic = None
        self._worker: threading.Thread | None = None
        self._heading_fn = None

    def set_heading_provider(self, fn):
        self._heading_fn = fn

    @property
    def active(self) -> bool:
        return self._running

    def start(self):
        if self._running:
            return
        mic, name, sr, err = find_wasapi_loopback()
        if mic is None:
            self.capture = CaptureInfo(ok=False, error=err or "No loopback device")
            self.failed.emit(self.capture.error)
            return
        self._mic = mic
        self.capture = CaptureInfo(ok=True, device_name=name, samplerate=int(sr), channels=2)
        self._running = True
        self._worker = threading.Thread(target=self._run, name="wmnavi-audio", daemon=True)
        self._worker.start()
        self.started.emit(self.capture)

    def stop(self):
        self._running = False
        thread = self._worker
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.2)
        self._worker = None
        self._mic = None
        self.manager.events.clear()

    def _run(self):
        sr = max(16000, int(self.capture.samplerate or 48000))
        hop_frames = max(64, int(HOP_S * sr))
        try:
            with self._mic.recorder(samplerate=sr, channels=2) as rec:
                buf_l = np.zeros(0, dtype=np.float64)
                buf_r = np.zeros(0, dtype=np.float64)
                last_fire = 0.0
                win = max(64, int(WINDOW_S * sr))
                hop = max(32, int(HOP_S * sr))
                while self._running:
                    t_cap = time.perf_counter()
                    try:
                        block = rec.record(numframes=hop_frames)
                    except Exception:
                        time.sleep(0.004)
                        continue
                    if block is None:
                        continue
                    block = np.asarray(block, dtype=np.float64)
                    if block.ndim == 1:
                        left_b, right_b = block, block
                    else:
                        left_b = block[:, 0]
                        right_b = block[:, 1] if block.shape[1] > 1 else block[:, 0]
                    buf_l = np.concatenate([buf_l, left_b])
                    buf_r = np.concatenate([buf_r, right_b])
                    keep = win * 4
                    if len(buf_l) > keep:
                        buf_l = buf_l[-keep:]
                        buf_r = buf_r[-keep:]
                    while len(buf_l) >= win and len(buf_r) >= win:
                        left = buf_l[:win]
                        right = buf_r[:win]
                        buf_l = buf_l[hop:]
                        buf_r = buf_r[hop:]
                        last_fire = self._analyze_window(left, right, sr, t_cap, last_fire)
        except Exception as exc:
            self.capture = CaptureInfo(ok=False, error=str(exc))
            self._running = False
            self.failed.emit(str(exc))

    def _analyze_window(self, left, right, sr: int, t_cap: float, last_fire: float) -> float:
        rms = math.sqrt(0.5 * (float(np.mean(left * left)) + float(np.mean(right * right))) + 1e-12)
        if rms < self._noise * 2.2:
            self._noise = 0.92 * self._noise + 0.08 * max(rms, 1e-5)
        prob, dbg = detect_gunshot(left, right, sr, self._noise)
        self.debug = dbg
        now = time.perf_counter()
        if prob < GUNSHOT_THRESHOLD or (now - last_fire) < 0.07:
            return last_fire
        deg, dconf, hemi, ddbg = estimate_direction(left, right, sr)
        dbg.rel_deg = deg
        dbg.dir_conf = dconf
        dbg.ild_db = ddbg.ild_db
        dbg.itd_ms = ddbg.itd_ms
        self.debug = dbg
        if dconf < DIR_SHOW_THRESHOLD and abs(deg) < 8:
            dconf = max(dconf, 0.22)
            deg = 0.0
        yaw = None
        if self._heading_fn:
            try:
                yaw = self._heading_fn()
            except Exception:
                yaw = None
        world = wrap_deg(float(yaw) + deg) if yaw is not None else None
        latency = (now - t_cap) * 1000.0
        ev = ShotEvent(
            t0=now,
            rel_deg=deg,
            gunshot_prob=prob,
            dir_conf=dconf,
            world_bearing=world,
            latency_ms=latency,
            hemisphere=hemi,
        )
        clustered = self.manager.ingest(ev)
        self.last_latency_ms = latency
        self.last_shot_at = now
        self.shot.emit(clustered)
        return now
