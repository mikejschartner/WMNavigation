"""Offline collision import from the local Tarkov install. Never runs in raid."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np

from .applog import get_logger
from .map_geometry import CollisionWorld, HeightField, box_triangles, build_bvh, save_collision

log = get_logger("wmnavi.geometry")

# Unity scene files (levelN in EscapeFromTarkov_Data). Terrain first.
# Sound / light / culling / AI / scripts are omitted.
MAP_LEVELS: dict[str, tuple[int, ...]] = {
    "customs": (17, 5, 6, 7, 9, 10, 11, 12, 16, 18, 20, 21, 22, 170, 171, 172, 173, 174, 175, 176),
    "factory": (527, 528, 529, 530, 531, 532, 533, 534),
    "woods": (165, 43),
    "shoreline": (25, 23, 26, 28, 30, 32, 33, 34, 35, 36, 37, 39, 40),
    "interchange": (63, 54, 55, 56, 57, 58, 59, 60, 61, 62, 65),
    "reserve": (140, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 137, 138, 141, 142, 148, 149, 150, 151, 152, 153, 154, 155, 156, 157, 158, 159, 168, 169),
    "lighthouse": (200, 183, 186, 187, 188, 190, 192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 204, 205, 206, 207, 208),
    "streets-of-tarkov": tuple(n for n in range(214, 366) if n not in {211, 212, 213}),
    "the-lab": tuple(range(71, 113)),
    "ground-zero": tuple(range(466, 491)),
    "the-labyrinth": (545, 546, 547, 548, 549, 550),
}

_SKIP_WALK = ("acoustics", "audiobake", "audio", "pocketmap")
_SKIP_SUFFIX = {".xrageo", ".xramap", ".dll", ".exe", ".txt", ".xml", ".json", ".manifest", ".resource"}
_TERRAIN_IDS = {154}
_MESH_IDS = {64, 65}


def default_eft_data_dirs() -> list[Path]:
    roots = [
        Path(r"C:\Battlestate Games\EFT\EscapeFromTarkov_Data"),
        Path(r"D:\Battlestate Games\EFT\EscapeFromTarkov_Data"),
        Path(r"E:\Battlestate Games\EFT\EscapeFromTarkov_Data"),
        Path(r"C:\Battlestate Games\Escape from Tarkov\EscapeFromTarkov_Data"),
        Path.home() / "Battlestate Games" / "EFT" / "EscapeFromTarkov_Data",
    ]
    extra = _steam_eft_dirs()
    out: list[Path] = []
    seen: set[str] = set()
    for path in extra + roots:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def _steam_eft_dirs() -> list[Path]:
    found: list[Path] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam, _typ = winreg.QueryValueEx(key, "SteamPath")
        steam_path = Path(str(steam))
        libraries = [steam_path / "steamapps"]
        vdf = steam_path / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            text = vdf.read_text(encoding="utf-8", errors="ignore")
            for line in text.splitlines():
                if "path" in line.lower() and ":" in line:
                    bits = line.split('"')
                    for bit in bits:
                        if ":\\" in bit or bit.startswith("/"):
                            libraries.append(Path(bit) / "steamapps")
        for lib in libraries:
            for name in ("EscapeFromTarkov", "Escape from Tarkov", "EFT"):
                cand = lib / "common" / name / "EscapeFromTarkov_Data"
                found.append(cand)
    except Exception:
        pass
    return found


def find_eft_data_dir(explicit: str | None = None) -> Path | None:
    if explicit:
        path = Path(explicit)
        if path.name.lower() != "escapefromtarkov_data" and (path / "EscapeFromTarkov_Data").is_dir():
            path = path / "EscapeFromTarkov_Data"
        if path.is_dir():
            return path
    for path in default_eft_data_dirs():
        if path.is_dir():
            return path
    return None


def eft_is_running() -> bool:
    try:
        import ctypes

        from .win_input import find_eft_window

        if find_eft_window():
            return True
        kernel32 = ctypes.windll.kernel32
        snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
        if snapshot == -1:
            return False

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", ctypes.c_ulong),
                ("cntUsage", ctypes.c_ulong),
                ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", ctypes.c_ulong),
                ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        running = False
        if kernel32.Process32FirstW(snapshot, ctypes.byref(pe)):
            while True:
                name = (pe.szExeFile or "").lower()
                if "escapefromtarkov" in name or "bsglauncher" in name:
                    running = True
                    break
                if not kernel32.Process32NextW(snapshot, ctypes.byref(pe)):
                    break
        kernel32.CloseHandle(snapshot)
        return running
    except Exception:
        return False


def _file_hash(path: Path, limit: int = 2_000_000) -> str:
    digest = hashlib.sha1()
    try:
        with path.open("rb") as handle:
            digest.update(handle.read(limit))
            digest.update(str(path.stat().st_size).encode("ascii"))
    except OSError:
        return ""
    return digest.hexdigest()


def _frozen_tpk_paths() -> list[Path]:
    paths: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "UnityPy" / "resources" / "lzma.tpk")
    try:
        import UnityPy

        paths.append(Path(UnityPy.__file__).resolve().parent / "resources" / "lzma.tpk")
    except Exception:
        pass
    return paths


def unity_typetree_error() -> str | None:
    """None if UnityPy can parse Unity objects. Frozen exe needs lzma.tpk packed."""
    try:
        from UnityPy.helpers.Tpk import get_typetree

        get_typetree()
        return None
    except Exception as exc:
        tpk_path = next((p for p in _frozen_tpk_paths() if p.is_file()), None)
        if tpk_path is None:
            return f"Unity type trees missing ({exc})"
        try:
            from functools import cache
            from io import BytesIO

            import UnityPy.helpers.Tpk as tpk_mod
            from tpk_ar import TpkFile, TpkTypeTreeBlob

            raw = tpk_path.read_bytes()

            @cache
            def _get_typetree():
                with BytesIO(raw) as stream:
                    tree = TpkFile.parse(stream).GetDataBlob()
                if not isinstance(tree, TpkTypeTreeBlob):
                    raise TypeError("lzma.tpk is not a type tree")
                return tree

            tpk_mod.get_typetree = _get_typetree
            _get_typetree()
            log.info("Loaded Unity type trees from %s", tpk_path)
            return None
        except Exception as exc2:
            return f"Unity type trees missing ({exc2})"


def _object_kind(obj) -> tuple[str, int]:
    cid = 0
    try:
        cid = int(getattr(obj, "class_id", 0) or 0)
    except Exception:
        cid = 0
    if not cid:
        try:
            cid = int(getattr(getattr(obj, "type", None), "value", 0) or 0)
        except Exception:
            cid = 0
    kind = ""
    try:
        kind = str(getattr(getattr(obj, "type", None), "name", "") or "")
    except Exception:
        kind = ""
    if not kind:
        if cid in _TERRAIN_IDS:
            kind = "TerrainCollider"
        elif cid in _MESH_IDS:
            kind = "MeshCollider" if cid == 64 else "BoxCollider"
    return kind, cid


def candidate_files(data_dir: Path, slug: str) -> list[Path]:
    """Unity levelN scene files for this map. Not acoustic .xrageo / pocket maps."""
    hits: list[Path] = []
    for num in MAP_LEVELS.get(slug) or ():
        path = data_dir / f"level{int(num)}"
        if path.is_file() and path.stat().st_size > 64:
            hits.append(path)
    return hits


def import_map(slug: str, data_dir: Path, on_status=None) -> tuple[CollisionWorld | None, str]:
    """Build collision.bin for one map. Caller must ensure Tarkov is closed."""

    def status(text: str):
        log.info("%s", text)
        if on_status:
            try:
                on_status(text)
            except Exception:
                pass

    files = candidate_files(data_dir, slug)
    if not files:
        return None, f"No Unity scene files for {slug}. Check the Tarkov Data folder."
    try:
        from UnityPy.environment import Environment
        from UnityPy.helpers.MeshHelper import MeshHandler
    except Exception:
        return None, "UnityPy is not installed. Install it to import map collision."
    tpk_err = unity_typetree_error()
    if tpk_err:
        log.warning("%s", tpk_err)
        return None, tpk_err

    height_slices: list[tuple[float, float, float, np.ndarray]] = []
    mesh_groups: list[np.ndarray] = []
    scanned = 0
    first_read_err = ""
    for path in files:
        scanned += 1
        status(f"Reading Unity {path.name} ({scanned}/{len(files)})")
        try:
            env = Environment(path=str(data_dir))
            env.load_file(str(path))
            for asset in list(env.files.values()):
                loader = getattr(asset, "load_dependencies", None)
                if loader:
                    try:
                        loader()
                    except Exception as exc:
                        log.debug("load_dependencies %s: %s", path.name, exc)
        except Exception as exc:
            log.warning("Failed to open Unity %s: %s", path.name, exc)
            continue
        try:
            objects = list(env.objects)
        except Exception as exc:
            log.warning("Failed to list objects in %s: %s", path.name, exc)
            continue
        terrain_objs = []
        collider_objs = []
        for obj in objects:
            kind, cid = _object_kind(obj)
            if kind == "TerrainCollider" or cid in _TERRAIN_IDS:
                terrain_objs.append(obj)
            elif kind in {"MeshCollider", "BoxCollider"} or cid in _MESH_IDS:
                collider_objs.append((kind or "MeshCollider", obj))
        got_t = 0
        got_m = 0
        for obj in terrain_objs:
            parsed = _read_terrain_collider(obj)
            if parsed is not None:
                height_slices.append(parsed)
                got_t += 1
            elif not first_read_err:
                first_read_err = _first_read_error(obj, "TerrainCollider")
        for kind, obj in collider_objs:
            if kind == "MeshCollider":
                tris = _read_mesh_collider(obj, MeshHandler)
            else:
                tris = _read_box_collider(obj)
            if tris is not None and len(tris):
                mesh_groups.append(tris)
                got_m += 1
            elif not first_read_err:
                first_read_err = _first_read_error(obj, kind)
            if len(mesh_groups) > 8000:
                break
        log.info(
            "%s: %d objects, TerrainCollider %d/%d, Mesh/Box %d/%d",
            path.name,
            len(objects),
            got_t,
            len(terrain_objs),
            got_m,
            len(collider_objs),
        )

    if first_read_err:
        log.warning("Unity read error: %s", first_read_err)

    world = CollisionWorld(slug=slug, source="local tarkov colliders")
    merged = _merge_height_slices(height_slices)
    if merged is not None:
        world.height = merged
    if mesh_groups:
        tris = np.concatenate(mesh_groups, axis=0)
        if len(tris) > 250_000:
            tris = tris[:250_000]
        status(f"Building BVH ({len(tris)} triangles)")
        nodes, packed = build_bvh(np.asarray(tris, dtype=np.float32))
        world.tris = packed
        world.nodes = nodes
    if not world.ready():
        extra = f" {first_read_err}" if first_read_err else ""
        return None, f"No terrain or colliders found in Unity scenes for {slug}.{extra}"
    hashes = [_file_hash(p) for p in files[:12]]
    save_collision(
        world,
        source="local tarkov colliders",
        extra_meta={"files": [p.name for p in files[:24]], "hashes": hashes, "scanned": scanned},
    )
    status(f"Saved collision for {slug}")
    return world, "ok"


def _first_read_error(obj, kind: str) -> str:
    try:
        obj.read()
        return ""
    except Exception as exc:
        return f"{kind} read failed: {exc}"


def _ptr_read(ptr):
    if ptr is None:
        return None
    try:
        if int(getattr(ptr, "m_PathID", 0) or 0) == 0:
            return None
    except Exception:
        pass
    try:
        return ptr.read()
    except Exception:
        return None


def _gameobject_transform(component) -> object | None:
    go = _ptr_read(getattr(component, "m_GameObject", None))
    if go is None:
        return None
    for entry in getattr(go, "m_Component", None) or []:
        ptr = getattr(entry, "component", None) or getattr(entry, "m_Component", None)
        inner = _ptr_read(ptr)
        if inner is not None and type(inner).__name__ == "Transform":
            return inner
    return None


def _world_matrix(transform) -> np.ndarray:
    mats: list[np.ndarray] = []
    cur = transform
    depth = 0
    while cur is not None and depth < 24:
        mats.append(_local_matrix(cur))
        father = getattr(cur, "m_Father", None)
        cur = _ptr_read(father)
        depth += 1
    world = np.eye(4, dtype=np.float64)
    for mat in reversed(mats):
        world = world @ mat
    return world


def _local_matrix(transform) -> np.ndarray:
    p = getattr(transform, "m_LocalPosition", None)
    q = getattr(transform, "m_LocalRotation", None)
    s = getattr(transform, "m_LocalScale", None)
    px = py = pz = 0.0
    sx = sy = sz = 1.0
    qx = qy = qz = 0.0
    qw = 1.0
    if p is not None:
        px, py, pz = float(p.x), float(p.y), float(p.z)
    if s is not None:
        sx, sy, sz = float(s.x), float(s.y), float(s.z)
    if q is not None:
        qx, qy, qz, qw = float(q.x), float(q.y), float(q.z), float(q.w)
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    rot = np.array(
        [
            [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
            [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
            [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
        ],
        dtype=np.float64,
    )
    scale = np.diag([sx, sy, sz])
    mat = np.eye(4, dtype=np.float64)
    mat[:3, :3] = rot @ scale
    mat[0, 3] = px
    mat[1, 3] = py
    mat[2, 3] = pz
    return mat


def _transform_points(points: np.ndarray, world: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    flat = pts.reshape(-1, 3)
    ones = np.ones((len(flat), 1), dtype=np.float64)
    out = (world @ np.concatenate([flat, ones], axis=1).T).T[:, :3]
    return out.reshape(pts.shape).astype(np.float32)


def _read_terrain_collider(obj) -> tuple[float, float, float, np.ndarray] | None:
    try:
        col = obj.read()
    except Exception:
        return None
    td = _ptr_read(getattr(col, "m_TerrainData", None))
    if td is None:
        return None
    hm = getattr(td, "m_Heightmap", None)
    if hm is None:
        return None
    raw = getattr(hm, "m_Heights", None)
    if raw is None:
        return None
    res = int(getattr(hm, "m_Resolution", 0) or 0)
    grid = np.array(raw, dtype=np.float32)
    if res <= 1:
        side = int(np.sqrt(grid.size))
        if side * side != grid.size:
            return None
        res = side
    grid = grid.reshape((res, res))
    scale = getattr(hm, "m_Scale", None)
    try:
        cell = float(scale.x)
        size_y = float(scale.y)
    except Exception:
        cell, size_y = 1.0, 1.0
    if cell <= 1e-6:
        cell = 1.0
    grid = grid * (size_y / 65535.0)
    tr = _gameobject_transform(col)
    ox = oz = oy = 0.0
    if tr is not None:
        world = _world_matrix(tr)
        ox, oy, oz = float(world[0, 3]), float(world[1, 3]), float(world[2, 3])
        grid = grid + oy
    return ox, oz, cell, grid


def _merge_height_slices(slices: list[tuple[float, float, float, np.ndarray]]) -> HeightField | None:
    if not slices:
        return None
    cell = float(slices[0][2])
    if cell <= 1e-6:
        return None
    min_x = min(ox for ox, _oz, _c, _g in slices)
    min_z = min(oz for _ox, oz, _c, _g in slices)
    max_x = max(ox + (g.shape[1] - 1) * cell for ox, _oz, _c, g in slices)
    max_z = max(oz + (g.shape[0] - 1) * cell for _ox, oz, _c, g in slices)
    cols = int(round((max_x - min_x) / cell)) + 1
    rows = int(round((max_z - min_z) / cell)) + 1
    cols = max(2, min(cols, 4096))
    rows = max(2, min(rows, 4096))
    out = np.full((rows, cols), np.nan, dtype=np.float32)
    for ox, oz, _c, grid in slices:
        x0 = int(round((ox - min_x) / cell))
        z0 = int(round((oz - min_z) / cell))
        h, w = grid.shape
        x1, z1 = min(cols, x0 + w), min(rows, z0 + h)
        if x1 <= 0 or z1 <= 0 or x0 >= cols or z0 >= rows:
            continue
        sx0, sz0 = max(0, -x0), max(0, -z0)
        dst = out[z0 + sz0 : z1, x0 + sx0 : x1]
        src = grid[sz0 : sz0 + dst.shape[0], sx0 : sx0 + dst.shape[1]]
        mask = np.isnan(dst)
        dst[mask] = src[mask]
        out[z0 + sz0 : z1, x0 + sx0 : x1] = dst
    fill = float(np.nanmedian(out)) if np.isfinite(out).any() else 0.0
    out = np.where(np.isnan(out), fill, out).astype(np.float32)
    return HeightField(min_x, min_z, cell, out)


def _read_mesh_collider(obj, mesh_handler_cls) -> np.ndarray | None:
    try:
        col = obj.read()
    except Exception:
        return None
    mesh = _ptr_read(getattr(col, "m_Mesh", None) or getattr(col, "mesh", None) or getattr(col, "sharedMesh", None))
    if mesh is None:
        return None
    try:
        handler = mesh_handler_cls(mesh)
        handler.process()
        verts = handler.m_Vertices
    except Exception:
        return None
    if not verts:
        return None
    idx = getattr(mesh, "m_IndexBuffer", None) or getattr(mesh, "m_Triangles", None)
    try:
        tris_i = np.array(list(idx), dtype=np.int32).reshape((-1, 3))
        pts = np.array(verts, dtype=np.float32)
        local = pts[tris_i]
    except Exception:
        return None
    tr = _gameobject_transform(col)
    if tr is not None:
        local = _transform_points(local, _world_matrix(tr))
    return local


def _read_box_collider(obj) -> np.ndarray | None:
    try:
        col = obj.read()
    except Exception:
        return None
    center = getattr(col, "m_Center", None) or getattr(col, "center", None)
    size = getattr(col, "m_Size", None) or getattr(col, "size", None)
    try:
        cx, cy, cz = float(center.x), float(center.y), float(center.z)
        sx, sy, sz = float(size.x), float(size.y), float(size.z)
    except Exception:
        return None
    local = box_triangles((cx - sx / 2, cy - sy / 2, cz - sz / 2), (cx + sx / 2, cy + sy / 2, cz + sz / 2))
    tr = _gameobject_transform(col)
    if tr is not None:
        local = _transform_points(local, _world_matrix(tr))
    return local
