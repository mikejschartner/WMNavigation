"""Draw or fetch marker icons for map layers."""

from __future__ import annotations

import hashlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QPen

_ICON_CACHE: dict[str, QPixmap] = {}

CONTAINER_COLORS = [
    "#3b82f6",
    "#22c55e",
    "#eab308",
    "#f97316",
    "#ec4899",
    "#14b8a6",
    "#a855f7",
    "#ef4444",
    "#6366f1",
    "#84cc16",
]

EXTRACT_COLORS = {
    "pmc": "#22c55e",
    "scav": "#f59e0b",
    "coop": "#38bdf8",
    "transit": "#c084fc",
}


def _cache_key(kind: str, key: str, size: int) -> str:
    return f"{kind}:{key}:{size}"


def _draw_pixmap(size: int, draw_fn) -> QPixmap:
    size = max(4, int(size))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_fn(painter, size)
    painter.end()
    return pix


def _color_for_key(key: str) -> QColor:
    digest = hashlib.md5(key.encode()).hexdigest()
    idx = int(digest[:8], 16) % len(CONTAINER_COLORS)
    return QColor(CONTAINER_COLORS[idx])


def get_container_icon(container_id: str, name: str, size: float = 5) -> QPixmap:
    size_i = max(4, int(round(size)))
    cache = _cache_key("container", container_id, size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    color = _color_for_key(container_id)
    label = "".join(ch for ch in (name or "?") if ch.isalnum())[:2].upper() or "?"

    def draw(painter: QPainter, sz: int):
        margin = max(1, sz // 8)
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#0a0a0f"), max(1, sz // 12)))
        painter.drawRoundedRect(margin, margin, sz - margin * 2, sz - margin * 2, max(2, sz // 5), max(2, sz // 5))
        painter.setPen(QColor("#ffffff"))
        font = QFont("Segoe UI", max(5, sz // 3))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, 0, sz, sz, Qt.AlignmentFlag.AlignCenter, label)

    pix = _draw_pixmap(size_i, draw)
    _ICON_CACHE[cache] = pix
    return pix


def get_loose_loot_icon(size: float = 4, rare: bool = False) -> QPixmap:
    size_i = max(3, int(round(size)))
    cache = _cache_key("loose", "rare" if rare else "star", size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    fill = QColor("#ef4444") if rare else QColor("#fb923c")
    edge = QColor("#fca5a5") if rare else QColor("#fdba74")

    def draw(painter: QPainter, sz: int):
        cx = cy = sz / 2
        r = sz * 0.38
        painter.setBrush(fill)
        painter.setPen(QPen(edge, max(1, sz // 10)))
        from math import cos, pi, sin

        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF

        points = []
        for i in range(10):
            angle = pi / 2 + i * pi / 5
            radius = r if i % 2 == 0 else r * 0.45
            points.append((cx + radius * cos(angle), cy - radius * sin(angle)))
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))

    pix = _draw_pixmap(size_i, draw)
    _ICON_CACHE[cache] = pix
    return pix


def get_extract_icon(kind: str, size: float = 6) -> QPixmap:
    """Tiny solid square pin marking the exact extract position."""
    size_i = max(3, int(round(size)))
    cache = _cache_key("extract_pin", kind, size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    color = QColor(EXTRACT_COLORS.get(kind, "#22c55e"))

    def draw(painter: QPainter, sz: int):
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#0a0a0f"), max(1, sz // 8)))
        painter.drawRect(0, 0, sz - 1, sz - 1)

    pix = _draw_pixmap(size_i, draw)
    _ICON_CACHE[cache] = pix
    return pix


def extract_label_color(kind: str) -> QColor:
    key = kind.replace("extract_", "")
    return QColor(EXTRACT_COLORS.get(key, "#f3e8ff"))


def get_item_hunt_marker(
    item_id: str,
    short_name: str,
    icon_url: str,
    size: float,
    item_name: str = "",
) -> QPixmap:
    """High-quality loot/key icon at fixed screen size (caller uses IgnoresTransform)."""
    from .icon_cache import get_item_icon
    from .wiki_icons import best_icon_url_cached

    size_i = max(12, int(round(size)))
    cache = _cache_key("hunt_hq", f"{item_id}:{size_i}", size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    # Cache-only wiki + tarkov.dev — never hit the network wiki API during redraws.
    url = best_icon_url_cached(item_id, item_name or short_name, short_name, icon_url)
    source_size = max(64, size_i * 4)
    icon = get_item_icon(url, source_size, allow_download=True)
    has_icon = not icon.isNull() and icon.width() > 4

    def draw(painter: QPainter, sz: int):
        # Soft pad + thin border; keep art readable at small sizes.
        painter.setBrush(QColor(12, 12, 18, 220))
        painter.setPen(QPen(QColor("#a855f7"), max(1, sz // 14)))
        painter.drawRoundedRect(0, 0, sz - 1, sz - 1, max(2, sz // 7), max(2, sz // 7))
        if has_icon:
            pad = max(1, sz // 9)
            scaled = icon.scaled(
                sz - pad * 2,
                sz - pad * 2,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            ox = (sz - scaled.width()) // 2
            oy = (sz - scaled.height()) // 2
            painter.drawPixmap(ox, oy, scaled)
        else:
            label = "".join(ch for ch in (short_name or "?") if ch.isalnum())[:3].upper() or "?"
            painter.setPen(QColor("#f3e8ff"))
            font = QFont("Segoe UI", max(6, sz // 3))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(0, 0, sz, sz, Qt.AlignmentFlag.AlignCenter, label)

    pix = _draw_pixmap(size_i, draw)
    _ICON_CACHE[cache] = pix
    return pix


def get_quest_marker(size: float = 18, requires_key: bool = False) -> QPixmap:
    """Gold quest pin; optional K badge for key-required quests."""
    size_i = max(12, int(round(size)))
    cache = _cache_key("quest", f"k{int(requires_key)}", size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    def draw(painter: QPainter, sz: int):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Shield / diamond body
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPolygonF

        cx = sz / 2
        body = QPolygonF(
            [
                QPointF(cx, 1),
                QPointF(sz - 2, sz * 0.38),
                QPointF(cx, sz - 2),
                QPointF(2, sz * 0.38),
            ]
        )
        painter.setBrush(QColor("#eab308"))
        painter.setPen(QPen(QColor("#0a0a0f"), max(1, sz // 12)))
        painter.drawPolygon(body)
        painter.setPen(QColor("#0a0a0f"))
        font = QFont("Segoe UI", max(6, sz // 3))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, 0, sz, sz, Qt.AlignmentFlag.AlignCenter, "Q")
        if requires_key:
            badge = max(8, sz // 2)
            painter.setBrush(QColor("#111827"))
            painter.setPen(QPen(QColor("#fbbf24"), max(1, sz // 16)))
            painter.drawEllipse(sz - badge - 1, 1, badge, badge)
            painter.setPen(QColor("#fbbf24"))
            kf = QFont("Segoe UI", max(5, badge // 2))
            kf.setBold(True)
            painter.setFont(kf)
            painter.drawText(sz - badge - 1, 1, badge, badge, Qt.AlignmentFlag.AlignCenter, "K")

    pix = _draw_pixmap(size_i, draw)
    _ICON_CACHE[cache] = pix
    return pix


def get_location_pin(color: str = "#c084fc", size: float = 8) -> QPixmap:
    """Tiny exact-position pin drawn under item/extract markers."""
    size_i = max(4, int(round(size)))
    cache = _cache_key("pin", color, size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    def draw(painter: QPainter, sz: int):
        painter.setBrush(QColor(color))
        painter.setPen(QPen(QColor("#0a0a0f"), max(1, sz // 8)))
        painter.drawEllipse(1, 1, sz - 2, sz - 2)

    pix = _draw_pixmap(size_i, draw)
    _ICON_CACHE[cache] = pix
    return pix


def load_player_marker_pixmap(size: float = 52) -> QPixmap:
    """Load the custom player arrow (circle = feet, arrow = facing)."""
    from .paths import app_root

    size_i = max(24, int(round(size)))
    cache = _cache_key("player", "arrow_thick", size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    path = app_root() / "assets" / "player_marker.png"
    source = QPixmap(str(path)) if path.exists() else QPixmap()
    if source.isNull():
        # Fallback drawn marker if asset is missing.
        def draw(painter: QPainter, sz: int):
            cx = cy = sz / 2
            painter.setPen(QPen(QColor("#ffffff"), max(3, sz // 14)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(int(sz * 0.14), int(sz * 0.14), int(sz * 0.72), int(sz * 0.72))
            painter.setPen(QPen(QColor("#b91c1c"), max(4, sz // 10), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(int(cx), int(cy), int(cx), int(sz * 0.12))
            painter.setBrush(QColor("#b91c1c"))
            painter.setPen(Qt.PenStyle.NoPen)
            from PySide6.QtCore import QPointF
            from PySide6.QtGui import QPolygonF

            tip = QPolygonF(
                [
                    QPointF(cx, sz * 0.05),
                    QPointF(cx - sz * 0.16, sz * 0.28),
                    QPointF(cx + sz * 0.16, sz * 0.28),
                ]
            )
            painter.drawPolygon(tip)

        pix = _draw_pixmap(size_i, draw)
    else:
        pix = source.scaled(
            size_i,
            size_i,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    _ICON_CACHE[cache] = pix
    return pix


def get_usable_icon(kind: str, size: float = 4) -> QPixmap:
    size_i = max(3, int(round(size)))
    cache = _cache_key("usable", kind, size_i)
    if cache in _ICON_CACHE:
        return _ICON_CACHE[cache]

    colors = {"lock": "#f87171", "switch": "#fbbf24", "weapon": "#94a3b8"}
    color = QColor(colors.get(kind, "#94a3b8"))
    glyph = {"lock": "L", "switch": "S", "weapon": "G"}.get(kind, "?")

    def draw(painter: QPainter, sz: int):
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#0a0a0f"), 1))
        painter.drawRoundedRect(1, 1, sz - 2, sz - 2, 2, 2)
        painter.setPen(QColor("#0a0a0f"))
        font = QFont("Segoe UI", max(5, sz // 3))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, 0, sz, sz, Qt.AlignmentFlag.AlignCenter, glyph)

    pix = _draw_pixmap(size_i, draw)
    _ICON_CACHE[cache] = pix
    return pix
