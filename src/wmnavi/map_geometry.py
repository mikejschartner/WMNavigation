"""Cached collision for ping raycasts: heightfield + triangle BVH.

Runtime is one mmap'd file per current map. Import writes this offline.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .applog import get_logger
from .paths import geometry_dir

log = get_logger("wmnavi.geometry")

MAGIC = b"WMNG"
VERSION = 1
MAX_RAY_M = 420.0


@dataclass
class RayHit:
    x: float
    y: float
    z: float
    distance: float
    kind: str  # terrain | mesh
    source: str = "collision"


@dataclass
class HeightField:
    origin_x: float
    origin_z: float
    cell: float
    heights: np.ndarray  # (rows, cols) Y

    @property
    def rows(self) -> int:
        return int(self.heights.shape[0])

    @property
    def cols(self) -> int:
        return int(self.heights.shape[1])

    def sample(self, x: float, z: float) -> float | None:
        if self.cell <= 1e-6:
            return None
        gx = (x - self.origin_x) / self.cell
        gz = (z - self.origin_z) / self.cell
        if gx < 0 or gz < 0 or gx >= self.cols - 1 or gz >= self.rows - 1:
            return None
        ix, iz = int(gx), int(gz)
        fx, fz = gx - ix, gz - iz
        h00 = float(self.heights[iz, ix])
        h10 = float(self.heights[iz, ix + 1])
        h01 = float(self.heights[iz + 1, ix])
        h11 = float(self.heights[iz + 1, ix + 1])
        return h00 * (1 - fx) * (1 - fz) + h10 * fx * (1 - fz) + h01 * (1 - fx) * fz + h11 * fx * fz


@dataclass
class BvhNode:
    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float
    left: int = -1
    right: int = -1
    start: int = 0
    count: int = 0


@dataclass
class CollisionWorld:
    slug: str
    height: HeightField | None = None
    tris: np.ndarray | None = None  # (n, 3, 3)
    nodes: list[BvhNode] = field(default_factory=list)
    source: str = "empty"

    def ready(self) -> bool:
        has_h = self.height is not None and self.height.heights.size > 4
        has_m = self.tris is not None and len(self.tris) > 0
        return has_h or has_m


def geometry_paths(slug: str) -> tuple[Path, Path]:
    folder = geometry_dir() / slug
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "collision.bin", folder / "metadata.json"


def load_collision(slug: str) -> CollisionWorld | None:
    bin_path, meta_path = geometry_paths(slug)
    if not bin_path.exists():
        return None
    try:
        raw = bin_path.read_bytes()
        world = _unpack(raw, slug)
    except Exception:
        return None
    meta: dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            world.source = str(meta.get("source") or world.source)
        except (OSError, json.JSONDecodeError):
            meta = {}
    if world.ready() and not meta.get("y_align_applied"):
        extra = align_heightfield_to_questie(world)
        if extra:
            meta.update(extra)
            meta["y_align_applied"] = True
            try:
                save_collision(world, source=str(meta.get("source") or world.source), extra_meta=meta)
            except OSError:
                log.warning("could not write Y-aligned collision for %s", slug)
    return world if world.ready() else None


def save_collision(world: CollisionWorld, *, source: str, extra_meta: dict | None = None) -> Path:
    bin_path, meta_path = geometry_paths(world.slug)
    bin_path.write_bytes(_pack(world))
    meta = {
        "slug": world.slug,
        "version": VERSION,
        "source": source,
        "has_heightfield": world.height is not None,
        "triangle_count": 0 if world.tris is None else int(len(world.tris)),
        "height_cells": 0 if world.height is None else int(world.height.heights.size),
    }
    if extra_meta:
        meta.update(extra_meta)
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    world.source = source
    return bin_path


def _questie_ground_points(slug: str) -> list[tuple[float, float, float]]:
    try:
        from .questie_source import find_map_entry
    except Exception:
        return []
    entry = find_map_entry("regular", slug) or find_map_entry("pvp-season", slug)
    if not entry:
        return []
    out: list[tuple[float, float, float]] = []
    for key in ("spawns", "extracts"):
        for item in entry.get(key) or []:
            pos = item.get("position") or {}
            if not isinstance(pos, dict):
                continue
            try:
                out.append((float(pos["x"]), float(pos["y"]), float(pos["z"])))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def fit_heightfield_y(hf_vals: np.ndarray, ref_vals: np.ndarray) -> tuple[float, float] | None:
    """Map imported terrain Y to questie ground Y: ref ≈ a * hf + b."""
    hf = np.asarray(hf_vals, dtype=np.float64).reshape(-1)
    ref = np.asarray(ref_vals, dtype=np.float64).reshape(-1)
    if len(hf) < 16 or len(hf) != len(ref):
        return None
    span = float(hf.max() - hf.min())
    if span < 4.0:
        return 1.0, float(np.median(ref - hf))
    design = np.column_stack([hf, np.ones(len(hf))])
    ab, *_ = np.linalg.lstsq(design, ref, rcond=None)
    a, b = float(ab[0]), float(ab[1])
    if not (0.6 <= a <= 3.5) or not np.isfinite(b):
        return 1.0, float(np.median(ref - hf))
    return a, b


def align_heightfield_to_questie(world: CollisionWorld) -> dict | None:
    """Shift cached terrain Y to spawn/extract height. Does not reimport Unity files."""
    if world.height is None or world.height.heights.size < 8:
        return None
    points = _questie_ground_points(world.slug)
    if len(points) < 16:
        return None
    hf_vals: list[float] = []
    ref_vals: list[float] = []
    for x, y, z in points:
        sample = world.height.sample(x, z)
        if sample is None:
            continue
        hf_vals.append(float(sample))
        ref_vals.append(float(y))
    fit = fit_heightfield_y(np.asarray(hf_vals), np.asarray(ref_vals))
    if fit is None:
        return None
    a, b = fit
    if abs(a - 1.0) < 0.04 and abs(b) < 1.5:
        return {"y_align_a": a, "y_align_b": b, "y_align_n": len(hf_vals), "y_align_applied": True}
    world.height.heights = (world.height.heights.astype(np.float64) * a + b).astype(np.float32)
    log.info("aligned %s terrain Y: y' = %.4f * y + %.3f (%d anchors)", world.slug, a, b, len(hf_vals))
    return {"y_align_a": a, "y_align_b": b, "y_align_n": len(hf_vals)}


def _pack(world: CollisionWorld) -> bytes:
    h = world.height
    if h is None:
        hf = np.zeros((2, 2), dtype=np.float32)
        origin_x = origin_z = cell = 1.0
    else:
        hf = np.asarray(h.heights, dtype=np.float32)
        origin_x, origin_z, cell = h.origin_x, h.origin_z, h.cell
    tris = np.zeros((0, 3, 3), dtype=np.float32) if world.tris is None else np.asarray(world.tris, dtype=np.float32)
    nodes = world.nodes
    header = struct.pack(
        "<4sIfffIIII",
        MAGIC,
        VERSION,
        float(origin_x),
        float(origin_z),
        float(cell),
        int(hf.shape[0]),
        int(hf.shape[1]),
        int(len(tris)),
        int(len(nodes)),
    )
    parts = [header, hf.tobytes(order="C"), tris.tobytes(order="C")]
    for node in nodes:
        parts.append(
            struct.pack(
                "<ffffffiiii",
                node.min_x,
                node.min_y,
                node.min_z,
                node.max_x,
                node.max_y,
                node.max_z,
                node.left,
                node.right,
                node.start,
                node.count,
            )
        )
    return b"".join(parts)


def _unpack(raw: bytes, slug: str) -> CollisionWorld:
    magic, version, ox, oz, cell, rows, cols, n_tris, n_nodes = struct.unpack_from("<4sIfffIIII", raw, 0)
    if magic != MAGIC or version != VERSION:
        raise ValueError("bad collision header")
    off = struct.calcsize("<4sIfffIIII")
    n_h = int(rows) * int(cols)
    hf = np.frombuffer(raw, dtype=np.float32, count=n_h, offset=off).reshape((int(rows), int(cols))).copy()
    off += n_h * 4
    tris = np.frombuffer(raw, dtype=np.float32, count=int(n_tris) * 9, offset=off).reshape((int(n_tris), 3, 3)).copy()
    off += int(n_tris) * 9 * 4
    nodes: list[BvhNode] = []
    node_size = struct.calcsize("<ffffffiiii")
    for _ in range(int(n_nodes)):
        vals = struct.unpack_from("<ffffffiiii", raw, off)
        off += node_size
        nodes.append(BvhNode(*vals))
    height = HeightField(ox, oz, cell, hf) if hf.size else None
    return CollisionWorld(slug=slug, height=height, tris=tris if len(tris) else None, nodes=nodes)


def build_bvh(tris: np.ndarray) -> tuple[list[BvhNode], np.ndarray]:
    """Build a BVH and a triangle array whose leaf ranges are contiguous."""
    src = np.asarray(tris, dtype=np.float32)
    if src.ndim != 3 or len(src) == 0:
        return [], src
    nodes: list[BvhNode] = []
    leaf_groups: list[np.ndarray] = []

    def aabb_of(idx: np.ndarray) -> tuple[float, float, float, float, float, float]:
        chunk = src[idx]
        lo = chunk.reshape(-1, 3).min(axis=0)
        hi = chunk.reshape(-1, 3).max(axis=0)
        return float(lo[0]), float(lo[1]), float(lo[2]), float(hi[0]), float(hi[1]), float(hi[2])

    def recurse(idx: np.ndarray) -> int:
        min_x, min_y, min_z, max_x, max_y, max_z = aabb_of(idx)
        node_i = len(nodes)
        nodes.append(BvhNode(min_x, min_y, min_z, max_x, max_y, max_z))
        if len(idx) <= 8:
            nodes[node_i].start = len(leaf_groups)
            nodes[node_i].count = -1  # leaf placeholder until packed
            leaf_groups.append(np.asarray(idx, dtype=np.int32))
            return node_i
        extent = (max_x - min_x, max_y - min_y, max_z - min_z)
        axis = int(np.argmax(extent))
        centers = src[idx].mean(axis=1)[:, axis]
        order = np.argsort(centers)
        idx = idx[order]
        mid = max(1, len(idx) // 2)
        nodes[node_i].left = recurse(idx[:mid])
        nodes[node_i].right = recurse(idx[mid:])
        return node_i

    recurse(np.arange(len(src), dtype=np.int32))
    ordered: list[np.ndarray] = []
    cursor = 0
    group_i = 0
    for node in nodes:
        if node.count != -1:
            continue
        idx = leaf_groups[group_i]
        group_i += 1
        node.start = cursor
        node.count = int(len(idx))
        node.left = -1
        node.right = -1
        ordered.append(idx)
        cursor += int(len(idx))
    packed = src[np.concatenate(ordered)] if ordered else src
    return nodes, packed


def _ray_aabb(ox, oy, oz, dx, dy, dz, node: BvhNode, max_t: float) -> bool:
    tmin, tmax = 0.0, max_t
    for o, d, mn, mx in (
        (ox, dx, node.min_x, node.max_x),
        (oy, dy, node.min_y, node.max_y),
        (oz, dz, node.min_z, node.max_z),
    ):
        if abs(d) < 1e-12:
            if o < mn or o > mx:
                return False
            continue
        inv = 1.0 / d
        t1 = (mn - o) * inv
        t2 = (mx - o) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        tmin = max(tmin, t1)
        tmax = min(tmax, t2)
        if tmin > tmax:
            return False
    return True


def _ray_triangle(ox, oy, oz, dx, dy, dz, tri) -> float | None:
    v0, v1, v2 = tri
    e1 = v1 - v0
    e2 = v2 - v0
    pvec = np.cross(np.array([dx, dy, dz], dtype=np.float64), e2)
    det = float(np.dot(e1, pvec))
    if abs(det) < 1e-10:
        return None
    inv = 1.0 / det
    tvec = np.array([ox, oy, oz], dtype=np.float64) - v0
    u = float(np.dot(tvec, pvec)) * inv
    if u < 0.0 or u > 1.0:
        return None
    qvec = np.cross(tvec, e1)
    v = float(np.dot(np.array([dx, dy, dz], dtype=np.float64), qvec)) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    t = float(np.dot(e2, qvec)) * inv
    if t <= 1e-4:
        return None
    return t


def _ray_mesh(world: CollisionWorld, ox, oy, oz, dx, dy, dz, max_t: float) -> RayHit | None:
    if world.tris is None or not world.nodes:
        return None
    best_t = max_t
    hit = False
    stack = [0]
    tris = world.tris
    while stack:
        i = stack.pop()
        if i < 0 or i >= len(world.nodes):
            continue
        node = world.nodes[i]
        if not _ray_aabb(ox, oy, oz, dx, dy, dz, node, best_t):
            continue
        if node.count > 0:
            for ti in range(node.start, node.start + node.count):
                t = _ray_triangle(ox, oy, oz, dx, dy, dz, tris[ti])
                if t is not None and t < best_t:
                    best_t = t
                    hit = True
            continue
        if node.right >= 0:
            stack.append(node.right)
        if node.left >= 0:
            stack.append(node.left)
    if not hit:
        return None
    return RayHit(ox + dx * best_t, oy + dy * best_t, oz + dz * best_t, best_t, "mesh")


def _ray_height(hf: HeightField, ox, oy, oz, dx, dy, dz, max_t: float) -> RayHit | None:
    if hf.cell <= 1e-6:
        return None
    step = max(0.35, hf.cell * 0.45)
    prev_above = None
    t = 0.0
    while t <= max_t:
        x = ox + dx * t
        y = oy + dy * t
        z = oz + dz * t
        ground = hf.sample(x, z)
        if ground is not None:
            above = y >= ground
            if prev_above is True and not above:
                # crossed into terrain; binary search
                lo, hi = max(0.0, t - step), t
                for _ in range(12):
                    mid = (lo + hi) * 0.5
                    gy = oy + dy * mid
                    g = hf.sample(ox + dx * mid, oz + dz * mid)
                    if g is None:
                        break
                    if gy >= g:
                        lo = mid
                    else:
                        hi = mid
                ht = hi
                return RayHit(ox + dx * ht, oy + dy * ht, oz + dz * ht, ht, "terrain")
            if prev_above is None and not above and t <= step * 1.5:
                return RayHit(x, ground, z, t, "terrain")
            prev_above = above
        t += step
    return None


def look_direction(yaw_deg: float, pitch_deg: float) -> tuple[float, float, float]:
    yaw = math.radians(float(yaw_deg))
    pitch = math.radians(float(pitch_deg))
    cp = math.cos(pitch)
    return math.sin(yaw) * cp, math.sin(pitch), math.cos(yaw) * cp


def raycast(
    world: CollisionWorld,
    origin: tuple[float, float, float],
    yaw_deg: float,
    pitch_deg: float,
    max_distance: float = MAX_RAY_M,
) -> RayHit | None:
    if not world.ready():
        return None
    dx, dy, dz = look_direction(yaw_deg, pitch_deg)
    length = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    dx, dy, dz = dx / length, dy / length, dz / length
    ox, oy, oz = origin
    max_t = min(MAX_RAY_M, float(max_distance))
    hits: list[RayHit] = []
    if world.height is not None:
        h = _ray_height(world.height, ox, oy, oz, dx, dy, dz, max_t)
        if h:
            hits.append(h)
    mesh = _ray_mesh(world, ox, oy, oz, dx, dy, dz, max_t)
    if mesh:
        hits.append(mesh)
    if not hits:
        return None
    return min(hits, key=lambda h: h.distance)


def box_triangles(min_c: tuple[float, float, float], max_c: tuple[float, float, float]) -> np.ndarray:
    x0, y0, z0 = min_c
    x1, y1, z1 = max_c
    p = {
        0: (x0, y0, z0),
        1: (x1, y0, z0),
        2: (x1, y0, z1),
        3: (x0, y0, z1),
        4: (x0, y1, z0),
        5: (x1, y1, z0),
        6: (x1, y1, z1),
        7: (x0, y1, z1),
    }
    faces = (
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (3, 2, 6, 7),
        (0, 3, 7, 4),
        (1, 5, 6, 2),
    )
    tris = []
    for a, b, c, d in faces:
        tris.append([p[a], p[b], p[c]])
        tris.append([p[a], p[c], p[d]])
    return np.array(tris, dtype=np.float32)


def synthetic_test_world() -> CollisionWorld:
    """Developer mesh: ground, hill, wall, building, valley. Known hits for tests."""
    cell = 1.0
    n = 121
    origin = -10.0
    heights = np.zeros((n, n), dtype=np.float32)
    for iz in range(n):
        for ix in range(n):
            x = origin + ix * cell
            z = origin + iz * cell
            hill = 18.0 * math.exp(-((x - 40.0) ** 2 + (z - 40.0) ** 2) / (18.0**2))
            valley = -6.0 * math.exp(-((x - 10.0) ** 2 + (z - 70.0) ** 2) / (12.0**2))
            heights[iz, ix] = hill + valley
    wall = box_triangles((70.0, 0.0, 5.0), (71.2, 12.0, 40.0))
    building = box_triangles((90.0, 0.0, 90.0), (104.0, 16.0, 104.0))
    tris = np.concatenate([wall, building], axis=0)
    nodes, packed = build_bvh(tris)
    return CollisionWorld(
        slug="_synthetic",
        height=HeightField(origin, origin, cell, heights),
        tris=packed,
        nodes=nodes,
        source="synthetic",
    )
