"""Between-ping position prediction + local statistical calibration.

Confirmed screenshot localization is always ground truth. Visual motion only
fills the gaps. No large ML model — rolling weighted averages with outliers dropped.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from .coords import PlayerState, point_in_crs_bounds
from .heading import shortest_delta, wrap_deg
from .motion_tracker import MotionSample
from .paths import user_data_dir


UNCALIBRATED = "UNCALIBRATED"
CALIBRATING = "CALIBRATING"
CALIBRATED = "CALIBRATED"
LOW_CONFIDENCE = "LOW_CONFIDENCE"

# Tarkov-ish speeds (m/s) used as priors until calibrated.
WALK_MPS = 3.0
SPRINT_MPS = 6.2
MAX_MPS = 8.5
CROUCH_MPS = 1.55
BACKPEDAL_MPS = 2.1
STRAFE_WALK_MPS = 2.4


def _profile_path() -> Path:
    return user_data_dir() / "prediction_calib.json"


@dataclass
class PredictorState:
    confirmed_x: float = 0.0
    confirmed_y: float = 0.0
    confirmed_z: float = 0.0
    confirmed_yaw: float = 0.0
    predicted_x: float = 0.0
    predicted_y: float = 0.0
    predicted_z: float = 0.0
    predicted_yaw: float = 0.0
    vx: float = 0.0
    vz: float = 0.0
    speed: float = 0.0
    confidence: float = 0.0
    last_error_m: float = 0.0
    last_confirm_at: float = 0.0
    has_fix: bool = False
    loc_ms: float = 0.0
    map_slug: str = ""


@dataclass
class Calibration:
    yaw_deg_per_px: float = 0.42
    fwd_mps_per_px: float = 0.55
    strafe_mps_per_px: float = 0.45
    samples: int = 0
    recent_errors: list = field(default_factory=list)

    def state_name(self) -> str:
        if self.samples < 3:
            return UNCALIBRATED
        if self.samples < 12:
            return CALIBRATING
        mean_err = sum(self.recent_errors[-8:]) / max(1, len(self.recent_errors[-8:]))
        if mean_err > 4.5:
            return LOW_CONFIDENCE
        return CALIBRATED


class MovementPredictor:
    def __init__(self):
        self.state = PredictorState()
        self.calib = Calibration()
        self._integ_yaw_flow = 0.0
        self._integ_fwd_flow = 0.0
        self._integ_strafe_flow = 0.0
        self._integ_dt = 0.0
        self._last_tick = time.perf_counter()
        self._load()

    def reset_calibration(self):
        self.calib = Calibration()
        self._save()

    def _load(self):
        path = _profile_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.calib.yaw_deg_per_px = float(data.get("yaw_deg_per_px") or 0.42)
        self.calib.fwd_mps_per_px = float(data.get("fwd_mps_per_px") or 0.55)
        self.calib.strafe_mps_per_px = float(data.get("strafe_mps_per_px") or 0.45)
        self.calib.samples = int(data.get("samples") or 0)
        errs = data.get("recent_errors") or []
        if isinstance(errs, list):
            self.calib.recent_errors = [float(x) for x in errs[-16:]]

    def _save(self):
        path = _profile_path()
        payload = {
            "yaw_deg_per_px": self.calib.yaw_deg_per_px,
            "fwd_mps_per_px": self.calib.fwd_mps_per_px,
            "strafe_mps_per_px": self.calib.strafe_mps_per_px,
            "samples": self.calib.samples,
            "recent_errors": self.calib.recent_errors[-16:],
        }
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def time_since_confirm(self) -> float:
        if not self.state.has_fix:
            return 999.0
        return max(0.0, time.perf_counter() - self.state.last_confirm_at)

    def confirm(self, player: PlayerState, *, map_slug: str, loc_ms: float = 0.0, bounds=None, rotation=0, transform=None):
        """Apply a high-confidence screenshot localization."""
        st = self.state
        now = time.perf_counter()
        if st.has_fix and map_slug == st.map_slug:
            pred_err = math.hypot(st.predicted_x - player.x, st.predicted_z - player.z)
            st.last_error_m = pred_err
            dt = max(0.05, now - st.last_confirm_at)
            actual_dx = player.x - st.confirmed_x
            actual_dz = player.z - st.confirmed_z
            actual_dist = math.hypot(actual_dx, actual_dz)
            yaw_err = abs(shortest_delta(st.predicted_yaw, player.yaw_deg))
            self._learn(dt, actual_dist, actual_dx, actual_dz, player.yaw_deg, pred_err, yaw_err)
            if pred_err > 8.0:
                st.predicted_x, st.predicted_z = player.x, player.z
                st.confidence = 0.55
            elif pred_err > 2.5:
                t = 0.62
                st.predicted_x += (player.x - st.predicted_x) * t
                st.predicted_z += (player.z - st.predicted_z) * t
                st.confidence = 0.72
            else:
                t = 0.28
                st.predicted_x += (player.x - st.predicted_x) * t
                st.predicted_z += (player.z - st.predicted_z) * t
                st.confidence = 0.96
            if dt > 0.2 and actual_dist < 25.0:
                vx, vz = actual_dx / dt, actual_dz / dt
                spd = math.hypot(vx, vz)
                if spd > MAX_MPS:
                    s = MAX_MPS / spd
                    vx, vz = vx * s, vz * s
                    spd = MAX_MPS
                st.vx, st.vz, st.speed = vx, vz, spd
            else:
                st.vx, st.vz, st.speed = 0.0, 0.0, 0.0
        else:
            st.predicted_x, st.predicted_z = player.x, player.z
            st.confidence = 1.0
            st.last_error_m = 0.0
            st.vx = 0.0
            st.vz = 0.0
            st.speed = 0.0

        st.confirmed_x, st.confirmed_y, st.confirmed_z = player.x, player.y, player.z
        st.confirmed_yaw = player.yaw_deg
        st.predicted_y = player.y
        st.predicted_yaw = player.yaw_deg
        st.map_slug = map_slug
        st.has_fix = True
        st.last_confirm_at = now
        st.loc_ms = loc_ms
        self._integ_yaw_flow = 0.0
        self._integ_fwd_flow = 0.0
        self._integ_strafe_flow = 0.0
        self._integ_dt = 0.0
        self._clamp_bounds(bounds, rotation, transform)

    def apply_controls(
        self,
        dt: float,
        keys: dict,
        yaw_deg: float,
        *,
        bounds=None,
        rotation=0,
        transform=None,
    ):
        """Dead-reckon from WASD along current facing. V pings remain ground truth."""
        if not self.state.has_fix:
            return
        dt = max(0.008, min(0.12, float(dt)))
        self.state.predicted_yaw = wrap_deg(float(yaw_deg))
        yaw = math.radians(self.state.predicted_yaw)
        fwd_x, fwd_z = math.sin(yaw), math.cos(yaw)
        right_x, right_z = math.cos(yaw), -math.sin(yaw)

        ax = 0.0
        ay = 0.0
        if keys.get("forward"):
            ax += 1.0
        if keys.get("back"):
            ax -= 1.0
        if keys.get("right"):
            ay += 1.0
        if keys.get("left"):
            ay -= 1.0
        mag = math.hypot(ax, ay)
        if mag > 1.0:
            ax /= mag
            ay /= mag

        if mag < 0.1:
            self.state.vx *= 0.12
            self.state.vz *= 0.12
            if math.hypot(self.state.vx, self.state.vz) < 0.12:
                self.state.vx = 0.0
                self.state.vz = 0.0
                self.state.speed = 0.0
                return
            self.state.speed = math.hypot(self.state.vx, self.state.vz)
            self.state.predicted_x += self.state.vx * dt
            self.state.predicted_z += self.state.vz * dt
            self._clamp_bounds(bounds, rotation, transform)
            return

        sprint = bool(keys.get("sprint")) and ax > 0.3 and not keys.get("crouch")
        if keys.get("crouch"):
            speed = CROUCH_MPS
        elif sprint:
            speed = SPRINT_MPS
        elif ax < -0.3 and abs(ay) < 0.3:
            speed = BACKPEDAL_MPS
        elif abs(ay) >= abs(ax) and ax <= 0.3:
            speed = STRAFE_WALK_MPS
        else:
            speed = WALK_MPS

        vx = (fwd_x * ax + right_x * ay) * speed
        vz = (fwd_z * ax + right_z * ay) * speed
        cap = math.hypot(vx, vz)
        if cap > MAX_MPS:
            scale = MAX_MPS / cap
            vx *= scale
            vz *= scale
        self.state.vx = vx
        self.state.vz = vz
        self.state.speed = math.hypot(vx, vz)
        self.state.predicted_x += vx * dt
        self.state.predicted_z += vz * dt
        self._clamp_bounds(bounds, rotation, transform)

    def apply_motion(
        self,
        sample: MotionSample,
        *,
        prediction_on: bool,
        bounds=None,
        rotation=0,
        transform=None,
        skip_translation: bool = False,
    ):
        if not self.state.has_fix:
            return
        dt = max(0.01, min(0.12, float(sample.dt or 0.05)))
        conf = float(sample.feature_conf if sample.capture_ok else 0.0)
        yaw_delta = -sample.yaw_flow_px * self.calib.yaw_deg_per_px
        if conf >= 0.08 and sample.capture_ok:
            self.state.predicted_yaw = wrap_deg(self.state.predicted_yaw + yaw_delta)
            self._integ_yaw_flow += sample.yaw_flow_px
        self._integ_fwd_flow += sample.fwd_flow_px * dt
        self._integ_strafe_flow += sample.strafe_flow_px * dt
        self._integ_dt += dt

        if not prediction_on:
            self.state.speed = 0.0
            return

        if skip_translation:
            return

        if conf < 0.10 or not sample.capture_ok:
            # Coast on last velocity so the marker does not freeze between frames.
            self.state.vx *= 0.90
            self.state.vz *= 0.90
            self.state.predicted_x += self.state.vx * dt
            self.state.predicted_z += self.state.vz * dt
            self.state.speed = math.hypot(self.state.vx, self.state.vz)
            self._decay_confidence(dt, extra=0.01)
            self._clamp_bounds(bounds, rotation, transform)
            return

        yaw = math.radians(self.state.predicted_yaw)
        fwd_x, fwd_z = math.sin(yaw), math.cos(yaw)
        right_x, right_z = math.cos(yaw), -math.sin(yaw)
        fwd_mps = max(-SPRINT_MPS, min(SPRINT_MPS, sample.fwd_flow_px * self.calib.fwd_mps_per_px))
        strafe_mps = max(-WALK_MPS, min(WALK_MPS, sample.strafe_flow_px * self.calib.strafe_mps_per_px))
        # Deadzone so idle camera noise does not walk the marker.
        if abs(sample.fwd_flow_px) < 0.12:
            fwd_mps = 0.0
        if abs(sample.strafe_flow_px) < 0.12:
            strafe_mps = 0.0
        vx = fwd_x * fwd_mps + right_x * strafe_mps
        vz = fwd_z * fwd_mps + right_z * strafe_mps
        speed = math.hypot(vx, vz)
        if speed > MAX_MPS:
            scale = MAX_MPS / speed
            vx *= scale
            vz *= scale
            speed = MAX_MPS
        # Blend with coasting velocity so a single noisy frame does not stall.
        self.state.vx = self.state.vx * 0.35 + vx * 0.65
        self.state.vz = self.state.vz * 0.35 + vz * 0.65
        self.state.speed = math.hypot(self.state.vx, self.state.vz)
        self.state.predicted_x += self.state.vx * dt
        self.state.predicted_z += self.state.vz * dt
        age = self.time_since_confirm()
        self._decay_confidence(dt, extra=0.004 * min(age, 8.0))
        if age > 18.0 and self.state.confidence < 0.12:
            # Only freeze after a long gap with no V ping — not every few seconds.
            self.state.predicted_x += (self.state.confirmed_x - self.state.predicted_x) * min(1.0, dt * 1.4)
            self.state.predicted_z += (self.state.confirmed_z - self.state.predicted_z) * min(1.0, dt * 1.4)
            self.state.vx = 0.0
            self.state.vz = 0.0
            self.state.speed = 0.0
        self._clamp_bounds(bounds, rotation, transform)

    def predicted_player(self) -> PlayerState | None:
        if not self.state.has_fix:
            return None
        return PlayerState(
            x=self.state.predicted_x,
            y=self.state.predicted_y,
            z=self.state.predicted_z,
            yaw_deg=self.state.predicted_yaw,
        )

    def confirmed_player(self) -> PlayerState | None:
        if not self.state.has_fix:
            return None
        return PlayerState(
            x=self.state.confirmed_x,
            y=self.state.confirmed_y,
            z=self.state.confirmed_z,
            yaw_deg=self.state.confirmed_yaw,
        )

    def _decay_confidence(self, dt: float, extra: float = 0.0):
        self.state.confidence = max(0.18, self.state.confidence - dt * (0.03 + extra))

    def _clamp_bounds(self, bounds, rotation, transform):
        if not bounds or not transform or not self.state.has_fix:
            return
        if point_in_crs_bounds(self.state.predicted_x, self.state.predicted_z, bounds, rotation, transform, 0.02):
            return
        self.state.predicted_x = self.state.confirmed_x
        self.state.predicted_z = self.state.confirmed_z
        self.state.vx = 0.0
        self.state.vz = 0.0
        self.state.confidence = min(self.state.confidence, 0.3)

    def _learn(self, dt, actual_dist, actual_dx, actual_dz, yaw, pred_err, yaw_err):
        # Reject teleport-like jumps (extract, desync).
        if actual_dist > 25.0 or dt > 12.0:
            return
        self.calib.recent_errors.append(pred_err)
        self.calib.recent_errors = self.calib.recent_errors[-16:]
        self.calib.samples += 1
        flow_yaw = self._integ_yaw_flow
        actual_yaw = shortest_delta(self.state.confirmed_yaw, yaw)
        if abs(flow_yaw) > 1.2 and 2.0 < abs(actual_yaw) < 80.0:
            est = abs(actual_yaw) / max(0.2, abs(flow_yaw))
            if 0.08 < est < 2.5:
                self.calib.yaw_deg_per_px = _blend(self.calib.yaw_deg_per_px, est, 0.12)
        mean_fwd = self._integ_fwd_flow / max(0.05, self._integ_dt)
        if actual_dist > 0.8 and abs(mean_fwd) > 0.15:
            mps = actual_dist / dt
            est = mps / mean_fwd
            if 0.05 < abs(est) < 4.0:
                self.calib.fwd_mps_per_px = _blend(self.calib.fwd_mps_per_px, abs(est), 0.1)
        self._save()


def _blend(old: float, new: float, alpha: float) -> float:
    return old * (1.0 - alpha) + new * alpha
