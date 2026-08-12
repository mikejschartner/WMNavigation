"""Interactive map view with player, layers, loot hunt markers, and floor filter."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QTransform, QWheelEvent
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

from .coords import PlayerState, crs_bounds_from_map, game_to_map
from .floors import FloorOption, marker_on_floor
from .loot_filter import best_item_at_spot, spot_is_super_rare, spots_passing_price
from .marker_icons import (
    extract_label_color,
    get_container_icon,
    get_extract_icon,
    get_item_hunt_marker,
    get_location_pin,
    get_loose_loot_icon,
    get_quest_marker,
    get_usable_icon,
    load_player_marker_pixmap,
)
from .models import ItemInfo, LootSpot, MapLayerData, MapPoint
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


class PlayerMarker(QGraphicsPixmapItem):
    """Custom arrow marker — circle = position, arrow = look direction."""

    def __init__(self, size_px: float = 52):
        pix = load_player_marker_pixmap(size_px)
        super().__init__(pix)
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
        # Asset arrow points up (north). Qt rotates clockwise; map_rotation matches CRS.
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
    MAX_ZOOM = 120.0

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
        self._haze_off_floor = True
        self._marker_items: list[QGraphicsItem] = []
        self._marker_scale = 0.85
        self._refreshing = False
        self.player = PlayerMarker(self._px(52))
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
        self.player = PlayerMarker(self._px(52))
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
        self.refresh_layers()

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

    def apply_layer_state(
        self,
        visibility: LayerVisibility,
        selected_ids: set[str],
        price_filter_ids: set[str] | None,
        hide_loose_stars: bool = False,
        quest_spots: list[MapPoint] | None = None,
        haze_off_floor: bool = True,
    ):
        """Update visibility + hunt + price filter and redraw once."""
        self._visibility = visibility
        self._selected_item_ids = set(selected_ids)
        self._visibility.item_hunt = visibility.item_hunt
        self._price_filter_ids = None if price_filter_ids is None else set(price_filter_ids)
        self._hide_loose_stars = hide_loose_stars
        self._quest_spots = list(quest_spots or [])
        self._haze_off_floor = haze_off_floor
        self.refresh_layers()

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
                if not self._hide_loose_stars:
                    normal_icon = get_loose_loot_icon(loose_px, rare=False)
                    rare_icon = get_loose_loot_icon(loose_px, rare=True)
                    for spot in spots_passing_price(data.loose_loot, self._price_filter_ids):
                        rare = spot_is_super_rare(spot, self._map_items, self._price_filter_ids)
                        self._add_marker(rare_icon if rare else normal_icon, spot, "loose_loot")

                # Category toggles and/or selected item hunt — show by item icon.
                if vis.item_hunt and self._selected_item_ids:
                    hunt_pix_cache: dict[str, object] = {}
                    for spot in data.loose_loot:
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

            if vis.locks:
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
        finally:
            self._refreshing = False

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
