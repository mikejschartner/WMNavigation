"""Synthetic collision and ping-state tests. No Tarkov window."""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wmnavi.map_geometry import load_collision, raycast, save_collision, synthetic_test_world
from wmnavi.ping_manager import PING_DEATH, PING_NORMAL, PingManager, ping_distance


def test_synthetic_wall_hill_building_miss():
    world = synthetic_test_world()
    assert world.ready()

    wall = raycast(world, (0.0, 4.0, 12.0), 90.0, 0.0)
    assert wall is not None, "expected wall hit"
    assert wall.kind == "mesh"
    assert abs(wall.x - 70.0) < 2.5
    assert 4.0 < wall.z < 41.0

    pitch = math.degrees(math.atan2(13.0, 40.0))
    hill = raycast(world, (40.0, 5.0, 0.0), 0.0, pitch)
    assert hill is not None, "expected hill hit"
    assert hill.kind == "terrain"
    assert abs(hill.x - 40.0) < 10.0
    assert 18.0 < hill.z < 55.0

    building = raycast(world, (97.0, 8.0, 70.0), 0.0, 0.0)
    assert building is not None, "expected building hit"
    assert building.kind == "mesh"
    assert building.z >= 89.0
    assert 89.0 <= building.x <= 105.0

    sky = raycast(world, (0.0, 80.0, 0.0), 0.0, 80.0)
    assert sky is None, "sky ray must fail honestly"


def test_collision_roundtrip(tmp_path=None):
    world = synthetic_test_world()
    save_collision(world, source="synthetic")
    loaded = load_collision("_synthetic")
    assert loaded is not None and loaded.ready()
    hit = raycast(loaded, (0.0, 4.0, 12.0), 90.0, 0.0)
    assert hit is not None
    assert abs(hit.x - 70.0) < 2.5


def test_one_normal_ping_per_owner():
    mgr = PingManager("me")
    first = mgr.make_normal(
        owner_name="Mike",
        map_slug="customs",
        x=1,
        y=2,
        z=3,
        duration_s=30,
        color="#fbbf24",
        predicted_origin=False,
        confidence=1.0,
        distance_m=10,
    )
    mgr.upsert(first)
    second = mgr.make_normal(
        owner_name="Mike",
        map_slug="customs",
        x=9,
        y=2,
        z=8,
        duration_s=30,
        color="#fbbf24",
        predicted_origin=False,
        confidence=1.0,
        distance_m=12,
    )
    mgr.upsert(second)
    normals = [p for p in mgr.active("customs") if p.ping_type == PING_NORMAL]
    assert len(normals) == 1
    assert normals[0].ping_id == second.ping_id
    assert mgr.last_valid is not None
    assert mgr.last_valid.ping_id == second.ping_id


def test_duplicate_mqtt_id_does_not_stack():
    mgr = PingManager("me")
    ping = mgr.make_normal(
        owner_name="Mike",
        map_slug="customs",
        x=1,
        y=1,
        z=1,
        duration_s=30,
        color="#fff",
        predicted_origin=False,
        confidence=1.0,
        distance_m=1,
    )
    mgr.upsert(ping)
    mgr.upsert(ping)
    assert len(mgr.active("customs")) == 1


def test_death_without_last_ping():
    mgr = PingManager("me")
    assert mgr.make_death(duration_s=30, until_raid_end=False) is None


def test_death_uses_last_successful_ping():
    mgr = PingManager("me")
    ping = mgr.make_normal(
        owner_name="Mike",
        map_slug="woods",
        x=11,
        y=4,
        z=22,
        duration_s=30,
        color="#fbbf24",
        predicted_origin=False,
        confidence=1.0,
        distance_m=5,
    )
    mgr.upsert(ping)
    death = mgr.make_death(duration_s=0, until_raid_end=True)
    assert death is not None
    assert death.ping_type == PING_DEATH
    assert death.x == ping.x and death.z == ping.z
    kinds = {p.ping_type for p in mgr.active("woods")}
    assert PING_NORMAL in kinds and PING_DEATH in kinds


def test_raid_reset_clears():
    mgr = PingManager("me")
    ping = mgr.make_normal(
        owner_name="Mike",
        map_slug="customs",
        x=1,
        y=1,
        z=1,
        duration_s=0,
        color="#fff",
        predicted_origin=False,
        confidence=1.0,
        distance_m=1,
    )
    mgr.upsert(ping)
    mgr.make_death(duration_s=0, until_raid_end=True)
    mgr.raid_reset()
    assert mgr.active() == []
    assert mgr.last_valid is None


def test_ping_distance():
    assert abs(ping_distance(0, 0, 0, 3, 4, 0) - 5.0) < 1e-9


def test_expire_drops_old_ping():
    mgr = PingManager("me")
    ping = mgr.make_normal(
        owner_name="Mike",
        map_slug="customs",
        x=1,
        y=1,
        z=1,
        duration_s=0.01,
        color="#fff",
        predicted_origin=False,
        confidence=1.0,
        distance_m=1,
    )
    mgr.upsert(ping)
    time.sleep(0.03)
    assert mgr.active("customs") == []


def test_shoreline_uses_unity_levels():
    from wmnavi.geometry_import import MAP_LEVELS, unity_typetree_error

    assert 25 in MAP_LEVELS["shoreline"]
    assert 17 in MAP_LEVELS["customs"]
    err = unity_typetree_error()
    assert err is None, err


def main() -> int:
    test_synthetic_wall_hill_building_miss()
    test_collision_roundtrip()
    test_one_normal_ping_per_owner()
    test_duplicate_mqtt_id_does_not_stack()
    test_death_without_last_ping()
    test_death_uses_last_successful_ping()
    test_raid_reset_clears()
    test_ping_distance()
    test_expire_drops_old_ping()
    test_shoreline_uses_unity_levels()
    print("PING TESTS OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
