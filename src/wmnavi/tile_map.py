"""Download and stitch tarkov.dev raster map tiles (fallback when SVG is missing)."""

from __future__ import annotations

import math

from PySide6.QtGui import QImage, QPainter, QPixmap

from .assets import cache_remote_file
from .paths import cache_dir


def _tile_url(tile_path: str, z: int, x: int, y: int) -> str:
    return tile_path.format(z=z, x=x, y=y)


def _tile_cache_name(tile_path: str, z: int, x: int, y: int) -> str:
    slug = tile_path.split("/maps/", 1)[-1].replace("/", "_").replace("{z}", str(z))
    return f"tile_{slug}_{x}_{y}.png"


def _fetch_tile(tile_path: str, z: int, x: int, y: int, min_bytes: int = 4000) -> QPixmap | None:
    url = _tile_url(tile_path, z, x, y)
    name = _tile_cache_name(tile_path, z, x, y)
    path = cache_dir() / "tiles" / name
    if not path.exists() or path.stat().st_size < min_bytes:
        try:
            path = cache_remote_file(url, f"tiles/{name}")
        except Exception:
            return None
    if path.stat().st_size < min_bytes:
        return None
    pix = QPixmap(str(path))
    return pix if not pix.isNull() else None


def tile_range_for_bounds(
    crs_bounds: tuple[float, float, float, float],
    z: int,
    tile_size: int = 256,
    pad: int = 1,
) -> tuple[int, int, int, int]:
    min_x, max_x, min_y, max_y = crs_bounds
    zoom_scale = 2**z
    x0 = max(0, int(math.floor(min_x * zoom_scale / tile_size)) - pad)
    x1 = max(x0, int(math.ceil(max_x * zoom_scale / tile_size)) + pad)
    y0 = max(0, int(math.floor(min_y * zoom_scale / tile_size)) - pad)
    y1 = max(y0, int(math.ceil(max_y * zoom_scale / tile_size)) + pad)
    return x0, x1, y0, y1


def stitch_map_tiles(
    tile_path: str,
    crs_bounds: tuple[float, float, float, float],
    *,
    min_zoom: int = 2,
    max_zoom: int = 6,
    tile_size: int = 256,
    map_slug: str = "map",
    max_tiles: int = 220,
) -> tuple[QPixmap, tuple[float, float, float, float]] | None:
    """Return pixmap + scene rect (x, y, w, h) in CRS units covering crs_bounds."""
    for z in range(max_zoom, min_zoom - 1, -1):
        x0, x1, y0, y1 = tile_range_for_bounds(crs_bounds, z, tile_size)
        tile_count = (x1 - x0 + 1) * (y1 - y0 + 1)
        if tile_count > max_tiles:
            continue

        stitched_cache = cache_dir() / f"{map_slug}_z{z}_{x0}-{x1}_{y0}-{y1}.png"
        if stitched_cache.exists() and stitched_cache.stat().st_size > 10_000:
            pix = QPixmap(str(stitched_cache))
            if not pix.isNull():
                min_x, max_x, min_y, max_y = crs_bounds
                return pix, (min_x, min_y, max_x - min_x, max_y - min_y)

        width = (x1 - x0 + 1) * tile_size
        height = (y1 - y0 + 1) * tile_size
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(0x0A0A0F)

        found = 0
        painter = QPainter(image)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                tile = _fetch_tile(tile_path, z, tx, ty)
                if tile is None:
                    continue
                found += 1
                painter.drawPixmap((tx - x0) * tile_size, (ty - y0) * tile_size, tile)
        painter.end()

        if found < 4:
            continue

        pix = QPixmap.fromImage(image)
        stitched_cache.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(stitched_cache))
        min_x, max_x, min_y, max_y = crs_bounds
        return pix, (min_x, min_y, max_x - min_x, max_y - min_y)

    return None
