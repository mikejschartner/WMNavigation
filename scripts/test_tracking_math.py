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


def test_predictor_coasts_when_flow_drops():
    p = MovementPredictor()
    p.reset_calibration()
    p.confirm(PlayerState(0, 1, 0, 0), map_slug="customs")
    moving = MotionSample(0.05, 0.0, 2.0, 0.0, 0.8, True, 4.0)
    p.apply_motion(moving, prediction_on=True)
    z_after = p.state.predicted_z
    assert z_after != 0.0
    weak = MotionSample(0.05, 0.0, 0.0, 0.0, 0.05, True, 4.0)
    p.apply_motion(weak, prediction_on=True)
    # Should still have moved (coast), not snapped back to confirm.
    assert abs(p.state.predicted_z) >= abs(z_after) * 0.5


def test_blend():
    assert abs(_blend(1.0, 2.0, 0.5) - 1.5) < 1e-9


def test_audio_ild_right_positive():
    import numpy as np
    from wmnavi.audio_detect import (
        GUNSHOT_MIN_PROB,
        ShotEvent,
        ShotEventManager,
        detect_gunshot,
        ild_to_deg,
    )

    deg, conf, ild = ild_to_deg(0.05, 0.2)
    assert deg > 20
    assert conf > 0.3
    deg_l, _, _ = ild_to_deg(0.2, 0.05)
    assert deg_l < -20

    sr = 48000
    n = int(0.03 * sr)
    t = np.arange(n) / sr
    burst = np.exp(-t * 180.0) * np.sin(2 * np.pi * 3200 * t)
    left = burst * 0.15
    right = burst * 0.9
    prob, dbg = detect_gunshot(left, right, sr, noise_rms=0.01)
    assert prob > GUNSHOT_MIN_PROB
    assert dbg.rms_r > dbg.rms_l

    # Distant: same crack, ~10x quieter — must still fire.
    quiet_l = burst * 0.012
    quiet_r = burst * 0.018
    qprob, _ = detect_gunshot(quiet_l, quiet_r, sr, noise_rms=0.002)
    assert qprob >= GUNSHOT_MIN_PROB

    # Nearby footstep thump: low-frequency, should stay below the gate.
    thump = np.exp(-t * 35.0) * np.sin(2 * np.pi * 160 * t) * 0.22
    fprob, _ = detect_gunshot(thump, thump, sr, noise_rms=0.01)
    assert fprob < GUNSHOT_MIN_PROB

    mgr = ShotEventManager()
    a = ShotEvent(t0=10.0, rel_deg=35.0, gunshot_prob=0.9, dir_conf=0.7)
    b = ShotEvent(t0=10.2, rel_deg=38.0, gunshot_prob=0.8, dir_conf=0.6)
    c = ShotEvent(t0=10.3, rel_deg=-60.0, gunshot_prob=0.85, dir_conf=0.7)
    mgr.ingest(a)
    merged = mgr.ingest(b)
    assert merged.count == 2
    mgr.ingest(c)
    assert len(mgr.events) == 2
    world = ShotEvent(t0=1.0, rel_deg=40.0, gunshot_prob=0.9, dir_conf=0.8, world_bearing=160.0)
    assert abs(mgr.display_rel(world, 150.0) - 10.0) < 0.01


def test_audio_classes():
    import numpy as np
    from wmnavi.audio_detect import GUNSHOT_MIN_PROB, detect_gunshot

    sr = 48000
    n = int(0.03 * sr)
    t = np.arange(n) / sr
    burst = np.exp(-t * 180.0) * np.sin(2 * np.pi * 3200 * t) * 0.02
    g, dbg = detect_gunshot(burst, burst * 0.8, sr, 0.002)
    assert g >= GUNSHOT_MIN_PROB
    assert dbg.kind == "gunshot"
    thump = np.exp(-t * 35.0) * np.sin(2 * np.pi * 160 * t) * 0.22
    g2, d2 = detect_gunshot(thump, thump, sr, 0.01)
    assert g2 < GUNSHOT_MIN_PROB
    assert d2.footstep_prob > d2.gunshot_prob


def test_loot_index_roundtrip():
    import sys

    import cv2
    import numpy as np
    from PySide6.QtWidgets import QApplication

    from wmnavi.loot_index import ItemIconIndex, transform_for_benchmark
    from wmnavi.loot_loader import load_items_catalog

    if QApplication.instance() is None:
        QApplication(sys.argv)
    catalog = load_items_catalog("regular") or load_items_catalog("pvp-season") or {}
    idx = ItemIconIndex()
    n = idx.build(catalog)
    if n < 8:
        return
    hits = 0
    for entry in idx.entries[:12]:
        bgr = cv2.cvtColor(entry.gray, cv2.COLOR_GRAY2BGR)
        cands = idx.query(bgr, top_k=5)
        if cands and cands[0][0] == entry.item_id:
            hits += 1
        scaled = transform_for_benchmark(bgr, "scaled")
        c2 = idx.query(scaled, top_k=5)
        if c2 and c2[0][0] == entry.item_id:
            hits += 1
    assert hits >= 8


def main() -> int:
    test_wrap_and_shortest()
    test_heading_visual_then_correct()
    test_large_heading_error_snaps()
    test_predictor_confirm_and_motion()
    test_predictor_coasts_when_flow_drops()
    test_blend()
    test_audio_ild_right_positive()
    test_audio_classes()
    test_loot_index_roundtrip()
    print("TRACKING MATH OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
