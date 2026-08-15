"""Shared lightweight visual motion tracker (compass + AI Prediction).

Captures Tarkov frames in memory and estimates yaw/translation flow with
Farneback optical flow. Does not run the screenshot localization pipeline.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from .win_capture import capture_eft_bgr


@dataclass
class MotionSample:
    dt: float
    yaw_flow_px: float
    fwd_flow_px: float
    strafe_flow_px: float
    feature_conf: float
    capture_ok: bool
    process_ms: float


class MotionTracker(QObject):
    """Background ~20 Hz loop. Emits samples on the creating (UI) thread via queued Signal."""

    sample = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self.last_sample: MotionSample | None = None
        self.fps = 0.0

    @property
    def active(self) -> bool:
        return self._running

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._loop, name="wmnavi-motion", daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            self._running = False
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.2)
        self._thread = None

    def _loop(self):
        prev = None
        last_emit = time.perf_counter()
        frames = 0
        fps_t = time.perf_counter()
        while True:
            with self._lock:
                if not self._running:
                    break
            t0 = time.perf_counter()
            result = self._step(prev)
            if result is None:
                time.sleep(0.08)
                continue
            prev, motion = result
            if motion.capture_ok:
                self.last_sample = motion
                self.sample.emit(motion)
                frames += 1
            else:
                prev = None
            now = time.perf_counter()
            if now - fps_t >= 1.0:
                self.fps = frames / max(0.001, now - fps_t)
                frames = 0
                fps_t = now
            elapsed = now - t0
            time.sleep(max(0.01, 0.05 - elapsed))
        self.fps = 0.0

    def _step(self, prev_gray):
        try:
            import cv2
            import numpy as np
        except Exception:
            time.sleep(0.25)
            return None
        t0 = time.perf_counter()
        frame = capture_eft_bgr(320)
        if frame is None:
            time.sleep(0.12)
            return None
        h, w = frame.shape[:2]
        y0, y1 = int(h * 0.18), int(h * 0.78)
        x0, x1 = int(w * 0.18), int(w * 0.82)
        crop = frame[y0:y1, x0:x1]
        if crop.size == 0:
            return None
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        if prev_gray is None or prev_gray.shape != gray.shape:
            return gray, MotionSample(0.05, 0.0, 0.0, 0.0, 0.05, True, (time.perf_counter() - t0) * 1000)
        flow = cv2.calcOpticalFlowFarneback(
            prev_gray, gray, None, 0.5, 2, 15, 2, 5, 1.1, 0
        )
        fx = flow[:, :, 0]
        fy = flow[:, :, 1]
        mag = np.hypot(fx, fy)
        mean_mag = float(np.mean(mag))
        if mean_mag < 0.04:
            conf = 0.15
        else:
            # Consistency: rotation is spatially uniform horizontal flow.
            conf = float(min(1.0, mean_mag / 2.2))
            std_fx = float(np.std(fx))
            if std_fx > 6.0:
                conf *= 0.55
        yaw_flow = float(np.median(fx))
        rot = np.median(fx)
        residual_x = fx - rot
        ch, cw = gray.shape
        lower = fy[int(ch * 0.45) :, :]
        fwd_flow = float(np.median(lower))
        turning = abs(yaw_flow) > 0.55
        strafe_flow = 0.0 if turning else float(np.median(residual_x))
        dt = 0.05
        ms = (time.perf_counter() - t0) * 1000
        return gray, MotionSample(dt, yaw_flow, fwd_flow, strafe_flow, conf, True, ms)
