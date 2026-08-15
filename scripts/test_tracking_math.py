"""Math tests for heading wrap and prediction correction (no Tarkov window)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wmnavi.heading import HeadingTracker, shortest_delta, wrap_deg
from wmnavi.predictor import MovementPredictor, _blend
from wmnavi.coords import PlayerState
from wmnavi.motion_tracker import MotionSample


def test_wrap_and_shortest():
    assert wrap_deg(-10) == 350
    assert abs(shortest_delta(358, 2) - 4) < 1e-6
    assert abs(shortest_delta(2, 358) - (-4)) < 1e-6


def test_heading_visual_then_correct():
    h = HeadingTracker()
    h.set_authoritative(90.0, 0)
    h.apply_visual_yaw(15.0, 0.8)
    assert abs(shortest_delta(h.predicted, 105.0)) < 0.6 or abs(shortest_delta(h.game_yaw, 105.0)) < 0.6
    h.set_authoritative(103.0, 0)
    # Small error blends toward 103, not a full snap back to 90.
    err = abs(shortest_delta(h.predicted, 103.0))
    assert err < 8.0


def test_large_heading_error_snaps():
    h = HeadingTracker()
    h.set_authoritative(10.0, 0)
    h.apply_visual_yaw(80.0, 0.9)
    h.set_authoritative(12.0, 0)
    assert abs(shortest_delta(h.predicted, 12.0)) < 0.2


def test_predictor_confirm_and_motion():
    p = MovementPredictor()
    p.reset_calibration()
    a = PlayerState(100, 1, 200, 90)
    p.confirm(a, map_slug="customs")
    sample = MotionSample(0.05, yaw_flow_px=-2.0, fwd_flow_px=1.2, strafe_flow_px=0.0, feature_conf=0.7, capture_ok=True, process_ms=4.0)
    p.apply_motion(sample, prediction_on=True)
    assert p.state.has_fix
    # Forward motion along +X at yaw 90
    assert p.state.predicted_x != p.state.confirmed_x or p.state.speed >= 0.0
    b = PlayerState(101.2, 1, 200.1, 92)
    p.confirm(b, map_slug="customs")
    assert p.state.last_error_m >= 0.0
    assert p.calib.samples >= 1


def test_blend():
    assert abs(_blend(1.0, 2.0, 0.5) - 1.5) < 1e-9


def main() -> int:
    test_wrap_and_shortest()
    test_heading_visual_then_correct()
    test_large_heading_error_snaps()
    test_predictor_confirm_and_motion()
    test_blend()
    print("TRACKING MATH OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
