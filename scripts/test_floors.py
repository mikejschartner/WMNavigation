"""Streets / Customs / Factory / Shoreline floor auto-select checks."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wmnavi.data_loader import get_interactive_map
from wmnavi.floors import (
    build_floor_options,
    build_house_index,
    floor_for_player,
    floor_for_y,
    keep_ground_map_art,
    loot_points_from_layers,
    overlay_boxes_from_floors,
)
from wmnavi.paths import cache_dir


def _label(floor) -> str:
    return "" if floor is None else floor.label


def _player(slug: str, x: float, z: float, y: float, houses=None) -> str:
    meta = get_interactive_map(slug)
    floors = build_floor_options(meta)
    return _label(floor_for_player(x, z, y, floors, meta, houses=houses))


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


def _synthetic_shoreline_houses():
    """Loot clusters matching raid-tested cottages / village / short house."""
    pts = [
        # Cottage (upstairs loot exists)
        (132.9, -48.15, 124.1),
        (135.9, -48.15, 120.3),
        (147.6, -48.15, 123.2),
        (145.5, -48.15, 126.0),
        (147.6, -48.14, 126.3),
        (138.6, -48.14, 123.2),
        (149.4, -48.14, 129.8),
        (144.1, -47.62, 130.4),
        (146.2, -47.05, 122.8),
        (145.3, -46.98, 129.0),
        (143.4, -46.86, 124.0),
        (135.2, -46.72, 120.6),
        (140.5, -45.24, 130.1),
        (135.8, -45.02, 124.6),
        (133.4, -44.63, 126.7),
        (143.7, -44.46, 125.1),
        (137.9, -44.35, 129.5),
        (142.1, -44.27, 130.2),
        (140.3, -44.24, 121.5),
        (135.9, -43.93, 130.7),
        # Short house west of village (no upstairs loot)
        (278.30, -52.32, 159.68),
        (279.50, -52.10, 162.00),
        (280.80, -51.90, 164.50),
        (281.50, -51.80, 166.20),
        (282.04, -51.68, 168.02),
        # Village house ground
        (409.17, -54.05, 164.64),
        (411.00, -53.50, 165.50),
        (412.40, -53.10, 166.40),
        (413.85, -52.78, 167.54),
    ]
    meta = get_interactive_map("shoreline")
    floors = build_floor_options(meta)
    houses = build_house_index(pts, overlay_boxes_from_floors(floors))
    return floors, meta, houses


def test_shoreline_house_stories_synthetic():
    floors, meta, houses = _synthetic_shoreline_houses()
    assert houses.house_at(143.89, 124.21) is not None
    assert houses.house_at(282.58, 162.78) is not None
    assert houses.house_at(412.81, 162.26) is not None
    assert houses.house_at(0.0, 0.0) is None

    cottage = _label(floor_for_player(143.89, 124.21, -44.16, floors, meta, houses=houses))
    short = _label(floor_for_player(282.58, 162.78, -50.81, floors, meta, houses=houses))
    village = _label(floor_for_player(412.81, 162.26, -52.53, floors, meta, houses=houses))
    outdoor = _label(floor_for_player(0.0, 0.0, 0.0, floors, meta, houses=houses))
    assert cottage.startswith("Floor 2"), cottage
    assert short.startswith("Floor 2"), short
    assert village.startswith("Floor 1"), village
    assert outdoor.startswith("Floor 1"), outdoor

    floor2 = next(f for f in floors if f.label.startswith("Floor 2"))
    assert keep_ground_map_art(floor2, 143.89, 124.21)
    assert keep_ground_map_art(floor2, 282.58, 162.78)
    assert not keep_ground_map_art(floor2, -250.0, -100.0)

    cottage_house = houses.house_at(143.89, 124.21)
    assert cottage_house is not None
    assert cottage_house.loot_story(-48.15) == 1
    assert cottage_house.loot_story(-43.93) == 2


def test_shoreline_house_stories_cached_raid():
    cache = cache_dir() / "regular_shoreline_layers.json"
    if not cache.exists():
        return
    from wmnavi.map_data_loader import load_map_layers

    meta = get_interactive_map("shoreline")
    floors = build_floor_options(meta)
    data = load_map_layers("shoreline", "regular", map_meta=meta)
    houses = build_house_index(
        loot_points_from_layers(data),
        overlay_boxes_from_floors(floors),
    )
    assert _label(floor_for_player(143.89, 124.21, -44.16, floors, meta, houses=houses)).startswith(
        "Floor 2"
    )
    assert _label(floor_for_player(282.58, 162.78, -50.81, floors, meta, houses=houses)).startswith(
        "Floor 2"
    )
    assert _label(floor_for_player(412.81, 162.26, -52.53, floors, meta, houses=houses)).startswith(
        "Floor 1"
    )
    assert _label(floor_for_player(-250.0, -100.0, 0.0, floors, meta, houses=houses)).startswith(
        "Floor 2"
    )
    assert _label(floor_for_player(0.0, 0.0, 0.0, floors, meta, houses=houses)).startswith("Floor 1")


def test_two_story_gap_split():
    pts = [
        (10.0, 0.0, 10.0),
        (11.0, 0.1, 11.0),
        (12.0, 0.2, 10.5),
        (10.5, 3.4, 11.5),
        (11.5, 3.5, 10.2),
        (12.2, 3.6, 11.0),
    ]
    houses = build_house_index(pts)
    house = houses.house_at(11.0, 11.0)
    assert house is not None
    assert house.split_y is not None
    assert house.player_story(0.15) == 1
    assert house.player_story(3.5) == 2
    assert house.loot_story(0.1) == 1
    assert house.loot_story(3.5) == 2


def main() -> int:
    test_streets_player_floors()
    test_streets_loot_height_only_keeps_street_on_floor1()
    test_customs_dorms_not_outdoor()
    test_factory_third_no_band_gap()
    test_shoreline_resort_boxes()
    test_shoreline_house_stories_synthetic()
    test_shoreline_house_stories_cached_raid()
    test_two_story_gap_split()
    print("FLOOR SELECT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
