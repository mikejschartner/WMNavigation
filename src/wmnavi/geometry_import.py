"""Offline collision import from the local Tarkov install. Never runs in raid."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np

from .applog import get_logger
from .map_geometry import CollisionWorld, HeightField, box_triangles, build_bvh, save_collision

log = get_logger("wmnavi.geometry")

# WMNav slug -> filename tokens in EscapeFromTarkov_Data.
MAP_TOKENS: dict[str, tuple[str, ...]] = {
    "customs": ("bigmap", "customs"),
    "factory": ("factory4", "factory"),
    "woods": ("woods",),
    "shoreline": ("shoreline",),
    "interchange": ("interchange",),
    "reserve": ("rezervbase", "rezerv", "reserve"),
    "lighthouse": ("lighthouse",),
    "streets-of-tarkov": ("tarkovstreets", "city_streets"),
    "the-lab": ("laboratory",),
    "ground-zero": ("sandbox",),
    "the-labyrinth": ("labyrinth",),
    "terminal": ("terminal",),
    "icebreaker": ("icebreaker",),
}

_SKIP_SUFFIX = {".dll", ".exe", ".txt", ".xml", ".json", ".manifest", ".resource"}


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
        # Launcher may hold files without the game window.
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


def candidate_files(data_dir: Path, slug: str) -> list[Path]:
    tokens = MAP_TOKENS.get(slug) or (slug.replace("-", ""),)
    hits: list[Path] = []
    if not data_dir.is_dir():
        return hits
    search_roots = [data_dir]
    streaming = data_dir / "StreamingAssets"
    if streaming.is_dir():
        search_roots.append(streaming)
    for root in search_roots:
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                lower = name.lower()
                suffix = Path(name).suffix.lower()
                if suffix in _SKIP_SUFFIX:
                    continue
                if not any(token in lower for token in tokens):
                    continue
                path = Path(dirpath) / name
                if path.stat().st_size < 64:
                    continue
                hits.append(path)
            # Do not walk the entire install forever.
            if len(hits) > 80:
                return hits
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
        return None, f"No game files matched {slug}. Check the Tarkov Data folder."
    try:
        import UnityPy
    except Exception:
        return None, "UnityPy is not installed. Install it to import map collision."

    heights: list[tuple[float, float, float, np.ndarray]] = []
    mesh_groups: list[np.ndarray] = []
    scanned = 0
    for path in files:
        scanned += 1
        if scanned % 8 == 1:
            status(f"Reading {path.name} ({scanned}/{len(files)})")
        try:
            env = UnityPy.load(str(path))
        except Exception:
            continue
        for obj in getattr(env, "objects", []) or []:
            try:
                kind = str(getattr(getattr(obj, "type", None), "name", "") or "")
            except Exception:
                continue
            if kind == "TerrainData":
                parsed = _read_terrain(obj)
                if parsed is not None:
                    heights.append(parsed)
            elif kind in {"MeshCollider", "BoxCollider"}:
                tris = _read_collider(obj, kind)
                if tris is not None and len(tris):
                    mesh_groups.append(tris)
            if len(mesh_groups) > 4000:
                break

    world = CollisionWorld(slug=slug, source="local tarkov colliders")
    if heights:
        origin_x, origin_z, cell, grid = heights[0]
        world.height = HeightField(origin_x, origin_z, cell, grid)
    if mesh_groups:
        tris = np.concatenate(mesh_groups, axis=0)
        if len(tris) > 250_000:
            # Keep precision; cap only runaway decorative colliders.
            tris = tris[:250_000]
        status(f"Building BVH ({len(tris)} triangles)")
        nodes, packed = build_bvh(np.asarray(tris, dtype=np.float32))
        world.tris = packed
        world.nodes = nodes
    if not world.ready():
        return None, f"No terrain or colliders found in files for {slug}."
    hashes = [_file_hash(p) for p in files[:12]]
    save_collision(
        world,
        source="local tarkov colliders",
        extra_meta={"files": [str(p.name) for p in files[:24]], "hashes": hashes, "scanned": scanned},
    )
    status(f"Saved collision for {slug}")
    return world, "ok"


def _read_terrain(obj) -> tuple[float, float, float, np.ndarray] | None:
    try:
        data = obj.read()
    except Exception:
        return None
    grid = None
    for attr in ("Heightmap", "m_Heightmap", "heightmap"):
        raw = getattr(data, attr, None)
        if raw is None:
            continue
        heights = getattr(raw, "heights", None) or getattr(raw, "m_Heights", None) or raw
        try:
            grid = np.array(heights, dtype=np.float32)
        except Exception:
            continue
        break
    if grid is None:
        return None
    if grid.ndim == 1:
        side = int(np.sqrt(grid.size))
        if side * side != grid.size:
            return None
        grid = grid.reshape((side, side))
    size = getattr(data, "size", None) or getattr(data, "m_Size", None)
    try:
        sx, sy, sz = float(size.x), float(size.y), float(size.z)
    except Exception:
        sx = sz = float(max(grid.shape) - 1)
        sy = 1.0
    rows, cols = grid.shape
    cell = sx / max(1, cols - 1)
    grid = grid * (sy if sy > 0.01 else 1.0)
    pos = getattr(data, "position", None)
    ox = float(getattr(pos, "x", 0.0) or 0.0)
    oz = float(getattr(pos, "z", 0.0) or 0.0)
    return ox, oz, cell, grid


def _read_collider(obj, kind: str) -> np.ndarray | None:
    try:
        data = obj.read()
    except Exception:
        return None
    if kind == "BoxCollider":
        center = getattr(data, "center", None) or getattr(data, "m_Center", None)
        size = getattr(data, "size", None) or getattr(data, "m_Size", None)
        try:
            cx, cy, cz = float(center.x), float(center.y), float(center.z)
            sx, sy, sz = float(size.x), float(size.y), float(size.z)
        except Exception:
            return None
        return box_triangles((cx - sx / 2, cy - sy / 2, cz - sz / 2), (cx + sx / 2, cy + sy / 2, cz + sz / 2))
    mesh = getattr(data, "mesh", None) or getattr(data, "m_Mesh", None) or getattr(data, "sharedMesh", None)
    if mesh is None:
        return None
    try:
        mesh = mesh.read() if hasattr(mesh, "read") else mesh
    except Exception:
        pass
    verts = getattr(mesh, "vertices", None) or getattr(mesh, "m_Vertices", None)
    idx = getattr(mesh, "triangles", None) or getattr(mesh, "m_Triangles", None) or getattr(mesh, "indices", None)
    if verts is None or idx is None:
        return None
    try:
        pts = np.array([[float(v.x), float(v.y), float(v.z)] if hasattr(v, "x") else list(v) for v in verts], dtype=np.float32)
        tri_i = np.array(list(idx), dtype=np.int32).reshape((-1, 3))
        return pts[tri_i]
    except Exception:
        return None
