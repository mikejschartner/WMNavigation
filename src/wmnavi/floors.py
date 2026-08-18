"""Floor height ranges from tarkov.dev map metadata.

Numbering matches Tarkov:
  Floor 0 = underground / basement
  Floor 1 = main / ground
  Floor 2+ = upper floors

Each Y belongs to exactly one floor so auto-select changes when you
go from 2nd to 3rd. svg_layer / tile_path drive the visible map plan.

Player auto-select uses building XZ footprints when present (Streets
Concordia, Customs dorms). Shoreline cottages use loot-cluster houses
so Floor 2 haze works without switching to the resort overlay. Loot /
extract pins stay height-only unless they sit in a house footprint.
"""

from __future__ import annotations

from dataclasses import dataclass, field


FOOTPRINT_PAD_M = 8.0

# Shoreline cottages / village (no overlay art). Snap includes doorways.
STORY_SPLIT_M = 2.45
INDOOR_ABOVE_LOOT_M = 0.5
STORY_GAP_M = 3.0
HOUSE_SNAP_M = 5.5
HOUSE_LINK_M = 5.5
HOUSE_MAX_SPAN_M = 28.0
HOUSE_MIN_POINTS = 2


@dataclass(frozen=True)
class FloorExtent:
    min_y: float
    max_y: float
    boxes: tuple[tuple[float, float, float, float], ...] = ()


@dataclass(frozen=True)
class FloorOption:
    label: str
    min_y: float
    max_y: float
    svg_layer: str = ""
    tile_path: str = ""
    kind: str = "main"  # all | underground | main | upper
    extents: tuple[FloorExtent, ...] = field(default_factory=tuple)


def _one_box(item) -> tuple[float, float, float, float] | None:
    """Accept [[x, z], [x, z]] or [[x, z], [x, z], \"name\"]."""
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    a, b = item[0], item[1]
    if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))):
        return None
    if len(a) < 2 or len(b) < 2:
        return None
    try:
        x1, z1 = float(a[0]), float(a[1])
        x2, z2 = float(b[0]), float(b[1])
    except (TypeError, ValueError):
        return None
    return (min(x1, x2), max(x1, x2), min(z1, z2), max(z1, z2))


def parse_extent_bounds(raw) -> tuple[tuple[float, float, float, float], ...]:
    boxes: list[tuple[float, float, float, float]] = []
    if not raw:
        return ()
    if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], (list, tuple)):
        first = raw[0]
        # A single bound written as [[x,z],[x,z],name] vs a list of bounds.
        if first and not isinstance(first[0], (list, tuple)):
            box = _one_box(raw)
            return (box,) if box else ()
    for item in raw:
        box = _one_box(item)
        if box:
            boxes.append(box)
    return tuple(boxes)


def parse_layer_extents(layer: dict | None) -> tuple[FloorExtent, ...]:
    out: list[FloorExtent] = []
    for ext in (layer or {}).get("extents") or []:
        height = ext.get("height")
        if not height or len(height) < 2:
            continue
        try:
            lo, hi = float(height[0]), float(height[1])
        except (TypeError, ValueError):
            continue
        boxes = parse_extent_bounds(ext.get("bounds"))
        out.append(FloorExtent(lo, hi, boxes))
    return tuple(out)


def _primary_extent(extents: list[dict] | tuple[FloorExtent, ...]) -> tuple[float, float] | None:
    """Height band for loot pins / dropdown span.

    Prefer an unbound (no-box) band so Streets Floor 2 stays [10, 15)
    instead of the indoor [3, 6.5] overlay. If every extent is boxed
    (Customs), use the lowest-starting band.
    """
    unbound: list[tuple[float, float]] = []
    boxed: list[tuple[float, float]] = []
    for ext in extents or []:
        if isinstance(ext, FloorExtent):
            lo, hi, boxes = ext.min_y, ext.max_y, ext.boxes
        else:
            height = ext.get("height")
            if not height or len(height) < 2:
                continue
            try:
                lo, hi = float(height[0]), float(height[1])
            except (TypeError, ValueError):
                continue
            boxes = parse_extent_bounds(ext.get("bounds"))
        (boxed if boxes else unbound).append((lo, hi))
    if unbound:
        return min(unbound, key=lambda r: r[0])
    if boxed:
        return min(boxed, key=lambda r: r[0])
    return None


def _is_underground_name(name: str) -> bool:
    key = (name or "").strip().lower()
    return any(
        token in key
        for token in ("underground", "basement", "bunker", "tunnel", "garage", "technical")
    )


def build_floor_options(map_meta: dict | None) -> list[FloorOption]:
    ground_svg = str((map_meta or {}).get("svgLayer") or "")
    ground_tiles = str((map_meta or {}).get("tilePath") or "")
    options = [
        FloorOption(
            "All Floors",
            -10000.0,
            10000.0,
            svg_layer=ground_svg,
            tile_path=ground_tiles,
            kind="all",
        )
    ]
    if not map_meta:
        options.append(FloorOption("Floor 1 (Main)", -10000.0, 10000.0, kind="main"))
        return options

    underground: list[tuple[str, float, float, str, str, tuple[FloorExtent, ...]]] = []
    uppers: list[tuple[str, float, float, str, str, tuple[FloorExtent, ...]]] = []
    for layer in map_meta.get("layers") or []:
        name = str(layer.get("name") or "Floor")
        parsed = parse_layer_extents(layer)
        span = _primary_extent(parsed) or _primary_extent(layer.get("extents") or [])
        if not span:
            continue
        low, high = span
        svg = str(layer.get("svgLayer") or "")
        tiles = str(layer.get("tilePath") or "")
        row = (name, low, high, svg, tiles, parsed)
        if _is_underground_name(name):
            underground.append(row)
        else:
            uppers.append(row)

    uppers.sort(key=lambda b: b[1])

    ug_high = -10000.0
    if underground:
        ug_name, _lo, ug_high, ug_svg, ug_tiles, ug_ext = underground[0]
        ug_high = max(b[2] for b in underground)
        if uppers and uppers[0][1] < ug_high:
            ug_high = uppers[0][1]
        extra = ug_name if ug_name.lower() not in {"underground", "basement"} else ""
        label = f"Floor 0 ({extra})" if extra else "Floor 0 (Underground)"
        all_ug = tuple(ext for row in underground for ext in row[5])
        options.append(
            FloorOption(
                label,
                -10000.0,
                ug_high,
                svg_layer=ug_svg or underground[0][3],
                tile_path=ug_tiles or underground[0][4],
                kind="underground",
                extents=all_ug or ug_ext,
            )
        )

    start_n = 2
    main_low = ug_high if underground else -10000.0
    if uppers and (uppers[0][1] - main_low) > 0.2:
        options.append(
            FloorOption(
                "Floor 1 (Main)",
                main_low,
                uppers[0][1],
                svg_layer=ground_svg,
                tile_path=ground_tiles,
                kind="main",
            )
        )
        start_n = 2
    elif not uppers:
        options.append(
            FloorOption(
                "Floor 1 (Main)",
                main_low,
                10000.0,
                svg_layer=ground_svg,
                tile_path=ground_tiles,
                kind="main",
            )
        )
        return options
    else:
        start_n = 1

    for i, (_name, low, _high, svg, tiles, parsed) in enumerate(uppers):
        n = start_n + i
        band_low = low if i else options[-1].max_y
        band_high = uppers[i + 1][1] if i + 1 < len(uppers) else 10000.0
        if band_high <= band_low:
            continue
        label = "Floor 1 (Main)" if n == 1 else f"Floor {n}"
        kind = "main" if n == 1 else "upper"
        options.append(
            FloorOption(
                label,
                band_low,
                band_high,
                svg_layer=svg if kind == "upper" else (svg or ground_svg),
                tile_path=tiles if kind == "upper" else (tiles or ground_tiles),
                kind=kind,
                extents=parsed,
            )
        )

    return options


def _in_height(y: float, min_y: float, max_y: float) -> bool:
    if max_y >= 9999:
        return min_y <= y <= max_y
    return min_y <= y < max_y


def marker_on_floor(y: float, floor: FloorOption) -> bool:
    return _in_height(y, floor.min_y, floor.max_y)


def floor_for_y(y: float, floors: list[FloorOption]) -> FloorOption | None:
    """Pick the named floor containing Y (skips 'All Floors'). Height only."""
    named = [f for f in floors if f.label.lower() != "all floors"]
    for floor in named:
        if marker_on_floor(y, floor):
            return floor
    if not named:
        return None
    return min(named, key=lambda f: min(abs(y - f.min_y), abs(y - f.max_y)))


def _usable_ground_range(map_meta: dict | None) -> tuple[float, float] | None:
    """Real outdoor band, or None for dummy ranges (Customs −1000..1000, Shoreline max < 2)."""
    raw = (map_meta or {}).get("heightRange")
    if not raw or not isinstance(raw, (list, tuple)) or len(raw) < 2:
        return None
    try:
        lo, hi = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    if (hi - lo) > 200.0:
        return None
    if hi < 2.0:
        return None
    return (lo, hi)


def _in_footprint(x: float, z: float, box: tuple[float, float, float, float], pad: float) -> bool:
    min_x, max_x, min_z, max_z = box
    return (min_x - pad) <= x <= (max_x + pad) and (min_z - pad) <= z <= (max_z + pad)


def _named_floors(floors: list[FloorOption]) -> list[FloorOption]:
    return [f for f in floors if f.label.lower() != "all floors"]


def floor_number(floor: FloorOption) -> int | None:
    if floor.kind == "all":
        return None
    if floor.kind == "underground":
        return 0
    if floor.kind == "main":
        return 1
    for token in floor.label.replace("(", " ").replace(")", " ").split():
        if token.isdigit():
            return int(token)
    return None


def _floor1(named: list[FloorOption]) -> FloorOption | None:
    for floor in named:
        if floor.kind == "main" or floor_number(floor) == 1:
            return floor
    return named[0] if named else None


def _floor_n(named: list[FloorOption], n: int) -> FloorOption | None:
    for floor in named:
        if floor_number(floor) == n:
            return floor
    return None


def xz_in_overlay_floor(x: float, z: float, floor: FloorOption, pad: float = FOOTPRINT_PAD_M) -> bool:
    """True if XZ sits in a boxed overlay footprint (height ignored — for map art)."""
    for ext in floor.extents:
        if not ext.boxes:
            continue
        if any(_in_footprint(x, z, box, pad) for box in ext.boxes):
            return True
    return False


def keep_ground_map_art(floor: FloorOption, x: float | None, z: float | None) -> bool:
    """Upper floor selected, but the player is not on that overlay plan (cottages)."""
    if floor.kind != "upper":
        return False
    if x is None or z is None:
        return False
    return not xz_in_overlay_floor(x, z, floor)


def overlay_boxes_from_floors(floors: list[FloorOption]) -> tuple[tuple[float, float, float, float], ...]:
    boxes: list[tuple[float, float, float, float]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for floor in floors:
        if floor.kind != "upper":
            continue
        for ext in floor.extents:
            for box in ext.boxes:
                if box not in seen:
                    seen.add(box)
                    boxes.append(box)
    return tuple(boxes)


def loot_points_from_layers(data) -> list[tuple[float, float, float]]:
    pts: list[tuple[float, float, float]] = []
    for spot in getattr(data, "loose_loot", None) or []:
        try:
            pts.append((float(spot.x), float(spot.y), float(spot.z)))
        except (TypeError, ValueError, AttributeError):
            continue
    containers = getattr(data, "containers", None) or {}
    if isinstance(containers, dict):
        values = containers.values()
    else:
        values = containers
    for container in values:
        for spot in getattr(container, "spots", None) or []:
            try:
                pts.append((float(spot.x), float(spot.y), float(spot.z)))
            except (TypeError, ValueError, AttributeError):
                continue
    return pts


def _boxes_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[1] < b[0] or a[0] > b[1] or a[3] < b[2] or a[2] > b[3])


def _story_split_y(ys: list[float]) -> float | None:
    if len(ys) < 2:
        return None
    ordered = sorted(ys)
    best_gap = 0.0
    split: float | None = None
    for lo, hi in zip(ordered, ordered[1:]):
        gap = hi - lo
        if gap >= STORY_GAP_M and gap > best_gap:
            best_gap = gap
            split = (lo + hi) / 2.0
    return split


@dataclass(frozen=True)
class HouseFootprint:
    min_x: float
    max_x: float
    min_z: float
    max_z: float
    loot_ys: tuple[float, ...]
    split_y: float | None = None

    def contains(self, x: float, z: float, pad: float = HOUSE_SNAP_M) -> bool:
        return _in_footprint(x, z, (self.min_x, self.max_x, self.min_z, self.max_z), pad)

    def player_story(self, y: float) -> int:
        ys = self.loot_ys
        if not ys:
            return 1
        if self.split_y is not None and y >= self.split_y:
            return 2
        if (y - min(ys)) >= STORY_SPLIT_M:
            return 2
        if (y - max(ys)) >= INDOOR_ABOVE_LOOT_M:
            return 2
        return 1

    def loot_story(self, y: float) -> int:
        if not self.loot_ys:
            return 1
        if self.split_y is not None:
            return 2 if y >= self.split_y else 1
        return 2 if (y - min(self.loot_ys)) >= STORY_SPLIT_M else 1


@dataclass(frozen=True)
class HouseIndex:
    houses: tuple[HouseFootprint, ...] = ()

    def house_at(self, x: float, z: float) -> HouseFootprint | None:
        hits = [h for h in self.houses if h.contains(x, z)]
        if not hits:
            return None
        return min(hits, key=lambda h: (h.max_x - h.min_x) * (h.max_z - h.min_z))

    def player_story(self, x: float, z: float, y: float) -> int | None:
        house = self.house_at(x, z)
        return None if house is None else house.player_story(y)

    def loot_story(self, x: float, z: float, y: float) -> int | None:
        house = self.house_at(x, z)
        return None if house is None else house.loot_story(y)


def build_house_index(
    points: list[tuple[float, float, float]] | tuple[tuple[float, float, float], ...],
    overlay_boxes: tuple[tuple[float, float, float, float], ...] | list[tuple[float, float, float, float]] = (),
) -> HouseIndex:
    """Union-find loot/container spots into small building footprints."""
    boxes = tuple(overlay_boxes or ())
    pts = [
        (float(x), float(y), float(z))
        for x, y, z in points
        if not any(_in_footprint(float(x), float(z), box, FOOTPRINT_PAD_M) for box in boxes)
    ]
    n = len(pts)
    if n < HOUSE_MIN_POINTS:
        return HouseIndex()
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    link2 = HOUSE_LINK_M * HOUSE_LINK_M
    for i in range(n):
        xi, zi = pts[i][0], pts[i][2]
        for j in range(i + 1, n):
            dx = xi - pts[j][0]
            dz = zi - pts[j][2]
            if dx * dx + dz * dz <= link2:
                union(i, j)

    groups: dict[int, list[tuple[float, float, float]]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(pts[i])

    houses: list[HouseFootprint] = []
    for group in groups.values():
        if len(group) < HOUSE_MIN_POINTS:
            continue
        xs = [p[0] for p in group]
        zs = [p[2] for p in group]
        ys = [p[1] for p in group]
        span = max(max(xs) - min(xs), max(zs) - min(zs))
        if span >= HOUSE_MAX_SPAN_M:
            continue
        box = (min(xs), max(xs), min(zs), max(zs))
        if any(_boxes_overlap(box, overlay) for overlay in boxes):
            continue
        houses.append(
            HouseFootprint(
                min_x=box[0],
                max_x=box[1],
                min_z=box[2],
                max_z=box[3],
                loot_ys=tuple(ys),
                split_y=_story_split_y(ys),
            )
        )
    return HouseIndex(tuple(houses))


def floor_for_player(
    x: float,
    z: float,
    y: float,
    floors: list[FloorOption],
    map_meta: dict | None = None,
    houses: HouseIndex | None = None,
) -> FloorOption | None:
    """Live player / overlay auto-select. Boxed interiors beat raw height."""
    named = _named_floors(floors)
    if not named:
        return None

    boxed: list[tuple[float, FloorOption]] = []
    for floor in named:
        for ext in floor.extents:
            if not ext.boxes:
                continue
            if not _in_height(y, ext.min_y, ext.max_y):
                continue
            if any(_in_footprint(x, z, box, FOOTPRINT_PAD_M) for box in ext.boxes):
                boxed.append((ext.min_y, floor))
                break
    if boxed:
        return max(boxed, key=lambda row: row[0])[1]

    main = _floor1(named)
    if houses is not None:
        story = houses.player_story(x, z, y)
        if story == 2:
            return _floor_n(named, 2) or main
        if story == 1:
            return main

    ground = _usable_ground_range(map_meta)
    if ground is not None:
        glo, ghi = ground
        if glo <= y < ghi:
            return main
        if y < glo:
            for floor in named:
                if floor.kind == "underground":
                    return floor

    for floor in named:
        for ext in floor.extents:
            if ext.boxes:
                continue
            if _in_height(y, ext.min_y, ext.max_y):
                return floor

    return main
