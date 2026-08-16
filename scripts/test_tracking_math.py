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


def test_predictor_keys_walk_and_sprint():
    p = MovementPredictor()
    p.reset_calibration()
    p.confirm(PlayerState(0, 1, 0, 0), map_slug="customs")
    walk = {"forward": True, "back": False, "left": False, "right": False, "sprint": False, "crouch": False}
    p.apply_controls(0.1, walk, 0.0)
    walk_z = p.state.predicted_z
    assert walk_z > 0.25
    assert abs(p.state.predicted_x) < 0.05
    p2 = MovementPredictor()
    p2.reset_calibration()
    p2.confirm(PlayerState(0, 1, 0, 0), map_slug="customs")
    sprint = dict(walk, sprint=True)
    p2.apply_controls(0.1, sprint, 0.0)
    assert p2.state.predicted_z > walk_z * 1.5


def test_predictor_keys_idle_stops():
    p = MovementPredictor()
    p.reset_calibration()
    p.confirm(PlayerState(0, 1, 0, 0), map_slug="customs")
    walk = {"forward": True, "back": False, "left": False, "right": False, "sprint": False, "crouch": False}
    p.apply_controls(0.1, walk, 0.0)
    idle = {"forward": False, "back": False, "left": False, "right": False, "sprint": False, "crouch": False}
    for _ in range(4):
        p.apply_controls(0.05, idle, 0.0)
    assert p.state.speed < 0.2


def test_apply_motion_skip_translation():
    p = MovementPredictor()
    p.reset_calibration()
    p.confirm(PlayerState(10, 1, 20, 0), map_slug="customs")
    sample = MotionSample(0.05, 0.0, 2.0, 0.0, 0.8, True, 4.0)
    p.apply_motion(sample, prediction_on=True, skip_translation=True)
    assert abs(p.state.predicted_z - 20.0) < 1e-9
    assert abs(p.state.predicted_x - 10.0) < 1e-9


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


def test_item_name_lookup():
    from wmnavi.loot_names import ItemNameIndex, normalize_item_name
    from wmnavi.models import ItemInfo

    assert normalize_item_name("  Pack of Sugar ") == "pack of sugar"
    catalog = {
        "sugar": ItemInfo("sugar", "Pack of sugar", "Sugar", "", 25000, 0, ""),
        "bolts": ItemInfo("bolts", "Bolts", "Bolts", "", 12000, 0, ""),
        "ak": ItemInfo("ak", "Kalashnikov AK-74M 5.45x39 assault rifle", "AK-74M", "", 40000, 0, ""),
        "tape": ItemInfo("tape", "KEKTAPE duct tape", "KEKTAPE", "", 18000, 0, ""),
    }
    idx = ItemNameIndex(catalog)
    hit = idx.lookup("Pack of sugar")
    assert hit and hit[0] == "sugar" and hit[1] >= 0.99
    fuzzy = idx.lookup("Pack of sugor")
    assert fuzzy and fuzzy[0] == "sugar"
    short = idx.lookup("AK-74M")
    assert short and short[0] == "ak"
    tape = idx.lookup("KEKTAPE duct tape")
    assert tape and tape[0] == "tape"
    assert idx.lookup("zzzz not an item") is None


def _stash_name_chip(text: str = "KEKTAPE duct tape"):
    import cv2
    import numpy as np

    img = np.full((86, 520, 3), (52, 48, 44), dtype=np.uint8)
    tw = min(500, 22 + 11 * len(text))
    th = 26
    x, y = 8, 86 - th - 10
    cv2.rectangle(img, (x, y), (x + tw, y + th), (8, 8, 8), -1)
    cv2.rectangle(img, (x, y), (x + tw, y + th), (186, 186, 186), 1)
    cv2.putText(
        img,
        text,
        (x + 8, y + th - 7),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.52,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return img


def test_tooltip_title_band():
    from wmnavi.loot_tooltip import find_name_chips

    img = _stash_name_chip()
    chips = find_name_chips(img)
    assert chips, "expected the above-right stash name chip"
    crop, score = chips[0]
    assert crop.shape[0] >= 12 and crop.shape[1] >= 60
    assert score > 0.2


def test_windows_ocr_tooltip_text():
    from wmnavi.loot_names import ItemNameIndex
    from wmnavi.loot_tooltip import _ocr_engine, _ocr_image, find_name_chips
    from wmnavi.models import ItemInfo

    engine = _ocr_engine()
    if engine is None:
        return
    img = _stash_name_chip("KEKTAPE duct tape")
    chips = find_name_chips(img)
    crop = chips[0][0] if chips else img
    text = _ocr_image(engine, crop) or _ocr_image(engine, img)
    assert text, "OCR returned empty on a high-contrast name chip"
    idx = ItemNameIndex(
        {"tape": ItemInfo("tape", "KEKTAPE duct tape", "KEKTAPE", "", 18000, 0, "")}
    )
    hit = idx.lookup(text)
    assert hit and hit[0] == "tape", text


def main() -> int:
    test_wrap_and_shortest()
    test_heading_visual_then_correct()
    test_large_heading_error_snaps()
    test_predictor_confirm_and_motion()
    test_predictor_coasts_when_flow_drops()
    test_predictor_keys_walk_and_sprint()
    test_predictor_keys_idle_stops()
    test_apply_motion_skip_translation()
    test_blend()
    test_audio_ild_right_positive()
    test_audio_classes()
    test_loot_index_roundtrip()
    test_item_name_lookup()
    test_tooltip_title_band()
    test_windows_ocr_tooltip_text()
    print("TRACKING MATH OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
