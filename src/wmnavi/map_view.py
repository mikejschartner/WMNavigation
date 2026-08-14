"""Interactive map view with player, layers, loot hunt markers, and floor filter."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QTransform, QWheelEvent

from .coords import PlayerState, crs_bounds_from_map, game_to_map
from .floors import FloorOption, marker_on_floor
from .loot_filter import best_item_at_spot, filter_spots, spot_is_super_rare
from .locks import door_locks, lock_key_name
from .marker_icons import (
    extract_label_color,
    get_container_icon,
    get_extract_icon,
    get_item_hunt_marker,
    get_location_pin,
    get_loose_loot_icon,
    get_quest_marker,
    get_usable_icon,
    load_friend_marker_pixmap,
    load_player_marker_pixmap,
)
from .models import ItemInfo, LootSpot, MapLayerData, MapPoint
from .paths import cache_dir
from .svg_layers import apply_svg_floor, map_has_floor_art
from .tile_map import stitch_map_tiles


@dataclass
class LayerVisibility:
    loose_loot: bool = False
    container_ids: set[str] | None = None
    extract_pmc: bool = False
    extract_scav: bool = False
    extract_coop: bool = False
    transits: bool = False
    locks: bool = False
    switches: bool = False
    stationary_weapons: bool = False
    item_hunt: bool = False
    show_locked_doors: bool = False


class PlayerMarker(QGraphicsPixmapItem):
    """Precision marker — green dot = position, green arrow = look direction."""

    def __init__(self, size_px: float = 28):
        pix = load_player_marker_pixmap(size_px)
        super().__init__(pix)
        # Center of pixmap (black dot) sits on true map coordinates.
        self.setOffset(-pix.width() / 2, -pix.height() / 2)
        self.setZValue(2000)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        # Keep a steady on-screen size while the map zooms.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self._size_px = size_px
        self._state: tuple[float, float, float, int] | None = None

    def set_state(self, x: float, y: float, yaw_deg: float, map_rotation: int):
        self._state = (x, y, yaw_deg, map_rotation)
        self.setPos(x, y)
        # Drawn arrow points up (north). Qt rotates clockwise; map_rotation matches CRS.
        self.setRotation(yaw_deg + map_rotation)


class MapMarkerItem(QGraphicsPixmapItem):
    def __init__(
        self,
        pix,
        point: MapPoint | LootSpot,
        marker_kind: str,
        item: ItemInfo | None = None,
    ):
        super().__init__(pix)
        self.point = point
        self.marker_kind = marker_kind
        self.item = item
        self.setOffset(-pix.width() / 2, -pix.height() / 2)
        self.setZValue(700 if marker_kind == "item_hunt" else 500)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        # Keep a steady on-screen size while the map zooms under the marker.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)


class ScreenAnchor(QGraphicsItemGroup):
    """Map-anchored group whose children are drawn in screen pixels."""

    def __init__(self):
        super().__init__()
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setHandlesChildEvents(False)


class MapView(QGraphicsView):
    player_updated = Signal(object)
    marker_clicked = Signal(str, object, object)

    MIN_ZOOM = 0.2
    MAX_ZOOM = 400.0

    def __init__(self):
        super().__init__()
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#0a0a0f")))
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.map_item: QGraphicsSvgItem | None = None
        self._tile_item: QGraphicsPixmapItem | None = None
        self._floor_tile_item: QGraphicsPixmapItem | None = None
        self._svg_source: str | None = None
        self._map_meta: dict | None = None
        self._map_slug = "map"
        self._applied_floor_key = ""
        self._map_unit = 6.0
        self.map_rotation = 0
        self.map_transform: list[float] | None = None
        self.map_bounds = None
        self.crs_bounds: tuple[float, float, float, float] | None = None
        self.bounds = None
        self._layer_data: MapLayerData | None = None
        self._visibility = LayerVisibility()
        self._floor = FloorOption("All Floors", -10000, 10000)
        self._selected_item_ids: set[str] = set()
        self._map_items: dict[str, ItemInfo] = {}
        # None = price filter off (show all loose loot); else only spots with these item ids.
        self._price_filter_ids: set[str] | None = None
        self._hide_loose_stars = False
        self._quest_spots: list[MapPoint] = []
        self._friend_pings: list = []
        self._haze_off_floor = True
        self._hide_locked_room_loot = False
        self._locked_loot_ids: set[str] = set()
        self._marker_items: list[QGraphicsItem] = []
        self._friend_items: list[QGraphicsItem] = []
        self._route_items: list[QGraphicsItem] = []
        self._marker_scale = 0.85
        self._refreshing = False
        # Small screen-space marker so the black dot stays precise while zooming.
        self.player = PlayerMarker(self._px(28))
        self.scene.addItem(self.player)
        self.player.hide()

    def set_marker_scale(self, scale: float):
        """Slider scale for on-screen marker pixels (independent of map zoom)."""
        self._marker_scale = max(0.4, min(2.5, scale))
        self._update_map_unit()
        self.refresh_layers()

    def _px(self, base: float) -> int:
        return max(4, int(round(base * self._marker_scale)))

    def _update_map_unit(self):
        if self.crs_bounds:
            min_x, max_x, _min_y, _max_y = self.crs_bounds
            width = max(max_x - min_x, 1.0)
            self._map_unit = width / 45.0 * self._marker_scale
        else:
            self._map_unit = 6.0 * self._marker_scale
        old = self.player
        was_visible = old.isVisible()
        old_state = getattr(old, "_state", None)
        self.player = PlayerMarker(self._px(28))
        self.scene.addItem(self.player)
        if old.scene():
            self.scene.removeItem(old)
        if old_state:
            self.player.set_state(*old_state)
            if was_visible:
                self.player.show()
            else:
                self.player.hide()
        else:
            self.player.hide()

    def set_floor(self, floor: FloorOption):
        self._floor = floor
        self._apply_floor_visual()
        self.refresh_layers()

    def load_svg(
        self,
        svg_path: str | None,
        map_rotation: int,
        bounds,
        transform: list[float] | None = None,
        map_meta: dict | None = None,
        map_slug: str = "map",
    ):
        self.map_rotation = map_rotation
        self.map_transform = transform
        self.map_bounds = bounds
        self.bounds = bounds
        self._map_meta = map_meta
        self._map_slug = map_slug
        self._svg_source = svg_path
        self._applied_floor_key = ""
        # Never keep the previous map's floor (would overlay Streets tiles on Lighthouse).
        self._floor = FloorOption(
            "All Floors",
            -10000.0,
            10000.0,
            svg_layer=str((map_meta or {}).get("svgLayer") or ""),
            tile_path=str((map_meta or {}).get("tilePath") or ""),
            kind="all",
        )
        if self._floor_tile_item:
            self.scene.removeItem(self._floor_tile_item)
            self._floor_tile_item = None
        if self._tile_item:
            self.scene.removeItem(self._tile_item)
            self._tile_item = None
        if self.map_item:
            self.scene.removeItem(self.map_item)
            self.map_item = None

        min_x = max_x = min_y = max_y = 0.0
        if transform and bounds:
            min_x, max_x, min_y, max_y = crs_bounds_from_map(bounds, map_rotation, transform)
            self.crs_bounds = (min_x, max_x, min_y, max_y)
            self.scene.setSceneRect(min_x, min_y, max_x - min_x, max_y - min_y)
        else:
            self.crs_bounds = None

        # Prefer SVG schematic (sharp + matches Questie). Tiles only when SVG is absent.
        used_svg = False
        if svg_path:
            self.map_item = QGraphicsSvgItem(svg_path)
            self.map_item.setZValue(0)
            self.map_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
            self.scene.addItem(self.map_item)
            svg_rect = self.map_item.boundingRect()
            if transform and bounds:
                sx = (max_x - min_x) / svg_rect.width() if svg_rect.width() else 1.0
                sy = (max_y - min_y) / svg_rect.height() if svg_rect.height() else 1.0
                self.map_item.setTransform(QTransform.fromScale(sx, sy))
                self.map_item.setPos(min_x, min_y)
            else:
                self.crs_bounds = None
                self.scene.setSceneRect(svg_rect)
            used_svg = True

        if not used_svg and map_meta and self.crs_bounds and map_meta.get("tilePath"):
            tile_result = stitch_map_tiles(
                map_meta["tilePath"],
                self.crs_bounds,
                min_zoom=int(map_meta.get("minZoom") or 2),
                max_zoom=int(map_meta.get("maxZoom") or 6),
                tile_size=int(map_meta.get("tileSize") or 256),
                map_slug=map_slug,
            )
            if tile_result:
                pix, (px, py, pw, ph) = tile_result
                self._tile_item = QGraphicsPixmapItem(pix)
                self._tile_item.setZValue(0)
                self._tile_item.setPos(px, py)
                sx = pw / pix.width() if pix.width() else 1.0
                sy = ph / pix.height() if pix.height() else 1.0
                self._tile_item.setTransform(QTransform.fromScale(sx, sy))
                self.scene.addItem(self._tile_item)

        self._update_map_unit()
        self.resetTransform()
        self.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._apply_floor_visual()
        self.refresh_layers()

    def _floor_cache_key(self) -> str:
        floor = self._floor
        return f"{self._map_slug}|{floor.label}|{floor.svg_layer}|{floor.kind}"

    def _replace_svg_item(self, path: str):
        """Swap the SVG graphic without resetting pan/zoom."""
        old = self.map_item
        item = QGraphicsSvgItem(path)
        item.setZValue(0)
        item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        if old is not None:
            item.setTransform(old.transform())
            item.setPos(old.pos())
            if old.scene():
                self.scene.removeItem(old)
        elif self.crs_bounds:
            min_x, max_x, min_y, max_y = self.crs_bounds
            svg_rect = item.boundingRect()
            sx = (max_x - min_x) / svg_rect.width() if svg_rect.width() else 1.0
            sy = (max_y - min_y) / svg_rect.height() if svg_rect.height() else 1.0
            item.setTransform(QTransform.fromScale(sx, sy))
            item.setPos(min_x, min_y)
        self.scene.addItem(item)
        self.map_item = item

    def _allowed_tile_paths(self) -> set[str]:
        meta = self._map_meta or {}
        allowed = {str(meta.get("tilePath") or "").strip()}
        for layer in meta.get("layers") or []:
            path = str(layer.get("tilePath") or "").strip()
            if path:
                allowed.add(path)
        allowed.discard("")
        return allowed

    def _set_floor_tiles(self, tile_path: str | None):
        if self._floor_tile_item:
            self.scene.removeItem(self._floor_tile_item)
            self._floor_tile_item = None
        if not tile_path or not self._map_meta or not self.crs_bounds:
            return
        if tile_path not in self._allowed_tile_paths():
            return
        main = str(self._map_meta.get("tilePath") or "")
        if tile_path == main:
            return
        tile_result = stitch_map_tiles(
            tile_path,
            self.crs_bounds,
            min_zoom=int(self._map_meta.get("minZoom") or 2),
            max_zoom=int(self._map_meta.get("maxZoom") or 6),
            tile_size=int(self._map_meta.get("tileSize") or 256),
            map_slug=f"{self._map_slug}_{self._floor.kind}_{self._floor.svg_layer or 'floor'}",
        )
        if not tile_result:
            return
        pix, (px, py, pw, ph) = tile_result
        item = QGraphicsPixmapItem(pix)
        item.setZValue(1)
        item.setPos(px, py)
        sx = pw / pix.width() if pix.width() else 1.0
        sy = ph / pix.height() if pix.height() else 1.0
        item.setTransform(QTransform.fromScale(sx, sy))
        item.setOpacity(0.92)
        self.scene.addItem(item)
        self._floor_tile_item = item

    def _apply_floor_visual(self):
        """Toggle SVG floor groups (and optional tile overlays) for the active floor."""
        key = self._floor_cache_key()
        if key == self._applied_floor_key and self.map_item is not None:
            return
        self._applied_floor_key = key
        floor = self._floor
        source = self._svg_source
        has_art = map_has_floor_art(self._map_meta)

        if source and has_art:
            dest = (
                cache_dir()
                / "svg_floors"
                / f"{self._map_slug}_{floor.kind}_{floor.svg_layer or 'ground'}.svg"
            )
            ok = apply_svg_floor(
                Path(source),
                dest,
                map_meta=self._map_meta,
                active_layer=floor.svg_layer,
                kind=floor.kind,
            )
            if ok and dest.exists():
                self._replace_svg_item(str(dest))
            elif self.map_item is None:
                self._replace_svg_item(source)
        elif source and self.map_item is None:
            self._replace_svg_item(source)

        if has_art:
            self._set_floor_tiles(floor.tile_path or None)
        elif self._floor_tile_item:
            self.scene.removeItem(self._floor_tile_item)
            self._floor_tile_item = None

    def _in_map_bounds(self, mx: float, my: float) -> bool:
        if not self.crs_bounds:
            return True
        min_x, max_x, min_y, max_y = self.crs_bounds
        pad_x = (max_x - min_x) * 0.08
        pad_y = (max_y - min_y) * 0.08
        return (min_x - pad_x) <= mx <= (max_x + pad_x) and (min_y - pad_y) <= my <= (max_y + pad_y)

    def wheelEvent(self, event: QWheelEvent):
        if event.angleDelta().y() == 0:
            return
        factor = 1.12 if event.angleDelta().y() > 0 else 1 / 1.12
        current = self.transform().m11()
        if (factor > 1 and current * factor > self.MAX_ZOOM) or (
            factor < 1 and current * factor < self.MIN_ZOOM
        ):
            return
        self.scale(factor, factor)
        event.accept()

    def set_player(self, state: PlayerState | None):
        if not state:
            self.player.hide()
            return
        mx, my = game_to_map(state.x, state.z, self.map_rotation, self.map_transform)
        self.player.set_state(mx, my, state.yaw_deg, self.map_rotation)
        self.player.show()
        self.player_updated.emit(state)

    def set_layer_data(self, data: MapLayerData):
        self._layer_data = data
        self._map_items = data.map_items
        self.refresh_layers()

    def set_visibility(self, visibility: LayerVisibility, *, refresh: bool = True):
        self._visibility = visibility
        if refresh:
            self.refresh_layers()

    def set_price_filter(self, allowed_ids: set[str] | None, *, refresh: bool = True):
        """When allowed_ids is a set, loose loot only shows spots with those items."""
        self._price_filter_ids = None if allowed_ids is None else set(allowed_ids)
        if refresh:
            self.refresh_layers()

    def set_item_hunt(
        self,
        selected_ids: set[str],
        enabled: bool,
        *,
        refresh: bool = True,
    ):
        self._selected_item_ids = set(selected_ids)
        self._visibility.item_hunt = enabled
        if refresh:
            self.refresh_layers()

    def set_friends(self, pings: list, map_slug: str):
        """Show friend pings that are on the current map."""
        self._friend_pings = [
            p for p in (pings or []) if getattr(p, "map_slug", "") == map_slug
        ]
        self._redraw_friends()

    def apply_layer_state(
        self,
        visibility: LayerVisibility,
        selected_ids: set[str],
        price_filter_ids: set[str] | None,
        hide_loose_stars: bool = False,
        quest_spots: list[MapPoint] | None = None,
        haze_off_floor: bool = True,
        hide_locked_room_loot: bool = False,
        locked_loot_ids: set[str] | None = None,
        show_locked_doors: bool = False,
    ):
        """Update visibility + hunt + price filter and redraw once."""
        self._visibility = visibility
        self._selected_item_ids = set(selected_ids)
        self._visibility.item_hunt = visibility.item_hunt
        self._visibility.show_locked_doors = bool(show_locked_doors or visibility.show_locked_doors)
        self._price_filter_ids = None if price_filter_ids is None else set(price_filter_ids)
        self._hide_loose_stars = hide_loose_stars
        self._quest_spots = list(quest_spots or [])
        self._haze_off_floor = haze_off_floor
        self._hide_locked_room_loot = hide_locked_room_loot
        self._locked_loot_ids = set(locked_loot_ids or [])
        self.refresh_layers()

    def _clear_friends(self):
        for item in self._friend_items:
            if item.scene():
                self.scene.removeItem(item)
        self._friend_items.clear()

    def _redraw_friends(self):
        self._clear_friends()
        for ping in self._friend_pings:
            mx, my = game_to_map(ping.x, ping.z, self.map_rotation, self.map_transform)
            if not self._in_map_bounds(mx, my):
                continue
            on_floor = self._on_active_floor(ping.y)
            opacity = 1.0 if on_floor else 0.35
            color = getattr(ping, "color", None) or "#38bdf8"
            pix = load_friend_marker_pixmap(self._px(28), color=color)
            group = ScreenAnchor()
            marker = QGraphicsPixmapItem(pix)
            marker.setOffset(-pix.width() / 2, -pix.height() / 2)
            marker.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            marker.setRotation(float(getattr(ping, "yaw_deg", 0)) + self.map_rotation)
            marker.setToolTip(
                f"{ping.name}\nFacing {float(getattr(ping, 'yaw_deg', 0)):.0f}°"
            )
            group.addToGroup(marker)

            text = QGraphicsSimpleTextItem(str(ping.name))
            text.setBrush(QBrush(QColor(color)))
            text.setPen(QPen(QColor("#0a0a0f"), 1))
            font = QFont("Segoe UI", max(8, self._px(9)))
            font.setBold(True)
            text.setFont(font)
            br = text.boundingRect()
            text.setPos(-br.width() / 2, pix.height() / 2 + 2)
            group.addToGroup(text)

            group.setPos(mx, my)
            group.setZValue(1900)
            group.setOpacity(opacity)
            self.scene.addItem(group)
            self._friend_items.append(group)

    def _on_active_floor(self, y: float) -> bool:
        if self._floor.label.lower() == "all floors":
            return True
        return marker_on_floor(y, self._floor)

    def _scene_pos(self, point: MapPoint | LootSpot) -> tuple[float, float, float]:
        y = point.y
        mx, my = game_to_map(point.x, point.z, self.map_rotation, self.map_transform)
        return mx, my, y

    def _add_marker(
        self,
        pix,
        point,
        kind: str,
        item: ItemInfo | None = None,
        label: str = "",
        label_kind: str | None = None,
    ):
        mx, my, y = self._scene_pos(point)
        on_floor = self._on_active_floor(y)
        # Haze off-floor loot instead of hiding it (when a specific floor is active).
        if not on_floor and not self._haze_off_floor:
            return
        if not self._in_map_bounds(mx, my):
            return
        opacity = 1.0 if on_floor else 0.28
        tooltip = label or (point.label if isinstance(point, MapPoint) else kind)
        if label_kind:
            group = ScreenAnchor()
            marker = MapMarkerItem(pix, point, kind, item)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
            marker.setPos(0, 0)
            marker.setToolTip(tooltip)
            group.addToGroup(marker)

            text = QGraphicsSimpleTextItem(label)
            color = extract_label_color(label_kind or kind)
            text.setBrush(QBrush(color))
            text.setPen(QPen(QColor("#0a0a0f"), 1))
            font = QFont("Segoe UI", max(8, self._px(9)))
            font.setBold(True)
            text.setFont(font)
            text.setToolTip(tooltip)
            br = text.boundingRect()
            text.setPos(-br.width() / 2, -pix.height() / 2 - br.height() - 2)
            group.addToGroup(text)

            group.setPos(mx, my)
            group.setZValue(650)
            group.setOpacity(opacity)
            self.scene.addItem(group)
            self._marker_items.append(group)
            return

        if kind == "item_hunt" and item is not None:
            group = ScreenAnchor()
            pin = MapMarkerItem(get_location_pin("#c084fc", self._px(7)), point, kind, item)
            pin.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
            pin.setPos(0, 0)
            pin.setToolTip(tooltip)
            group.addToGroup(pin)

            marker = MapMarkerItem(pix, point, kind, item)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
            marker.setPos(0, -pix.height() / 2 - 5)
            marker.setToolTip(tooltip)
            group.addToGroup(marker)

            group.setPos(mx, my)
            group.setZValue(700)
            group.setOpacity(opacity)
            self.scene.addItem(group)
            self._marker_items.append(group)
            return

        if kind == "quest":
            group = ScreenAnchor()
            marker = MapMarkerItem(pix, point, kind, item)
            marker.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, False)
            marker.setPos(0, 0)
            marker.setToolTip(tooltip)
            group.addToGroup(marker)
            group.setPos(mx, my)
            group.setZValue(750)
            group.setOpacity(opacity)
            self.scene.addItem(group)
            self._marker_items.append(group)
            return

        marker = MapMarkerItem(pix, point, kind, item)
        marker.setPos(mx, my)
        marker.setToolTip(tooltip)
        marker.setOpacity(opacity)
        self.scene.addItem(marker)
        self._marker_items.append(marker)

    def clear_markers(self):
        for item in self._marker_items:
            self.scene.removeItem(item)
        self._marker_items.clear()

    def refresh_layers(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            self.clear_markers()
            data = self._layer_data
            if not data:
                return
            vis = self._visibility
            item_px = self._px(22)
            container_px = self._px(14)
            extract_px = self._px(7)
            usable_px = self._px(12)
            loose_px = self._px(10)

            # Loose loot stars AND item-hunt icons only when the Loose loot layer is on.
            if vis.loose_loot:
                locked = self._locked_loot_ids if self._hide_locked_room_loot else set()
                if not self._hide_loose_stars:
                    normal_icon = get_loose_loot_icon(loose_px, rare=False)
                    rare_icon = get_loose_loot_icon(loose_px, rare=True)
                    for spot in filter_spots(
                        data.loose_loot,
                        allowed_ids=self._price_filter_ids,
                        hide_locked=self._hide_locked_room_loot,
                        locked_ids=locked,
                    ):
                        rare = spot_is_super_rare(spot, self._map_items, self._price_filter_ids)
                        self._add_marker(rare_icon if rare else normal_icon, spot, "loose_loot")

                # Category toggles and/or selected item hunt — show by item icon.
                if vis.item_hunt and self._selected_item_ids:
                    hunt_pix_cache: dict[str, object] = {}
                    for spot in data.loose_loot:
                        if locked and spot.id in locked:
                            continue
                        best = best_item_at_spot(
                            spot,
                            self._selected_item_ids,
                            self._map_items,
                        )
                        if not best:
                            continue
                        if self._price_filter_ids is not None and best.id not in self._price_filter_ids:
                            continue
                        pix = hunt_pix_cache.get(best.id)
                        if pix is None:
                            pix = get_item_hunt_marker(
                                best.id,
                                best.short_name,
                                best.icon_url,
                                item_px,
                                item_name=best.name,
                            )
                            hunt_pix_cache[best.id] = pix
                        self._add_marker(pix, spot, "item_hunt", best)

            container_ids = vis.container_ids or set()
            for cid in container_ids:
                ctype = data.containers.get(cid)
                if not ctype:
                    continue
                icon = get_container_icon(cid, ctype.name, container_px)
                for spot in ctype.spots:
                    self._add_marker(icon, spot, "container")

            if vis.extract_pmc:
                icon = get_extract_icon("pmc", extract_px)
                for spot in data.extracts_pmc:
                    self._add_marker(icon, spot, "extract_pmc", label=spot.label, label_kind="extract_pmc")
            if vis.extract_scav:
                icon = get_extract_icon("scav", extract_px)
                for spot in data.extracts_scav:
                    self._add_marker(icon, spot, "extract_scav", label=spot.label, label_kind="extract_scav")
            if vis.extract_coop:
                icon = get_extract_icon("coop", extract_px)
                for spot in data.extracts_coop:
                    self._add_marker(icon, spot, "extract_coop", label=spot.label, label_kind="extract_coop")
            if vis.transits:
                icon = get_extract_icon("transit", extract_px)
                for spot in data.transits:
                    self._add_marker(icon, spot, "transit", label=spot.label, label_kind="transit")

            if vis.show_locked_doors:
                icon = get_usable_icon("lock", usable_px)
                doors = {id(p) for p in door_locks(data.locks)}
                for spot in door_locks(data.locks):
                    name = lock_key_name(spot)
                    self._add_marker(icon, spot, "lock", label=name, label_kind="lock")
                if vis.locks:
                    for spot in data.locks:
                        if id(spot) in doors:
                            continue
                        self._add_marker(icon, spot, "lock")
            elif vis.locks:
                icon = get_usable_icon("lock", usable_px)
                for spot in data.locks:
                    self._add_marker(icon, spot, "lock")
            if vis.switches:
                icon = get_usable_icon("switch", usable_px)
                for spot in data.switches:
                    self._add_marker(icon, spot, "switch")
            if vis.stationary_weapons:
                icon = get_usable_icon("weapon", usable_px)
                for spot in data.stationary_weapons:
                    self._add_marker(icon, spot, "stationary")

            # Active quest objective markers
            if self._quest_spots:
                quest_px = self._px(20)
                pix_plain = get_quest_marker(quest_px, requires_key=False)
                pix_key = get_quest_marker(quest_px, requires_key=True)
                for spot in self._quest_spots:
                    needs_key = bool((spot.meta or {}).get("requires_key"))
                    tip = spot.label
                    desc = (spot.meta or {}).get("description")
                    if desc:
                        tip = f"{spot.label}\n{desc}"
                    self._add_marker(
                        pix_key if needs_key else pix_plain,
                        spot,
                        "quest",
                        label=tip,
                    )
            self._redraw_friends()
        finally:
            self._refreshing = False

    def set_route(
        self,
        waypoints: list[tuple[float, float, float]] | None,
        color: str = "#a855f7",
        stops: list | None = None,
    ):
        """Draw a planned route in scene space. Independent of layer marker refresh."""
        self.clear_route()
        if not waypoints or len(waypoints) < 2:
            return
        path = QPainterPath()
        mapped: list[QPointF] = []
        for x, _y, z in waypoints:
            mx, my = game_to_map(x, z, self.map_rotation, self.map_transform)
            mapped.append(QPointF(mx, my))
        path.moveTo(mapped[0])
        for pt in mapped[1:]:
            path.lineTo(pt)
        item = QGraphicsPathItem(path)
        pen = QPen(QColor(color), 2.6)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        item.setPen(pen)
        item.setZValue(400)
        item.setOpacity(0.88)
        self.scene.addItem(item)
        self._route_items.append(item)
        for i, pt in enumerate(mapped):
            if i == 0 or i == len(mapped) - 1:
                continue
            if i % max(1, len(mapped) // 24) != 0:
                continue
            dot = QGraphicsEllipseItem(-2.2, -2.2, 4.4, 4.4)
            dot.setBrush(QBrush(QColor(color)))
            dot.setPen(QPen(QColor("#0a0a0f"), 0.6))
            dot.setPos(pt)
            dot.setZValue(401)
            self.scene.addItem(dot)
            self._route_items.append(dot)
        for stop in stops or []:
            kind = getattr(stop, "kind", "")
            if kind not in {"quest", "loot", "extract"}:
                continue
            mx, my = game_to_map(stop.x, stop.z, self.map_rotation, self.map_transform)
            ring = QGraphicsEllipseItem(-5, -5, 10, 10)
            ring.setBrush(QBrush(QColor(color).lighter(130)))
            ring.setPen(QPen(QColor(color), 1.4))
            ring.setPos(mx, my)
            ring.setZValue(402)
            self.scene.addItem(ring)
            self._route_items.append(ring)

    def clear_route(self):
        for item in self._route_items:
            if item.scene():
                self.scene.removeItem(item)
        self._route_items.clear()

    def _find_marker_item(self, item: QGraphicsItem | None) -> MapMarkerItem | None:
        while item:
            if isinstance(item, MapMarkerItem):
                return item
            item = item.parentItem()
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())
            item = self._find_marker_item(self.scene.itemAt(scene_pos, self.transform()))
            if item:
                self.marker_clicked.emit(item.marker_kind, item.point, item.item)
        super().mousePressEvent(event)

    def center_on_player(self):
        if self.player.isVisible():
            self.centerOn(self.player)

    def focus_around_player(self, fraction: float = 0.14, radius_m: float | None = None):
        """Center + zoom the overlay around the player.

        Prefer radius_m (game meters) so large maps still get a close overlay.
        Fraction is only used when the map has no transform scale.
        """
        if self.crs_bounds:
            min_x, max_x, min_y, max_y = self.crs_bounds
            span = max(max_x - min_x, max_y - min_y, 1.0)
        else:
            rect = self.scene.sceneRect()
            if rect.isEmpty():
                return
            span = max(rect.width(), rect.height(), 1.0)

        if self.player.isVisible():
            pos = self.player.pos()
        else:
            # No lock yet — zoom around map center so the slider still does something.
            if self.crs_bounds:
                min_x, max_x, min_y, max_y = self.crs_bounds
                pos = QPointF((min_x + max_x) / 2.0, (min_y + max_y) / 2.0)
            else:
                pos = self.scene.sceneRect().center()

        radius = None
        if radius_m is not None and self.map_transform and len(self.map_transform) >= 3:
            try:
                scale_x = abs(float(self.map_transform[0]))
                scale_z = abs(float(self.map_transform[2]))
                scale = max((scale_x + scale_z) / 2.0, 0.01)
                radius = max(4.0, float(radius_m) * scale)
            except (TypeError, ValueError):
                radius = None
        if radius is None:
            radius = span * max(0.003, min(0.55, float(fraction)))
        area = QRectF(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2)
        # resetTransform is required — fitInView alone often no-ops when already zoomed.
        self.resetTransform()
        self.fitInView(area, Qt.AspectRatioMode.KeepAspectRatio)
        self.viewport().update()
