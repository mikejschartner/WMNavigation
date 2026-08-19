"""Factual between-screenshot prediction from keys + calibrated mouse.

Screenshot localization is always ground truth. No optical-flow walking.
No coasting after keys are released.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from .coords import PlayerState, point_in_crs_bounds
from .heading import shortest_delta, wrap_deg
from .paths import user_data_dir

UNCALIBRATED = "UNCALIBRATED"
CALIBRATING = "CALIBRATING"
CALIBRATED = "CALIBRATED"
LOW_CONFIDENCE = "LOW_CONFIDENCE"

# Used only until enough screenshot samples exist; confidence stays low.
WALK_MPS = 3.0
SPRINT_MPS = 6.2
MAX_MPS = 8.5
CROUCH_MPS = 1.55
BACKPEDAL_MPS = 2.1
STRAFE_WALK_MPS = 2.4
DEFAULT_MAX_AGE_S = 4.0


def _profile_path() -> Path:
    return user_data_dir() / "prediction_calib.json"


@dataclass
class PredictorState:
    confirmed_x: float = 0.0
    confirmed_y: float = 0.0
    confirmed_z: float = 0.0
    confirmed_yaw: float = 0.0
    confirmed_pitch: float = 0.0
    predicted_x: float = 0.0
    predicted_y: float = 0.0
    predicted_z: float = 0.0
    predicted_yaw: float = 0.0
    predicted_pitch: float = 0.0
    vx: float = 0.0
    vz: float = 0.0
    speed: float = 0.0
    confidence: float = 0.0
    last_error_m: float = 0.0
    last_confirm_at: float = 0.0
    has_fix: bool = False
    loc_ms: float = 0.0
    map_slug: str = ""
    keys_held: dict = field(default_factory=dict)
    mouse_dx: int = 0
    mouse_dy: int = 0


@dataclass
class Calibration:
    yaw_deg_per_count: float = 0.0
    pitch_deg_per_count: float = 0.0
    invert_yaw: float = 1.0
    invert_pitch: float = 1.0
    walk_mps: float = 0.0
    sprint_mps: float = 0.0
    samples: int = 0
    mouse_samples: int = 0
    recent_errors: list = field(default_factory=list)
    camera_height_offset: float | None = None
    yaw_bias: float = 0.0
    pitch_bias: float = 0.0

    def state_name(self) -> str:
        if self.samples < 3:
            return UNCALIBRATED
        if self.samples < 12:
            return CALIBRATING
        mean_err = sum(self.recent_errors[-8:]) / max(1, len(self.recent_errors[-8:]))
        if mean_err > 4.5:
            return LOW_CONFIDENCE
        return CALIBRATED

    def yaw_scale(self) -> float | None:
        if self.mouse_samples < 4 or abs(self.yaw_deg_per_count) < 1e-6:
            return None
        return self.yaw_deg_per_count * self.invert_yaw

    def pitch_scale(self) -> float | None:
        if self.mouse_samples < 4 or abs(self.pitch_deg_per_count) < 1e-6:
            return None
        return self.pitch_deg_per_count * self.invert_pitch


class MovementPredictor:
    def __init__(self):
        self.state = PredictorState()
        self.calib = Calibration()
        self.max_age_s = DEFAULT_MAX_AGE_S
        self._integ_mouse_x = 0
        self._integ_mouse_y = 0
        self._integ_dt = 0.0
        self._integ_keys: dict[str, bool] = {}
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
        self.calib.yaw_deg_per_count = float(data.get("yaw_deg_per_count") or data.get("yaw_deg_per_px") or 0)
        self.calib.pitch_deg_per_count = float(data.get("pitch_deg_per_count") or 0)
        self.calib.invert_yaw = float(data.get("invert_yaw") or 1)
        self.calib.invert_pitch = float(data.get("invert_pitch") or 1)
        self.calib.walk_mps = float(data.get("walk_mps") or 0)
        self.calib.sprint_mps = float(data.get("sprint_mps") or 0)
        self.calib.samples = int(data.get("samples") or 0)
        self.calib.mouse_samples = int(data.get("mouse_samples") or 0)
        offs = data.get("camera_height_offset")
        self.calib.camera_height_offset = None if offs is None else float(offs)
        self.calib.yaw_bias = float(data.get("yaw_bias") or 0)
        self.calib.pitch_bias = float(data.get("pitch_bias") or 0)
        errs = data.get("recent_errors") or []
        if isinstance(errs, list):
            self.calib.recent_errors = [float(x) for x in errs[-16:]]

    def _save(self):
        path = _profile_path()
        payload = {
            "yaw_deg_per_count": self.calib.yaw_deg_per_count,
            "pitch_deg_per_count": self.calib.pitch_deg_per_count,
            "invert_yaw": self.calib.invert_yaw,
            "invert_pitch": self.calib.invert_pitch,
            "walk_mps": self.calib.walk_mps,
            "sprint_mps": self.calib.sprint_mps,
            "samples": self.calib.samples,
            "mouse_samples": self.calib.mouse_samples,
            "camera_height_offset": self.calib.camera_height_offset,
            "yaw_bias": self.calib.yaw_bias,
            "pitch_bias": self.calib.pitch_bias,
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
            self._learn(dt, actual_dist, actual_dx, actual_dz, player, pred_err, yaw_err)
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
        else:
            st.predicted_x, st.predicted_z = player.x, player.z
            st.confidence = 1.0
            st.last_error_m = 0.0

        st.vx = 0.0
        st.vz = 0.0
        st.speed = 0.0
        st.confirmed_x, st.confirmed_y, st.confirmed_z = player.x, player.y, player.z
        st.confirmed_yaw = player.yaw_deg
        st.confirmed_pitch = float(getattr(player, "pitch_deg", 0.0) or 0.0)
        st.predicted_y = player.y
        st.predicted_yaw = player.yaw_deg
        st.predicted_pitch = st.confirmed_pitch
        st.map_slug = map_slug
        st.has_fix = True
        st.last_confirm_at = now
        st.loc_ms = loc_ms
        self._integ_mouse_x = 0
        self._integ_mouse_y = 0
        self._integ_dt = 0.0
        self._clamp_bounds(bounds, rotation, transform)

    def tick(
        self,
        dt: float,
        keys: dict,
        mouse_dx: int,
        mouse_dy: int,
        *,
        prediction_on: bool,
        bounds=None,
        rotation=0,
        transform=None,
    ):
        if not self.state.has_fix:
            return
        if not prediction_on:
            self._copy_confirmed()
            return
        age = self.time_since_confirm()
        if age > self.max_age_s:
            self.state.confidence = min(self.state.confidence, 0.2)
            self.state.vx = 0.0
            self.state.vz = 0.0
            self.state.speed = 0.0
            return
        dt = max(0.008, min(0.12, float(dt)))
        self.state.keys_held = dict(keys or {})
        self.state.mouse_dx = int(mouse_dx)
        self.state.mouse_dy = int(mouse_dy)
        self._integ_mouse_x += int(mouse_dx)
        self._integ_mouse_y += int(mouse_dy)
        self._integ_dt += dt
        self._integ_keys = dict(keys or {})
        self._apply_mouse(int(mouse_dx), int(mouse_dy))
        self.apply_controls(dt, keys or {}, self.state.predicted_yaw, bounds=bounds, rotation=rotation, transform=transform)
        name = self.calib.state_name()
        if name == UNCALIBRATED:
            self.state.confidence = min(self.state.confidence, 0.45)
        elif name == CALIBRATING:
            self.state.confidence = min(self.state.confidence, 0.72)
        self.state.confidence = max(0.15, self.state.confidence - dt * 0.04)

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
            self.state.vx = 0.0
            self.state.vz = 0.0
            self.state.speed = 0.0
            return

        sprint = bool(keys.get("sprint")) and ax > 0.3 and not keys.get("crouch")
        walk = self.calib.walk_mps if self.calib.walk_mps > 0.4 else WALK_MPS
        sprint_s = self.calib.sprint_mps if self.calib.sprint_mps > 0.4 else SPRINT_MPS
        if keys.get("crouch"):
            speed = CROUCH_MPS
        elif sprint:
            speed = sprint_s
        elif ax < -0.3 and abs(ay) < 0.3:
            speed = BACKPEDAL_MPS
        elif abs(ay) >= abs(ax) and ax <= 0.3:
            speed = STRAFE_WALK_MPS
        else:
            speed = walk

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

    def apply_motion(self, sample, *, prediction_on: bool, bounds=None, rotation=0, transform=None, skip_translation: bool = False):
        """Optical flow is not used for walking. Kept so compass/tests still call it."""
        if not prediction_on or not self.state.has_fix:
            return
        if skip_translation:
            return
        # Intentionally no translation. Flow is not a measured meter.

    def predicted_player(self) -> PlayerState | None:
        if not self.state.has_fix:
            return None
        return PlayerState(
            x=self.state.predicted_x,
            y=self.state.predicted_y,
            z=self.state.predicted_z,
            yaw_deg=self.state.predicted_yaw,
            pitch_deg=self.state.predicted_pitch,
        )

    def confirmed_player(self) -> PlayerState | None:
        if not self.state.has_fix:
            return None
        return PlayerState(
            x=self.state.confirmed_x,
            y=self.state.confirmed_y,
            z=self.state.confirmed_z,
            yaw_deg=self.state.confirmed_yaw,
            pitch_deg=self.state.confirmed_pitch,
        )

    def _copy_confirmed(self):
        st = self.state
        st.predicted_x, st.predicted_y, st.predicted_z = st.confirmed_x, st.confirmed_y, st.confirmed_z
        st.predicted_yaw = st.confirmed_yaw
        st.predicted_pitch = st.confirmed_pitch
        st.vx = 0.0
        st.vz = 0.0
        st.speed = 0.0

    def _apply_mouse(self, dx: int, dy: int):
        yaw_s = self.calib.yaw_scale()
        if yaw_s is not None and dx:
            self.state.predicted_yaw = wrap_deg(self.state.predicted_yaw + dx * yaw_s)
        pitch_s = self.calib.pitch_scale()
        if pitch_s is not None and dy:
            self.state.predicted_pitch = max(-89.0, min(89.0, self.state.predicted_pitch + dy * pitch_s))

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

    def _learn(self, dt, actual_dist, actual_dx, actual_dz, player: PlayerState, pred_err, yaw_err):
        if actual_dist > 25.0 or dt > 12.0:
            return
        self.calib.recent_errors.append(pred_err)
        self.calib.recent_errors = self.calib.recent_errors[-16:]
        self.calib.samples += 1
        keys = self._integ_keys
        if keys.get("forward") and not keys.get("back") and actual_dist > 0.8:
            mps = actual_dist / dt
            if 0.8 < mps < MAX_MPS:
                if keys.get("sprint"):
                    self.calib.sprint_mps = _blend(self.calib.sprint_mps or SPRINT_MPS, mps, 0.12)
                else:
                    self.calib.walk_mps = _blend(self.calib.walk_mps or WALK_MPS, mps, 0.12)
        mx = self._integ_mouse_x
        actual_yaw = shortest_delta(self.state.confirmed_yaw, player.yaw_deg)
        if abs(mx) > 8 and 1.0 < abs(actual_yaw) < 80.0:
            est = actual_yaw / mx
            if 0.0005 < abs(est) < 0.25:
                prev = self.calib.yaw_deg_per_count or est
                self.calib.yaw_deg_per_count = _blend(prev, est, 0.12)
                self.calib.invert_yaw = 1.0
                self.calib.mouse_samples += 1
        my = self._integ_mouse_y
        actual_pitch = float(getattr(player, "pitch_deg", 0.0) or 0.0) - self.state.confirmed_pitch
        if abs(my) > 8 and 1.0 < abs(actual_pitch) < 60.0:
            est = actual_pitch / my
            if 0.0005 < abs(est) < 0.25:
                prev = self.calib.pitch_deg_per_count or est
                self.calib.pitch_deg_per_count = _blend(prev, est, 0.12)
                self.calib.invert_pitch = 1.0
                self.calib.mouse_samples += 1
        self._save()


def _blend(old: float, new: float, alpha: float) -> float:
    return old * (1.0 - alpha) + new * alpha
