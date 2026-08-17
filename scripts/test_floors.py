"""Streets / Customs / Factory / Shoreline floor auto-select checks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wmnavi.data_loader import get_interactive_map
from wmnavi.floors import build_floor_options, floor_for_player, floor_for_y


def _label(floor) -> str:
    return "" if floor is None else floor.label


def _player(slug: str, x: float, z: float, y: float) -> str:
    meta = get_interactive_map(slug)
    floors = build_floor_options(meta)
    return _label(floor_for_player(x, z, y, floors, meta))


def test_streets_player_floors():
    assert _player("streets-of-tarkov", 0, 0, 4.0).startswith("Floor 1")
    assert _player("streets-of-tarkov", 0, 0, 8.0).startswith("Floor 1")
    assert _player("streets-of-tarkov", 140, 362, 1.5).startswith("Floor 1")
    assert _player("streets-of-tarkov", 140, 362, 4.2).startswith("Floor 2")
    assert _player("streets-of-tarkov", 140, 362, 7.2).startswith("Floor 3")
    assert _player("streets-of-tarkov", 66, 305, 5.0).startswith("Floor 2")
    assert _player("streets-of-tarkov", 66, 305, 7.5).startswith("Floor 3")
    assert _player("streets-of-tarkov", -55, 55, 6.0).startswith("Floor 2")
    assert _player("streets-of-tarkov", -128, -35, 5.8).startswith("Floor 2")
    assert _player("streets-of-tarkov", 10, 10, -8.0).startswith("Floor 0")
    assert _player("streets-of-tarkov", 0, 0, 10.5).startswith("Floor 2")
    assert _player("streets-of-tarkov", 0, 0, 15.5).startswith("Floor 3")


def test_streets_loot_height_only_keeps_street_on_floor1():
    meta = get_interactive_map("streets-of-tarkov")
    floors = build_floor_options(meta)
    assert _label(floor_for_y(4.0, floors)).startswith("Floor 1")
    assert _label(floor_for_y(8.0, floors)).startswith("Floor 1")
    assert _label(floor_for_y(10.5, floors)).startswith("Floor 2")


def test_customs_dorms_not_outdoor():
    assert _player("customs", 0, 0, 4.0).startswith("Floor 1")
    assert _player("customs", 200, 150, 4.0).startswith("Floor 2")


def test_factory_third_no_band_gap():
    assert _player("factory", 20, 20, 6.0).startswith("Floor 3")
    assert _player("factory", 20, 20, 4.0).startswith("Floor 2")
    assert _player("factory", 20, 20, 1.0).startswith("Floor 1")


def test_shoreline_resort_boxes():
    assert _player("shoreline", 0, 0, 0.0).startswith("Floor 1")
    assert _player("shoreline", 0, 0, 8.0).startswith("Floor 1")
    assert _player("shoreline", -250, -100, 0.0).startswith("Floor 2")
    assert _player("shoreline", -250, -100, 3.0).startswith("Floor 3")


def main() -> int:
    test_streets_player_floors()
    test_streets_loot_height_only_keeps_street_on_floor1()
    test_customs_dorms_not_outdoor()
    test_factory_third_no_band_gap()
    test_shoreline_resort_boxes()
    print("FLOOR SELECT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
