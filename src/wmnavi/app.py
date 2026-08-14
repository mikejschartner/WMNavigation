"""WMNavigation main window."""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import __version__
from .assets import cache_remote_file
from .compass import CompassHud
from .coords import PlayerState
from .extract_panel import ExtractAvailabilityPanel, unique_extracts
from .heading import HeadingTracker
from .locks import locked_loot_ids as compute_locked_loot_ids
from .nav_graph import NavGraph
from .raid_time import remaining_seconds
from .route_planner import (
    RouteResult,
    plan_loot_route,
    plan_quest_route,
    player_moved_enough,
    should_refresh_route,
)
from .data_loader import get_interactive_map, list_map_names
from .floors import FloorOption, build_floor_options, floor_for_y
from .friend_sync import FriendSync, new_player_id
from .hotkeys import GlobalHotkeys
from .item_categories import CATEGORY_META, CATEGORY_ORDER, ids_for_categories
from .item_filter_dialog import ItemFilterDialog
from .layer_sidebar import MapLayersSidebar
from .log_watcher import LogWatcher, describe_log_search, find_log_dir
from .loot_filter import items_at_spot, spots_for_selection, spots_passing_price, visible_map_items
from .loot_loader import GAME_MODES
from .map_data_loader import load_map_layers
from .map_view import LayerVisibility, MapView
from .minimap import MiniMapWindow
from .models import ItemInfo, LootSpot, MapLayerData, MapPoint
from .paths import app_root, cache_dir
from .quest_loader import QuestInfo, load_quests_for_map, load_quests_split
from .quest_log_sync import (
    QuestEvent,
    QuestLogState,
    QuestLogWatcher,
    import_quest_states_from_logs,
    load_cached_state,
    save_states,
)
from .quest_panel import QuestListPanel
from .screenshot import default_screenshot_dir, is_eft_screenshot_name, parse_screenshot
from .theme import STYLESHEET
from .win_input import press_v_in_raid


class ScreenshotHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def on_created(self, event):
        self._maybe(event)

    def on_modified(self, event):
        self._maybe(event)

    def on_moved(self, event):
        dest = getattr(event, "dest_path", None)
        if not dest:
            return
        path = Path(dest)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} and is_eft_screenshot_name(path.name):
            self.callback(path)

    def _maybe(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            return
        if not is_eft_screenshot_name(path.name):
            return
        self.callback(path)


class Bridge(QObject):
    map_changed = Signal(str)
    raid_ended = Signal()
    screenshot_parsed = Signal(object)
    quest_events = Signal(object)
    quests_imported = Signal(object)
    friend_update = Signal(object)
    friend_status = Signal(str)
    route_ready = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"WMNavigation v{__version__}")
        self.setMinimumSize(480, 320)
        icon_path = app_root() / "assets" / "icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.settings = QSettings("WMMods", "WMNavigation")
        self.bridge = Bridge()
        self.bridge.map_changed.connect(self.on_auto_map)
        self.bridge.raid_ended.connect(self.on_raid_end)
        self.bridge.screenshot_parsed.connect(self.on_player_update)
        self.bridge.quest_events.connect(self.on_quest_log_events)
        self.bridge.quests_imported.connect(self.on_quests_imported)
        self.bridge.friend_update.connect(self.on_friend_update)
        self.bridge.friend_status.connect(self.on_friend_status)
        self.bridge.route_ready.connect(self.on_route_ready)

        player_id = str(self.settings.value("friend_player_id", "") or "")
        if not player_id:
            player_id = new_player_id()
            self.settings.setValue("friend_player_id", player_id)
        self.friend_sync = FriendSync(
            player_id,
            on_update=lambda snaps: self.bridge.friend_update.emit(snaps),
            on_status=lambda text: self.bridge.friend_status.emit(str(text)),
        )
        self._friend_color = str(self.settings.value("friend_color", "#38bdf8") or "#38bdf8")

        self.current_map_slug = "customs"
        self.current_game_mode = self.settings.value("game_mode", "regular")
        self.layer_data = MapLayerData()
        self.map_items: dict[str, ItemInfo] = {}
        self.filtered_items: dict[str, ItemInfo] = {}
        self.selected_item_ids: set[str] = set()
        self.floor_options: list[FloorOption] = [FloorOption("All Floors", -10000, 10000)]
        self.map_quests: list[QuestInfo] = []
        self.anywhere_quests: list[QuestInfo] = []
        self.active_quest_ids: set[str] = set()
        self.player_active_quest_ids: set[str] | None = None  # None = manual catalog mode
        self._quest_log_states: dict[str, QuestLogState] = {}
        cached = load_cached_state(self.current_game_mode)
        if cached:
            self._quest_log_states[self.current_game_mode] = cached
            self.player_active_quest_ids = cached.active_ids()
        self.raid_started_at: datetime | None = None
        self.in_raid = False
        saved_shot = Path(self.settings.value("screenshot_dir", ""))
        if saved_shot and saved_shot.is_dir():
            self.screenshot_dir = saved_shot
        else:
            self.screenshot_dir = default_screenshot_dir()
            self.settings.setValue("screenshot_dir", str(self.screenshot_dir))
        self.auto_delete = self.settings.value("auto_delete_screenshots", True, type=bool)
        self.settings.setValue("auto_delete_screenshots", self.auto_delete)
        self._seen_screenshots: set[str] = set()
        self._price_filter_timer = QTimer(self)
        self._price_filter_timer.setSingleShot(True)
        self._price_filter_timer.timeout.connect(self.apply_item_filters)
        self._screenshot_poll = QTimer(self)
        self._screenshot_poll.setInterval(750)
        self._screenshot_poll.timeout.connect(self._poll_screenshots)
        self._live_timer = QTimer(self)
        self._live_timer.timeout.connect(self._on_live_tick)
        self._friend_prune_timer = QTimer(self)
        self._friend_prune_timer.setInterval(5000)
        self._friend_prune_timer.timeout.connect(self._refresh_friend_markers)
        self._last_player: PlayerState | None = None
        self._screenshot_loop_on = True
        self.heading = HeadingTracker()
        self.compass = None
        self.nav_graph = NavGraph()
        self._locked_loot_ids: set[str] = set()
        self._active_route: RouteResult | None = None
        self._route_kind: str | None = None
        self._route_origin: tuple[float, float, float] | None = None
        self._route_gen = 0
        self._route_refresh_timer = QTimer(self)
        self._route_refresh_timer.setSingleShot(True)
        self._route_refresh_timer.timeout.connect(self._maybe_refresh_route)

        self._build_ui()
        self.setStyleSheet(STYLESHEET)
        # Create minimap lazily on first F7 — avoids extra startup work/crashes.
        self.minimap = None
        self.hotkeys = GlobalHotkeys(self)
        self.hotkeys.pressed.connect(self.on_global_hotkey)
        try:
            self.load_map(self.current_map_slug)
        except Exception as exc:
            self.status_label.setText(f"Map load failed: {exc}")
        try:
            self.start_watchers()
        except Exception:
            pass
        self._friend_prune_timer.start()
        QTimer.singleShot(300, self._start_global_hotkeys)

    def _start_global_hotkeys(self):
        try:
            self.hotkeys.start()
        except Exception:
            pass

    def _ensure_minimap(self) -> bool:
        if self.minimap is not None:
            return True
        try:
            size = int(self.settings.value("minimap_size", 300) or 300)
            self.minimap = MiniMapWindow(size_px=size)
            self.minimap.map_view.set_marker_scale(self._minimap_marker_scale())
            return True
        except Exception as exc:
            self.status_label.setText(f"Mini map failed: {exc}")
            self.minimap = None
            return False

    def _layer_settings_prefix(self) -> str:
        return f"layers/{self.current_game_mode}/{self.current_map_slug}"

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(520)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(12, 10, 12, 10)
        side_layout.setSpacing(6)

        title = QLabel("WMNavigation")
        title.setObjectName("title")
        side_layout.addWidget(title)

        self.edition_label = QLabel(f"v{__version__}")
        self.edition_label.setObjectName("edition")
        side_layout.addWidget(self.edition_label)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("status")
        self.status_label.setWordWrap(True)
        side_layout.addWidget(self.status_label)

        side_layout.addWidget(QLabel("Game Mode"))
        self.mode_combo = QComboBox()
        for label in GAME_MODES:
            self.mode_combo.addItem(label)
        self.mode_combo.currentTextChanged.connect(self.on_mode_changed)
        side_layout.addWidget(self.mode_combo)

        side_layout.addWidget(QLabel("Map"))
        self.map_combo = QComboBox()
        self.map_slug_by_label = {}
        for label, slug in list_map_names():
            self.map_combo.addItem(label)
            self.map_slug_by_label[label] = slug
        self.map_combo.currentTextChanged.connect(self.on_map_combo)
        side_layout.addWidget(self.map_combo)

        self.layer_sidebar = MapLayersSidebar()
        self.layer_sidebar.changed.connect(self.on_layers_changed)
        self.layer_sidebar.setMinimumHeight(80)
        side_layout.addWidget(self.layer_sidebar, 3)

        # Compact bottom controls so the layers list keeps most of the height.
        bottom = QFrame()
        bottom.setObjectName("sidebarBottom")
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 4, 0, 0)
        bottom_layout.setSpacing(4)

        bottom_layout.addWidget(QLabel("Item Hunt Filters"))
        self.chk_price = QCheckBox("Min price filter")
        # Migrate old dual flea/trader toggles into one.
        legacy_on = self.settings.value("flea_enabled", False, type=bool) or self.settings.value(
            "trader_enabled", False, type=bool
        )
        self.chk_price.setChecked(self.settings.value("price_enabled", legacy_on, type=bool))
        self.chk_price.stateChanged.connect(self.on_price_filter_changed)
        bottom_layout.addWidget(self.chk_price)
        self.price_slider = QSlider(Qt.Orientation.Horizontal)
        self.price_slider.setRange(0, 1_000_000)
        self.price_slider.setSingleStep(25_000)
        self.price_slider.setPageStep(50_000)
        legacy_min = max(
            int(self.settings.value("min_flea", 0)),
            int(self.settings.value("min_trader", 0)),
        )
        self.price_slider.setValue(int(self.settings.value("min_price", legacy_min or 400_000)))
        self.price_slider.valueChanged.connect(self.on_price_filter_changed)
        bottom_layout.addWidget(self.price_slider)
        self.price_value = QLabel("₽400,000")
        bottom_layout.addWidget(self.price_value)

        bottom_layout.addWidget(QLabel("Quick show (by item)"))
        cat_row = QHBoxLayout()
        self.category_btns: dict[str, QPushButton] = {}
        for cat_id in CATEGORY_ORDER:
            meta = CATEGORY_META[cat_id]
            btn = QPushButton(meta["label"])
            btn.setCheckable(True)
            btn.setToolTip(meta["tip"])
            btn.setChecked(self.settings.value(f"category/{cat_id}", False, type=bool))
            btn.toggled.connect(self.on_category_toggles)
            self.category_btns[cat_id] = btn
            cat_row.addWidget(btn)
        bottom_layout.addLayout(cat_row)

        self.loot_stats = QLabel("Item hunt: —")
        self.loot_stats.setObjectName("status")
        self.loot_stats.setWordWrap(True)
        bottom_layout.addWidget(self.loot_stats)

        btn_row = QHBoxLayout()
        self.btn_filter = QPushButton("Filter Items...")
        self.btn_filter.clicked.connect(self.open_item_filter)
        btn_row.addWidget(self.btn_filter)
        self.btn_select_filtered = QPushButton("Select Filtered")
        self.btn_select_filtered.clicked.connect(self.select_all_filtered)
        btn_row.addWidget(self.btn_select_filtered)
        bottom_layout.addLayout(btn_row)

        self.chk_item_hunt = QCheckBox("Show selected items on map")
        self.chk_item_hunt.setChecked(self.settings.value("item_hunt_enabled", True, type=bool))
        self.chk_item_hunt.stateChanged.connect(self.refresh_map_layers)
        bottom_layout.addWidget(self.chk_item_hunt)

        self.chk_hide_locked_loot = QCheckBox("Hide Locked Room Loot")
        self.chk_hide_locked_loot.setToolTip(
            "Hide loot behind keyed doors. Does not change the Loot Route planner, which always skips locked rooms."
        )
        self.chk_hide_locked_loot.setChecked(self.settings.value("hide_locked_room_loot", False, type=bool))
        self.chk_hide_locked_loot.stateChanged.connect(self.on_hide_locked_loot_changed)
        bottom_layout.addWidget(self.chk_hide_locked_loot)

        self.chk_show_locked_doors = QCheckBox("Show Locked Doors")
        self.chk_show_locked_doors.setToolTip("Mark exact keyed door positions and the key name required.")
        self.chk_show_locked_doors.setChecked(self.settings.value("show_locked_doors", False, type=bool))
        self.chk_show_locked_doors.stateChanged.connect(self.on_show_locked_doors_changed)
        bottom_layout.addWidget(self.chk_show_locked_doors)

        self.extract_panel = ExtractAvailabilityPanel()
        self.extract_panel.changed.connect(self.on_extracts_available_changed)
        bottom_layout.addWidget(self.extract_panel)

        bottom_layout.addWidget(QLabel("Marker size"))
        self.marker_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.marker_scale_slider.setRange(40, 250)
        self.marker_scale_slider.setValue(int(float(self.settings.value("marker_scale", 0.85)) * 100))
        self.marker_scale_slider.valueChanged.connect(self.on_marker_scale_changed)
        bottom_layout.addWidget(self.marker_scale_slider)
        self.marker_scale_label = QLabel()
        bottom_layout.addWidget(self.marker_scale_label)
        self._update_marker_scale_label()
        self._marker_scale_timer = QTimer(self)
        self._marker_scale_timer.setSingleShot(True)
        self._marker_scale_timer.timeout.connect(self._apply_marker_scale)

        self.chk_topmost = QCheckBox("Always on top")
        self.chk_topmost.setChecked(self.settings.value("always_on_top", True, type=bool))
        self.chk_topmost.stateChanged.connect(self.on_topmost)
        bottom_layout.addWidget(self.chk_topmost)

        self.chk_autodelete = QCheckBox("Auto-delete raid screenshots")
        self.chk_autodelete.setChecked(self.auto_delete)
        self.chk_autodelete.stateChanged.connect(self.on_autodelete_toggle)
        bottom_layout.addWidget(self.chk_autodelete)

        live_row = QHBoxLayout()
        self.chk_live = QCheckBox("Continuous mode")
        self.chk_live.setToolTip(
            "While in raid, press V every N seconds for a live map track. Stops when you extract. F6 toggles this loop."
        )
        self.chk_live.setChecked(self.settings.value("live_mode", False, type=bool))
        self.chk_live.stateChanged.connect(self.on_live_mode_changed)
        live_row.addWidget(self.chk_live, 1)
        self.live_interval = QSpinBox()
        self.live_interval.setRange(2, 60)
        self.live_interval.setSuffix(" s")
        self.live_interval.setValue(int(self.settings.value("live_interval", 5)))
        self.live_interval.setToolTip("Seconds between automatic V screenshots")
        self.live_interval.valueChanged.connect(self.on_live_interval_changed)
        live_row.addWidget(self.live_interval)
        bottom_layout.addLayout(live_row)

        bottom_layout.addWidget(QLabel("Mini map (F7) · opacity (F8) · compass (F9)"))
        self.minimap_size = QSpinBox()
        self.minimap_size.setRange(180, 520)
        self.minimap_size.setSuffix(" px")
        self.minimap_size.setValue(int(self.settings.value("minimap_size", 300)))
        self.minimap_size.setToolTip("Size of the F7 overlay on your main monitor")
        self.minimap_size.valueChanged.connect(self.on_minimap_size_changed)
        bottom_layout.addWidget(self.minimap_size)
        bottom_layout.addWidget(QLabel("Mini map zoom"))
        self.minimap_zoom = QSlider(Qt.Orientation.Horizontal)
        self.minimap_zoom.setRange(1, 20)
        saved_zoom = int(self.settings.value("minimap_zoom", 5))
        self.minimap_zoom.setValue(max(1, min(20, saved_zoom)))
        self.minimap_zoom.setToolTip("1 = wide area around you · 20 = very close overlay zoom")
        self.minimap_zoom.valueChanged.connect(self.on_minimap_zoom_changed)
        bottom_layout.addWidget(self.minimap_zoom)
        self.minimap_zoom_label = QLabel()
        bottom_layout.addWidget(self.minimap_zoom_label)
        self._update_minimap_zoom_label()

        bottom_layout.addWidget(QLabel("Mini map marker size"))
        self.minimap_marker_slider = QSlider(Qt.Orientation.Horizontal)
        self.minimap_marker_slider.setRange(40, 250)
        self.minimap_marker_slider.setValue(
            int(float(self.settings.value("minimap_marker_scale", 0.7)) * 100)
        )
        self.minimap_marker_slider.setToolTip("Size of loot/extract/quest markers on the F7 overlay only")
        self.minimap_marker_slider.valueChanged.connect(self.on_minimap_marker_scale_changed)
        bottom_layout.addWidget(self.minimap_marker_slider)
        self.minimap_marker_label = QLabel()
        bottom_layout.addWidget(self.minimap_marker_label)
        self._update_minimap_marker_label()
        self._minimap_marker_timer = QTimer(self)
        self._minimap_marker_timer.setSingleShot(True)
        self._minimap_marker_timer.timeout.connect(self._apply_minimap_marker_scale)

        bottom_layout.addWidget(QLabel("Friend raid share"))
        self.friend_name_edit = QLineEdit()
        self.friend_name_edit.setPlaceholderText("Display name")
        self.friend_name_edit.setText(str(self.settings.value("friend_name", "") or ""))
        self.friend_name_edit.setMaxLength(24)
        bottom_layout.addWidget(self.friend_name_edit)
        self.friend_room_edit = QLineEdit()
        self.friend_room_edit.setPlaceholderText("Room code (share with friends)")
        self.friend_room_edit.setText(str(self.settings.value("friend_room", "") or ""))
        self.friend_room_edit.setMaxLength(24)
        bottom_layout.addWidget(self.friend_room_edit)
        friend_row = QHBoxLayout()
        self.btn_friend_color = QPushButton("Color")
        self.btn_friend_color.setToolTip("Color friends see you as on their map")
        self.btn_friend_color.clicked.connect(self.pick_friend_color)
        self._apply_friend_color_btn()
        friend_row.addWidget(self.btn_friend_color)
        self.btn_friend_join = QPushButton("Join")
        self.btn_friend_join.clicked.connect(self.join_friend_room)
        friend_row.addWidget(self.btn_friend_join)
        self.btn_friend_leave = QPushButton("Leave")
        self.btn_friend_leave.clicked.connect(self.leave_friend_room)
        friend_row.addWidget(self.btn_friend_leave)
        bottom_layout.addLayout(friend_row)
        self.friend_status_label = QLabel("Not in a room")
        self.friend_status_label.setObjectName("status")
        self.friend_status_label.setWordWrap(True)
        bottom_layout.addWidget(self.friend_status_label)

        bottom_layout.addWidget(QLabel("Tarkov screenshots (press V in-raid)"))
        self.screenshot_path_label = QLabel(str(self.screenshot_dir))
        self.screenshot_path_label.setObjectName("status")
        self.screenshot_path_label.setWordWrap(True)
        bottom_layout.addWidget(self.screenshot_path_label)
        self.btn_screenshot_dir = QPushButton("Change screenshots folder...")
        self.btn_screenshot_dir.clicked.connect(self.choose_screenshot_dir)
        bottom_layout.addWidget(self.btn_screenshot_dir)

        self.btn_center = QPushButton("Center on player")
        self.btn_center.clicked.connect(self.center_player)
        bottom_layout.addWidget(self.btn_center)

        self.btn_refresh = QPushButton("Refresh map data")
        self.btn_refresh.clicked.connect(lambda: self.load_map(self.current_map_slug, force_fetch=True))
        bottom_layout.addWidget(self.btn_refresh)

        self.pos_label = QLabel("Position: —")
        self.pos_label.setObjectName("status")
        bottom_layout.addWidget(self.pos_label)

        bottom_scroll = QScrollArea()
        bottom_scroll.setWidgetResizable(True)
        bottom_scroll.setFrameShape(QFrame.Shape.NoFrame)
        bottom_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        bottom_scroll.setWidget(bottom)
        bottom_scroll.setMinimumHeight(80)
        side_layout.addWidget(bottom_scroll, 2)

        map_host = QWidget()
        map_layout = QVBoxLayout(map_host)
        map_layout.setContentsMargins(0, 0, 0, 0)
        map_layout.setSpacing(0)

        map_top = QFrame()
        map_top.setObjectName("mapOverlay")
        top_row = QHBoxLayout(map_top)
        top_row.setContentsMargins(10, 8, 10, 8)
        top_row.addWidget(QLabel("Floor"))
        self.floor_combo = QComboBox()
        self.floor_combo.setMinimumWidth(80)
        self.floor_combo.currentIndexChanged.connect(self.on_floor_changed)
        top_row.addWidget(self.floor_combo)

        top_row.addWidget(QLabel("Quests"))
        self.quest_btn = QPushButton("None active")
        self.quest_btn.setMinimumWidth(80)
        self.quest_btn.setToolTip("Open active quests grouped by trader")
        self.quest_btn.clicked.connect(self.open_quest_panel)
        top_row.addWidget(self.quest_btn)
        self._quest_panel: QuestListPanel | None = None
        self.btn_import_quests = QPushButton("Import from logs")
        self.btn_import_quests.setToolTip(
            "Same method as TarkovQuestie: read accept/complete from EFT client logs."
        )
        self.btn_import_quests.clicked.connect(self.import_quests_from_logs)
        top_row.addWidget(self.btn_import_quests)
        self.btn_refresh_quests = QPushButton("Refresh quests")
        self.btn_refresh_quests.clicked.connect(lambda: self.refresh_player_quests(silent=False))
        top_row.addWidget(self.btn_refresh_quests)
        self.btn_quest_route = QPushButton("Quest Route")
        self.btn_quest_route.setToolTip("Fastest route through currently selected quest objectives, ending at a checked extract.")
        self.btn_quest_route.clicked.connect(lambda: self.start_route("quest"))
        top_row.addWidget(self.btn_quest_route)
        self.btn_loot_route = QPushButton("Loot Route")
        self.btn_loot_route.setToolTip("Efficient accessible loot route ending at a checked extract. Skips locked rooms.")
        self.btn_loot_route.clicked.connect(lambda: self.start_route("loot"))
        top_row.addWidget(self.btn_loot_route)
        top_row.addStretch(1)
        map_layout.addWidget(map_top)

        self.map_view = MapView()
        self.map_view.player_updated.connect(self._update_pos_label)
        self.map_view.marker_clicked.connect(self.on_marker_clicked)
        self.map_view.set_marker_scale(float(self.settings.value("marker_scale", 0.85)))
        map_layout.addWidget(self.map_view, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)
        splitter.addWidget(sidebar)
        splitter.addWidget(map_host)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(True)
        splitter.setSizes([280, 1100])

        self._set_mode_combo(self.current_game_mode)
        self.on_topmost()
        self._sync_live_timer()
        self._select_map_combo(self.current_map_slug)
        self._update_price_labels()
        QTimer.singleShot(1500, self.check_for_updates)

    def _update_marker_scale_label(self):
        scale = self.marker_scale_slider.value() / 100.0
        self.marker_scale_label.setText(f"Marker size: {scale:.0%}")

    def on_marker_scale_changed(self, value: int):
        self._update_marker_scale_label()
        self._marker_scale_timer.start(120)

    def _apply_marker_scale(self):
        scale = self.marker_scale_slider.value() / 100.0
        self.settings.setValue("marker_scale", scale)
        self.map_view.set_marker_scale(scale)

    def _minimap_marker_scale(self) -> float:
        if hasattr(self, "minimap_marker_slider"):
            return self.minimap_marker_slider.value() / 100.0
        return float(self.settings.value("minimap_marker_scale", 0.7))

    def _update_minimap_marker_label(self):
        scale = self._minimap_marker_scale()
        self.minimap_marker_label.setText(f"Mini map markers: {scale:.0%}")

    def on_minimap_marker_scale_changed(self, _value: int):
        self._update_minimap_marker_label()
        self._minimap_marker_timer.start(120)

    def _apply_minimap_marker_scale(self):
        scale = self._minimap_marker_scale()
        self.settings.setValue("minimap_marker_scale", scale)
        if self._minimap_ok():
            self.minimap.map_view.set_marker_scale(scale)

    def check_for_updates(self):
        if not self.settings.value("auto_update_check", True, type=bool):
            return
        self.status_label.setText("Checking for updates…")
        try:
            from .paths import is_frozen
            from .updater import apply_update, check_for_update

            info = check_for_update()
        except Exception:
            self.status_label.setText("Ready")
            return
        if not info:
            self.status_label.setText(f"Up to date (v{__version__})")
            QTimer.singleShot(4000, lambda: self.status_label.setText("Ready"))
            return

        remote = info["version"]
        # Packaged builds: apply automatically. Dev/source: keep Yes/No prompt.
        auto_apply = is_frozen()
        if not auto_apply:
            notes = (info.get("releaseNotes") or "").strip()
            msg = (
                f"WMNavigation {remote} is available "
                f"(you have {__version__}).\n\nUpdate now?"
            )
            if notes:
                msg += f"\n\n{notes[:400]}"
            reply = QMessageBox.question(
                self,
                "Update available",
                msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.status_label.setText("Ready")
                return

        self.status_label.setText(f"Updating to v{remote}…")
        try:
            apply_update(info["downloadUrl"])
            if not auto_apply:
                QMessageBox.information(
                    self,
                    "Updating",
                    "Update downloaded. WMNavigation will restart to finish installing.",
                )
            QApplication.instance().quit()
        except Exception as exc:
            self.status_label.setText("Update failed")
            QMessageBox.warning(self, "Update failed", str(exc))

    def _set_mode_combo(self, mode_key: str):
        for label, key in GAME_MODES.items():
            if key == mode_key:
                self.mode_combo.setCurrentText(label)
                return

    def _select_map_combo(self, slug: str):
        for i in range(self.map_combo.count()):
            label = self.map_combo.itemText(i)
            if self.map_slug_by_label.get(label) == slug:
                self.map_combo.setCurrentIndex(i)
                return

    def _update_pos_label(self, state: PlayerState):
        self.pos_label.setText(
            f"Position: X {state.x:.1f}  Y {state.y:.1f}  Z {state.z:.1f}  Facing {state.yaw_deg:.0f}°"
        )

    def _load_selection(self):
        key = f"selected/{self.current_game_mode}/{self.current_map_slug}"
        raw = self.settings.value(key, "")
        self.selected_item_ids = set(str(raw).split("|")) if raw else set()

    def _save_selection(self):
        key = f"selected/{self.current_game_mode}/{self.current_map_slug}"
        self.settings.setValue(key, "|".join(sorted(self.selected_item_ids)))

    def on_mode_changed(self, label: str):
        self.current_game_mode = GAME_MODES.get(label, "regular")
        self.settings.setValue("game_mode", self.current_game_mode)
        try:
            self.load_map(self.current_map_slug, force_fetch=False)
        except Exception as exc:
            self.status_label.setText(f"Map load failed: {exc}")

    def _update_price_labels(self):
        self.price_value.setText(f"₽{self.price_slider.value():,}")

    def on_price_filter_changed(self):
        self.settings.setValue("price_enabled", self.chk_price.isChecked())
        self.settings.setValue("min_price", self.price_slider.value())
        self._update_price_labels()
        # Debounce heavy map redraws while dragging the price slider.
        self._price_filter_timer.start(180)

    def apply_item_filters(self):
        price_on = self.chk_price.isChecked()
        new_filtered = visible_map_items(
            self.map_items,
            price_on,
            self.price_slider.value(),
        )
        old_ids = set(self.filtered_items.keys())
        new_ids = set(new_filtered.keys())
        had_filter = self.map_view._price_filter_ids is not None
        self.filtered_items = new_filtered
        # Do NOT wipe selected_item_ids here — lowering the slider must restore them.
        self._update_loot_stats()
        if new_ids == old_ids and price_on == had_filter:
            return
        self.refresh_map_layers()

    def _active_category_ids(self) -> set[str]:
        return {cid for cid, btn in self.category_btns.items() if btn.isChecked()}

    def _category_item_ids(self) -> set[str]:
        return ids_for_categories(self.map_items, self._active_category_ids())

    def on_category_toggles(self, _checked: bool = False):
        for cid, btn in self.category_btns.items():
            self.settings.setValue(f"category/{cid}", btn.isChecked())
        if self._active_category_ids():
            # Categories need Loose loot visible so item icons can draw.
            if not self.layer_sidebar.enabled_layers().get("loose_loot", False):
                self.layer_sidebar.set_layer_checked("loose_loot", True, emit=False)
                self.layer_sidebar.save_settings(self._layer_settings_prefix(), self.settings)
        self._update_loot_stats()
        self.refresh_map_layers()

    def _active_hunt_ids(self) -> set[str]:
        """Selected + quick-category items that pass the price filter."""
        ids = set(self._category_item_ids())
        if self.chk_item_hunt.isChecked():
            ids |= self.selected_item_ids
        return ids & set(self.filtered_items.keys())

    def _price_filter_active(self) -> bool:
        return self.chk_price.isChecked()

    def _price_filter_ids(self) -> set[str] | None:
        """Item ids allowed by the price filter, or None when filter is off."""
        if not self._price_filter_active():
            return None
        return set(self.filtered_items.keys())

    def _update_loot_stats(self):
        active = self._active_hunt_ids()
        spots = spots_for_selection(self.layer_data.loose_loot, active)
        loose_shown = spots_passing_price(self.layer_data.loose_loot, self._price_filter_ids())
        cats = self._active_category_ids()
        cat_note = ""
        if cats:
            labels = [CATEGORY_META[c]["label"] for c in CATEGORY_ORDER if c in cats]
            cat_note = f"\nQuick: {', '.join(labels)} ({len(self._category_item_ids())} items)"
        self.loot_stats.setText(
            f"Item hunt: {len(self.filtered_items)} pass filter · "
            f"{len(self.selected_item_ids)} selected · {len(spots)} spots"
            f"{cat_note}\n"
            f"Loose loot stars: {len(loose_shown) if not cats else 0} / {len(self.layer_data.loose_loot)}"
        )

    def select_all_filtered(self):
        self.selected_item_ids = set(self.filtered_items.keys())
        self._save_selection()
        self._update_loot_stats()
        self.refresh_map_layers()

    def open_item_filter(self):
        if not self.map_items:
            QMessageBox.information(
                self,
                "Filter Items",
                "No map items loaded yet.\nClick Refresh map data, then try again.",
            )
            return
        # Prefer price-filtered set; fall back to all map items so the dialog always opens.
        items = self.filtered_items or dict(self.map_items)
        if not items:
            QMessageBox.information(
                self,
                "Filter Items",
                "No items on this map match your price filter.\n"
                "Lower the min price or disable the filter, then try again.",
            )
            return
        try:
            dialog = ItemFilterDialog(items, self.selected_item_ids, self)
            dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
            if self.chk_topmost.isChecked():
                dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            if dialog.exec():
                shown = set(items.keys())
                # Keep selections for items not currently listed (e.g. filtered out by price).
                kept = self.selected_item_ids - shown
                self.selected_item_ids = kept | dialog.get_selected()
                self._save_selection()
                self._update_loot_stats()
                self.refresh_map_layers()
        except Exception as exc:
            QMessageBox.warning(self, "Filter Items", f"Could not open filter dialog:\n{exc}")

    def on_layers_changed(self):
        prefix = self._layer_settings_prefix()
        self.layer_sidebar.save_settings(prefix, self.settings)
        self.refresh_map_layers()

    def _minimap_ok(self) -> bool:
        return bool(getattr(self, "minimap", None))

    def on_floor_changed(self, index: int):
        if 0 <= index < len(self.floor_options):
            floor = self.floor_options[index]
            self.map_view.set_floor(floor)
            if self._minimap_ok():
                self.minimap.map_view.set_floor(floor)
                if self.minimap.isVisible() and self._last_player:
                    self._refocus_minimap_view(self.minimap.map_view)

    def _quest_settings_key(self) -> str:
        return f"active_quests/{self.current_game_mode}/{self.current_map_slug}"

    def import_quests_from_logs(self, *, silent: bool = False):
        """Full Questie-style import from BSG client logs (can take a few seconds)."""
        self._quest_import_silent = silent
        self.status_label.setText("Importing quests from EFT logs…")
        QApplication.processEvents()

        def work():
            try:
                if find_log_dir() is None:
                    self.bridge.quests_imported.emit(
                        {"__error__": f"EFT Logs folder not found ({describe_log_search()})"}
                    )
                    return
                states = import_quest_states_from_logs(prefer_mode=self.current_game_mode)
                save_states(states)
                self.bridge.quests_imported.emit(states)
            except Exception as exc:
                self.bridge.quests_imported.emit({"__error__": str(exc)})

        threading.Thread(target=work, daemon=True).start()

    @Slot(object)
    def on_quests_imported(self, states):
        silent = bool(getattr(self, "_quest_import_silent", False))
        if isinstance(states, dict) and states.get("__error__"):
            msg = states["__error__"]
            self.status_label.setText(msg)
            if not silent:
                QMessageBox.warning(self, "Quest import", msg)
            return
        if not isinstance(states, dict):
            return
        self._quest_log_states = states
        state = states.get(self.current_game_mode) or QuestLogState()
        active = state.active_ids()
        self.player_active_quest_ids = active if active else set()
        self._reload_quest_lists()
        if active:
            self.status_label.setText(
                f"Imported from logs · {len(active)} active · "
                f"{len(self.map_quests)} on this map · {len(self.anywhere_quests)} anywhere"
            )
        else:
            self.status_label.setText(
                "No quests in logs yet — accept quests in-game"
            )
        if not active and not silent:
            QMessageBox.information(
                self,
                "Quest import",
                "No accepted quests found in logs yet.\n\n"
                "Accept (or complete) quests in-game so Tarkov writes notifications, "
                "or check that the EFT Logs folder is present. "
                "Clearing cache in the BSG launcher deletes old log history.",
            )

    @Slot(object)
    def on_quest_log_events(self, events):
        if not events:
            return
        changed = False
        for event in events:
            if not isinstance(event, QuestEvent):
                continue
            state = self._quest_log_states.setdefault(event.mode, QuestLogState())
            before = set(state.active_ids())
            state.apply(event.kind, event.quest_id)
            if state.active_ids() != before:
                changed = True
        if not changed:
            return
        save_states(self._quest_log_states)
        state = self._quest_log_states.get(self.current_game_mode) or QuestLogState()
        self.player_active_quest_ids = state.active_ids()
        self._reload_quest_lists()
        self.status_label.setText(
            f"Quest log update · {len(self.player_active_quest_ids)} active · "
            f"{len(self.map_quests)} on map"
        )

    def refresh_player_quests(self, *, silent: bool = False):
        """Load active quests from EFT client log state (Questie method)."""
        state = self._quest_log_states.get(self.current_game_mode)
        if state is None:
            state = load_cached_state(self.current_game_mode)
            if state:
                self._quest_log_states[self.current_game_mode] = state
        if state is not None and (state.accepted or state.completed or state.failed):
            self.player_active_quest_ids = state.active_ids()
            self._reload_quest_lists()
            if not silent:
                self.status_label.setText(
                    f"Quests from logs · {len(self.player_active_quest_ids)} active · "
                    f"{len(self.map_quests)} on this map · {len(self.anywhere_quests)} anywhere"
                )
            return

        self.player_active_quest_ids = None
        self._reload_quest_lists()
        if not silent:
            self.status_label.setText(
                "No quest log state yet · accept quests in-game, then Import from logs"
            )

    def _reload_quest_lists(self):
        only_ids = self.player_active_quest_ids

        if only_ids is not None:
            self.map_quests, self.anywhere_quests = load_quests_split(
                self.current_map_slug,
                self.current_game_mode,
                only_ids=only_ids,
            )
            # Show all linked active quests on map by default.
            self.active_quest_ids = {q.id for q in self.map_quests}
            self._save_active_quests()
        else:
            self.map_quests = load_quests_for_map(self.current_map_slug, self.current_game_mode)
            self.anywhere_quests = []
            self._load_active_quests()
            valid = {q.id for q in self.map_quests}
            self.active_quest_ids &= valid

        self._rebuild_quest_menu()
        self.refresh_map_layers()
        self._refresh_quest_route_if_active()

    def _load_active_quests(self):
        raw = self.settings.value(self._quest_settings_key(), "")
        if isinstance(raw, str) and raw.strip():
            self.active_quest_ids = {x for x in raw.split(",") if x}
        elif isinstance(raw, (list, tuple)):
            self.active_quest_ids = {str(x) for x in raw}
        else:
            self.active_quest_ids = set()

    def _save_active_quests(self):
        self.settings.setValue(self._quest_settings_key(), ",".join(sorted(self.active_quest_ids)))

    def _update_quest_btn_label(self):
        n = len(self.active_quest_ids)
        total = len(self.map_quests) + len(self.anywhere_quests)
        if total == 0:
            self.quest_btn.setText("No quests")
        elif n == 0:
            self.quest_btn.setText(f"{total} active")
        else:
            self.quest_btn.setText(f"{n}/{len(self.map_quests)} on map · {len(self.anywhere_quests)} anywhere")

    def open_quest_panel(self):
        if self._quest_panel and self._quest_panel.isVisible():
            self._quest_panel.raise_()
            self._quest_panel.activateWindow()
            return
        map_title = self.current_map_slug.replace("-", " ").title()
        panel = QuestListPanel(
            self,
            map_quests=self.map_quests,
            anywhere_quests=self.anywhere_quests,
            active_ids=set(self.active_quest_ids),
            map_label=f"This map — {map_title}",
            from_logs=self.player_active_quest_ids is not None,
        )
        panel.toggled.connect(self._on_quest_toggled)
        panel.hide_all.connect(self._clear_active_quests)
        panel.show_all.connect(self._show_all_map_quests)
        self._quest_panel = panel
        panel.show()

    def _rebuild_quest_menu(self):
        """Refresh quest button label; reopen panel content if it's open."""
        self._update_quest_btn_label()
        if self._quest_panel and self._quest_panel.isVisible():
            self._quest_panel.close()
            self.open_quest_panel()

    def _clear_active_quests(self):
        self.active_quest_ids.clear()
        self._save_active_quests()
        if self._quest_panel and self._quest_panel.isVisible():
            self._quest_panel.set_all_map_checked(False)
        self._update_quest_btn_label()
        self.refresh_map_layers()
        self._refresh_quest_route_if_active()

    def _show_all_map_quests(self):
        self.active_quest_ids = {q.id for q in self.map_quests}
        self._save_active_quests()
        if self._quest_panel and self._quest_panel.isVisible():
            self._quest_panel.set_all_map_checked(True)
        self._update_quest_btn_label()
        self.refresh_map_layers()
        self._refresh_quest_route_if_active()

    def _on_quest_toggled(self, quest_id: str, checked: bool):
        if checked:
            self.active_quest_ids.add(quest_id)
        else:
            self.active_quest_ids.discard(quest_id)
        self._save_active_quests()
        self._update_quest_btn_label()
        self.refresh_map_layers()
        self._refresh_quest_route_if_active()

    def _refresh_quest_route_if_active(self):
        if self._route_kind == "quest":
            self._plan_route_async("quest", force=True)

    def _active_quest_spots(self) -> list[MapPoint]:
        spots: list[MapPoint] = []
        for quest in self.map_quests:
            if quest.id not in self.active_quest_ids:
                continue
            spots.extend(quest.spots)
        return spots

    def _select_floor_for_y(self, y: float):
        match = floor_for_y(y, self.floor_options)
        if not match:
            return
        try:
            idx = self.floor_options.index(match)
        except ValueError:
            return
        if self.floor_combo.currentIndex() == idx:
            # Still ensure map_view floor is set (e.g. after map reload).
            self.map_view.set_floor(match)
            if self._minimap_ok():
                self.minimap.map_view.set_floor(match)
            return
        self.floor_combo.blockSignals(True)
        self.floor_combo.setCurrentIndex(idx)
        self.floor_combo.blockSignals(False)
        self.map_view.set_floor(match)
        if self._minimap_ok():
            self.minimap.map_view.set_floor(match)

    def _build_visibility(self) -> LayerVisibility:
        enabled = self.layer_sidebar.enabled_layers()
        category_on = bool(self._active_category_ids())
        return LayerVisibility(
            loose_loot=enabled.get("loose_loot", False),
            container_ids=self.layer_sidebar.enabled_container_ids(),
            extract_pmc=enabled.get("extract_pmc", False),
            extract_scav=enabled.get("extract_scav", False),
            extract_coop=enabled.get("extract_coop", False),
            transits=enabled.get("transits", False),
            locks=enabled.get("locks", False),
            switches=enabled.get("switches", False),
            stationary_weapons=enabled.get("stationary_weapons", False),
            # Categories force item icons even if "Show selected" is off.
            item_hunt=self.chk_item_hunt.isChecked() or category_on,
            show_locked_doors=self.chk_show_locked_doors.isChecked(),
        )

    def refresh_map_layers(self):
        self.settings.setValue("item_hunt_enabled", self.chk_item_hunt.isChecked())
        state = dict(
            visibility=self._build_visibility(),
            selected_ids=self._active_hunt_ids(),
            price_filter_ids=self._price_filter_ids(),
            hide_loose_stars=bool(self._active_category_ids()),
            quest_spots=self._active_quest_spots(),
            haze_off_floor=True,
            hide_locked_room_loot=self.chk_hide_locked_loot.isChecked(),
            locked_loot_ids=self._locked_loot_ids,
            show_locked_doors=self.chk_show_locked_doors.isChecked(),
        )
        self.map_view.apply_layer_state(**state)
        self._sync_minimap_layers(**state)

    def _sync_minimap_layers(self, **state):
        if not self._minimap_ok():
            return
        self.minimap.map_view.apply_layer_state(**state)

    def _sync_minimap_map(self):
        if not self._minimap_ok():
            return
        src = self.map_view
        mm = self.minimap.map_view
        mm.load_svg(
            src._svg_source,
            src.map_rotation,
            src.map_bounds,
            src.map_transform,
            map_meta=src._map_meta,
            map_slug=src._map_slug,
        )
        mm.set_layer_data(self.layer_data)
        mm.set_floor(src._floor)
        mm.set_marker_scale(self._minimap_marker_scale())
        mm.apply_layer_state(
            visibility=self._build_visibility(),
            selected_ids=self._active_hunt_ids(),
            price_filter_ids=self._price_filter_ids(),
            hide_loose_stars=bool(self._active_category_ids()),
            quest_spots=self._active_quest_spots(),
            haze_off_floor=True,
            hide_locked_room_loot=self.chk_hide_locked_loot.isChecked(),
            locked_loot_ids=self._locked_loot_ids,
            show_locked_doors=self.chk_show_locked_doors.isChecked(),
        )
        if self._last_player:
            mm.set_player(self._last_player)
            self._refocus_minimap_view(mm)
        snaps = self.friend_sync.friends_snapshot() if self.friend_sync.room else {}
        mm.set_friends(list(snaps.values()), self.current_map_slug)
        if self._active_route and self._active_route.ok and self._active_route.waypoints:
            color = "#a855f7" if self._active_route.kind == "quest" else "#f59e0b"
            mm.set_route(self._active_route.waypoints, color=color, stops=self._active_route.stops)

    def on_minimap_size_changed(self, value: int):
        self.settings.setValue("minimap_size", int(value))
        if self._minimap_ok():
            self.minimap.set_size_px(int(value))

    def _minimap_radius_m(self) -> float:
        """Slider 1–20 → world meters shown around you on the F7 overlay."""
        level = int(self.minimap_zoom.value()) if hasattr(self, "minimap_zoom") else 8
        level = max(1, min(20, level))
        # Exponential so high levels get much closer than a % of the whole map.
        # 1 ≈ 160m, 10 ≈ 30m, 20 ≈ 8m
        return max(8.0, 160.0 * (0.83 ** (level - 1)))

    def _minimap_focus_fraction(self) -> float:
        """Legacy fraction fallback if a map has no transform scale."""
        level = int(self.minimap_zoom.value()) if hasattr(self, "minimap_zoom") else 8
        level = max(1, min(20, level))
        return max(0.004, 0.22 * (0.83 ** (level - 1)))

    def _refocus_minimap_view(self, view) -> None:
        view.focus_around_player(
            self._minimap_focus_fraction(),
            radius_m=self._minimap_radius_m(),
        )

    def _update_minimap_zoom_label(self):
        level = int(self.minimap_zoom.value())
        meters = self._minimap_radius_m()
        if level <= 3:
            tip = "wide"
        elif level <= 7:
            tip = "medium"
        elif level <= 12:
            tip = "close"
        else:
            tip = "tight"
        self.minimap_zoom_label.setText(f"Zoom {level}/20 · {tip} · ~{meters:.0f}m")

    def on_minimap_zoom_changed(self, value: int):
        self.settings.setValue("minimap_zoom", int(value))
        self._update_minimap_zoom_label()
        self._refocus_minimap()

    def _refocus_minimap(self):
        if not self._minimap_ok() or not self.minimap.isVisible():
            return
        mm = self.minimap.map_view
        if self._last_player:
            mm.set_player(self._last_player)
        self._refocus_minimap_view(mm)
        mm.viewport().update()
        self.minimap.update()

    @Slot(str)
    def on_global_hotkey(self, key: str):
        if key == "f6":
            self._set_screenshot_loop(not self._screenshot_loop_on)
        elif key == "f7":
            if not self._ensure_minimap():
                return
            visible = self.minimap.toggle()
            if visible:
                self._sync_minimap_map()
                self._refocus_minimap()
                pct = int(self.minimap.opacity_tier() * 100)
                self.status_label.setText(f"Mini map on · {pct}% opacity (F8 to cycle)")
            else:
                self.status_label.setText("Mini map off (F7)")
        elif key == "f8":
            if not self._minimap_ok() or not self.minimap.isVisible():
                return
            self.minimap.cycle_opacity()
            pct = int(self.minimap.opacity_tier() * 100)
            self.status_label.setText(f"Mini map opacity {pct}%")
        elif key == "f9":
            self._toggle_compass()

    def on_marker_clicked(self, kind: str, point: object, item: ItemInfo | None):
        if kind == "quest" and isinstance(point, MapPoint):
            meta = point.meta or {}
            name = meta.get("quest_name") or point.label or "Quest"
            desc = meta.get("description") or "Complete this objective."
            obj_type = meta.get("type") or ""
            lines = [f"<b>{name}</b>", desc]
            if obj_type:
                lines.append(f"<i>Type: {obj_type}</i>")
            if meta.get("optional"):
                lines.append("<i>Optional objective</i>")
            if meta.get("requires_key"):
                lines.append("<b>Key required (K)</b>")
            QMessageBox.information(self, "Quest", "<br>".join(lines))
            return

        if kind == "item_hunt" and isinstance(point, LootSpot) and item:
            active = self._active_hunt_ids()
            lines = [
                f"<b>{item.name}</b>",
                f"Flea: ₽{item.flea_price:,}",
                f"Trader: ₽{item.trader_price:,}",
                "",
            ]
            matches = [
                self.map_items[iid]
                for iid in point.item_ids
                if iid in active and iid in self.map_items
            ]
            if len(matches) > 1:
                lines.append("<b>Also selected at this spot:</b>")
                for other in sorted(matches, key=lambda i: i.best_price, reverse=True):
                    if other.id == item.id:
                        continue
                    lines.append(f"• {other.short_name} — ₽{other.best_price:,}")
            QMessageBox.information(self, "Loot Spawn", "<br>".join(lines))
            return

        if isinstance(point, MapPoint):
            title = point.label or kind.replace("_", " ").title()
            extra = ""
            if kind == "lock" and point.meta.get("key"):
                extra = f"<br>Key id: {point.meta.get('key')}"
            QMessageBox.information(self, title, f"<b>{title}</b><br>{kind.replace('_', ' ').title()}{extra}")
            return

        if isinstance(point, LootSpot) and kind == "loose_loot":
            # When price filter is on, only list items that pass it.
            pool = self.filtered_items if self._price_filter_active() else self.map_items
            found = items_at_spot(point, pool)
            if not found:
                QMessageBox.information(
                    self,
                    "Loose Loot",
                    "No items at this spawn pass the current price filter.",
                )
                return
            if self._price_filter_active():
                header = f"<b>Loose loot</b> — {len(found)} item(s) above filter:"
            else:
                header = f"<b>Loose loot</b> — {len(found)} possible item(s):"
            lines = [header]
            max_show = 40
            for other in found[:max_show]:
                lines.append(
                    f"• {other.short_name or other.name} — "
                    f"Flea ₽{other.flea_price:,} · Trader ₽{other.trader_price:,}"
                )
            if len(found) > max_show:
                lines.append(f"…and {len(found) - max_show} more")
            QMessageBox.information(self, "Loose Loot", "<br>".join(lines))

    @Slot(str)
    def on_auto_map(self, slug: str):
        self.raid_started_at = datetime.now()
        self.in_raid = True
        if slug != self.current_map_slug:
            self.load_map(slug)
            self._select_map_combo(slug)
        self.status_label.setText(f"Raid detected → {slug.replace('-', ' ').title()}")
        self._sync_live_timer()

    @Slot()
    def on_raid_end(self):
        self.in_raid = False
        self._live_timer.stop()
        self.status_label.setText("Raid ended")
        if self.auto_delete:
            self.delete_raid_screenshots()

    def delete_raid_screenshots(self):
        """Remove Tarkov coordinate screenshots from the watch folder.

        Prefer files from this raid (mtime >= raid_started_at). If we never
        captured a raid start time, clear all EFT-named shots in the folder —
        that folder is dedicated to V-key position screenshots.
        """
        if not self.screenshot_dir.exists():
            return
        start = self.raid_started_at
        removed = 0
        for path in list(self.screenshot_dir.glob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                continue
            if not is_eft_screenshot_name(path.name):
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
            if start is not None and mtime < start:
                continue
            try:
                path.unlink()
                removed += 1
                key = str(path.resolve())
                self._seen_screenshots.discard(key)
                self._seen_screenshots.discard(str(path))
            except OSError:
                pass
        if removed:
            self.status_label.setText(f"Raid ended · deleted {removed} screenshot(s)")
        self.raid_started_at = None

    @Slot(object)
    def on_player_update(self, state: PlayerState):
        self._last_player = state
        self.heading.set_authoritative(state.yaw_deg, self.map_view.map_rotation)
        self._select_floor_for_y(state.y)
        self.map_view.set_player(state)
        self.map_view.center_on_player()
        if self.compass is not None:
            self.compass.set_player_xz(state.x, state.z)
            self.compass.set_world_context(
                self.map_view.map_rotation,
                self.map_view.map_transform,
                self.current_map_slug,
            )
        if self._minimap_ok() and self.minimap.isVisible():
            self.minimap.map_view.set_player(state)
            self.minimap.map_view.set_floor(self.map_view._floor)
            self._refocus_minimap_view(self.minimap.map_view)
        floor_label = self.floor_combo.currentText()
        self.status_label.setText(
            f"Position locked · X {state.x:.1f}  Y {state.y:.1f}  Z {state.z:.1f}  "
            f"Facing {state.yaw_deg:.0f}° · {floor_label}"
        )
        if self.friend_sync.room:
            self.friend_sync.publish_position(
                map_slug=self.current_map_slug,
                x=state.x,
                y=state.y,
                z=state.z,
                yaw_deg=state.yaw_deg,
                name=self.friend_name_edit.text(),
                color=self._friend_color,
            )
        if self._route_kind:
            self._route_refresh_timer.start(1800)

    def _apply_friend_color_btn(self):
        color = self._friend_color if str(self._friend_color).startswith("#") else f"#{self._friend_color}"
        self.btn_friend_color.setStyleSheet(
            f"QPushButton {{ background: {color}; color: #0a0a0f; font-weight: 600; }}"
        )

    def pick_friend_color(self):
        current = QColor(self._friend_color)
        if not current.isValid():
            current = QColor("#38bdf8")
        chosen = QColorDialog.getColor(current, self, "Your marker color")
        if not chosen.isValid():
            return
        self._friend_color = chosen.name()
        self.settings.setValue("friend_color", self._friend_color)
        self._apply_friend_color_btn()
        if self.friend_sync.room and self.friend_sync._last_pos:
            self.friend_sync.publish_position(
                **self.friend_sync._last_pos,
                name=self.friend_name_edit.text(),
                color=self._friend_color,
            )

    def join_friend_room(self):
        name = self.friend_name_edit.text().strip() or "Operator"
        room = self.friend_room_edit.text().strip()
        self.settings.setValue("friend_name", name)
        self.settings.setValue("friend_room", room)
        self.settings.setValue("friend_color", self._friend_color)
        ok = self.friend_sync.join(room, name, self._friend_color)
        self.btn_friend_join.setEnabled(not ok)
        if ok and self.friend_sync._last_pos:
            self.friend_sync.publish_position(
                **self.friend_sync._last_pos,
                name=name,
                color=self._friend_color,
            )

    def leave_friend_room(self):
        self.friend_sync.leave()
        self.btn_friend_join.setEnabled(True)
        self.friend_status_label.setText("Not in a room")
        self.map_view.set_friends([], self.current_map_slug)
        if self._minimap_ok():
            self.minimap.map_view.set_friends([], self.current_map_slug)
        if self.compass is not None:
            self.compass.set_friends([], self.current_map_slug)

    @Slot(object)
    def on_friend_update(self, snaps):
        pings = list((snaps or {}).values())
        self.map_view.set_friends(pings, self.current_map_slug)
        if self._minimap_ok():
            self.minimap.map_view.set_friends(pings, self.current_map_slug)
        if self.compass is not None:
            self.compass.set_friends(pings, self.current_map_slug)
        if self.friend_sync.room:
            n = self.friend_sync.live_count(self.current_map_slug)
            total = self.friend_sync.live_count()
            self.friend_status_label.setText(
                f"Room {self.friend_sync.room} · {total} friend(s) · {n} on this map"
            )

    @Slot(str)
    def on_friend_status(self, text: str):
        self.friend_status_label.setText(text)
        self.btn_friend_join.setEnabled(not bool(self.friend_sync.room))

    def _refresh_friend_markers(self):
        if not self.friend_sync.room:
            return
        snaps = self.friend_sync.friends_snapshot()
        pings = list(snaps.values())
        self.map_view.set_friends(pings, self.current_map_slug)
        if self._minimap_ok():
            self.minimap.map_view.set_friends(pings, self.current_map_slug)
        if self.compass is not None:
            self.compass.set_friends(pings, self.current_map_slug)

    def choose_screenshot_dir(self):
        start = str(self.screenshot_dir if self.screenshot_dir.exists() else Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Tarkov Screenshots folder", start)
        if not chosen:
            return
        self.screenshot_dir = Path(chosen)
        self.settings.setValue("screenshot_dir", str(self.screenshot_dir))
        self.screenshot_path_label.setText(str(self.screenshot_dir))
        self._restart_screenshot_watch()

    def handle_screenshot(self, path: Path):
        if not self._screenshot_loop_on:
            return
        key = str(path.resolve()) if path.exists() else str(path)
        if key in self._seen_screenshots:
            return
        # Retry — OneDrive / game may still be writing the file.
        for delay in (200, 500, 1000, 1800):
            QTimer.singleShot(delay, lambda p=path, k=key: self._parse_screenshot_file(p, k))

    def _parse_screenshot_file(self, path: Path, key: str | None = None):
        if key and key in self._seen_screenshots:
            return
        if not path.exists():
            return
        try:
            if path.stat().st_size < 1024:
                return
        except OSError:
            return
        state = parse_screenshot(path)
        if not state:
            # Ignore non-coordinate screenshots quietly after first attempts.
            return
        resolved = str(path.resolve())
        if resolved in self._seen_screenshots:
            return
        self._seen_screenshots.add(resolved)
        if key:
            self._seen_screenshots.add(key)
        self.bridge.screenshot_parsed.emit(state)

    def _poll_screenshots(self):
        """Backup for OneDrive folders where filesystem events are flaky."""
        if not self._screenshot_loop_on:
            return
        folder = self.screenshot_dir
        if not folder.is_dir():
            return
        try:
            files = [
                f
                for f in folder.iterdir()
                if f.is_file()
                and f.suffix.lower() in {".png", ".jpg", ".jpeg"}
                and is_eft_screenshot_name(f.name)
            ]
        except OSError:
            return
        if not files:
            return
        newest = max(files, key=lambda f: f.stat().st_mtime)
        key = str(newest.resolve())
        if key in self._seen_screenshots:
            return
        # Only react to screenshots created while the app is running.
        age = datetime.now().timestamp() - newest.stat().st_mtime
        if age > 20:
            self._seen_screenshots.add(key)
            return
        self.handle_screenshot(newest)

    def _seed_seen_screenshots(self):
        folder = self.screenshot_dir
        if not folder.is_dir():
            return
        try:
            for f in folder.iterdir():
                if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                    try:
                        self._seen_screenshots.add(str(f.resolve()))
                    except OSError:
                        self._seen_screenshots.add(str(f))
        except OSError:
            pass

    def _restart_screenshot_watch(self):
        if hasattr(self, "observer") and self.observer:
            try:
                self.observer.stop()
                self.observer.join(timeout=2)
            except Exception:
                pass
            self.observer = None
        self._screenshot_poll.stop()
        self._seen_screenshots.clear()
        self._seed_seen_screenshots()
        self._start_screenshot_watch()

    def _start_screenshot_watch(self):
        folder = self.screenshot_dir
        if not folder.exists():
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.status_label.setText(f"Screenshots folder not found: {folder}")
                return

        self._seed_seen_screenshots()
        try:
            self.observer = Observer()
            self.observer.schedule(
                ScreenshotHandler(self.handle_screenshot),
                str(folder),
                recursive=False,
            )
            self.observer.start()
        except Exception as exc:
            self.observer = None
            self.status_label.setText(f"Screenshot watch failed: {exc}")
        if self._screenshot_loop_on and not self._screenshot_poll.isActive():
            self._screenshot_poll.start()
        self.screenshot_path_label.setText(str(folder))
        self.status_label.setText(f"Watching Tarkov screenshots (V) → {folder}")

    def start_watchers(self):
        self.log_watcher = LogWatcher(
            on_map=lambda slug: self.bridge.map_changed.emit(slug),
            on_raid_end=lambda: self.bridge.raid_ended.emit(),
            on_status=lambda text: QTimer.singleShot(0, lambda t=text: self.status_label.setText(t)),
        )
        self.log_watcher.start()
        self.quest_log_watcher = QuestLogWatcher(
            on_events=lambda events: self.bridge.quest_events.emit(events),
            on_status=lambda text: QTimer.singleShot(0, lambda t=text: None),
        )
        self.quest_log_watcher.start()
        self._start_screenshot_watch()
        # First-run / refresh: import quest history from logs in background (no modal).
        QTimer.singleShot(800, lambda: self.import_quests_from_logs(silent=True))

    def closeEvent(self, event):
        self._screenshot_poll.stop()
        self._live_timer.stop()
        self._friend_prune_timer.stop()
        self._route_refresh_timer.stop()
        try:
            if self.compass is not None:
                self.compass.shutdown()
        except Exception:
            pass
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            if self._minimap_ok():
                self.minimap.hide()
                self.minimap.close()
        except Exception:
            pass
        try:
            self.friend_sync.leave()
        except Exception:
            pass
        if hasattr(self, "observer") and self.observer:
            self.observer.stop()
            self.observer.join(timeout=2)
        if hasattr(self, "log_watcher"):
            self.log_watcher.stop()
        if hasattr(self, "quest_log_watcher"):
            self.quest_log_watcher.stop()
        super().closeEvent(event)

    def on_map_combo(self, label: str):
        slug = self.map_slug_by_label.get(label)
        if slug:
            self.load_map(slug)

    def on_topmost(self):
        enabled = self.chk_topmost.isChecked()
        self.settings.setValue("always_on_top", enabled)
        flags = self.windowFlags()
        if enabled:
            self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~Qt.WindowType.WindowStaysOnTopHint)
        self.show()

    def on_autodelete_toggle(self):
        self.auto_delete = self.chk_autodelete.isChecked()
        self.settings.setValue("auto_delete_screenshots", self.auto_delete)

    def on_live_mode_changed(self):
        self.settings.setValue("live_mode", self.chk_live.isChecked())
        self._sync_live_timer()
        if self.chk_live.isChecked():
            if self.in_raid:
                self.status_label.setText(
                    f"Continuous mode on · V every {self.live_interval.value()}s while in raid"
                )
            else:
                self.status_label.setText("Continuous mode on · waiting for raid")
        else:
            self.status_label.setText("Continuous mode off")

    def on_live_interval_changed(self, value: int):
        self.settings.setValue("live_interval", int(value))
        self._sync_live_timer()

    def _sync_live_timer(self):
        if not hasattr(self, "chk_live"):
            return
        interval_ms = max(2, int(self.live_interval.value())) * 1000
        self._live_timer.setInterval(interval_ms)
        if self.chk_live.isChecked() and self.in_raid and self._screenshot_loop_on:
            if not self._live_timer.isActive():
                self._live_timer.start()
                self._on_live_tick()
        else:
            self._live_timer.stop()

    def _on_live_tick(self):
        if not self.chk_live.isChecked() or not self.in_raid or not self._screenshot_loop_on:
            self._live_timer.stop()
            return
        if press_v_in_raid():
            return
        self.status_label.setText("Continuous mode · Tarkov window not found")

    def center_player(self):
        self.map_view.center_on_player()

    def on_hide_locked_loot_changed(self):
        self.settings.setValue("hide_locked_room_loot", self.chk_hide_locked_loot.isChecked())
        self.refresh_map_layers()

    def on_show_locked_doors_changed(self):
        self.settings.setValue("show_locked_doors", self.chk_show_locked_doors.isChecked())
        self.refresh_map_layers()

    def _extract_settings_key(self) -> str:
        return f"extracts_available/{self.current_game_mode}/{self.current_map_slug}"

    def _rebuild_extract_panel(self):
        extracts = unique_extracts(
            self.layer_data.extracts_pmc,
            self.layer_data.extracts_scav,
            self.layer_data.extracts_coop,
        )
        raw = self.settings.value(self._extract_settings_key(), None)
        selected = None
        if isinstance(raw, str) and raw.strip():
            selected = {x for x in raw.split("|") if x}
        elif isinstance(raw, (list, tuple)):
            selected = {str(x) for x in raw}
        self.extract_panel.rebuild(extracts, selected)

    def on_extracts_available_changed(self):
        keys = self.extract_panel.selected_keys()
        self.settings.setValue(self._extract_settings_key(), "|".join(sorted(keys)))
        if self._route_kind:
            self._plan_route_async(self._route_kind, force=True)

    def _set_screenshot_loop(self, on: bool):
        """F6: enable/disable the existing screenshot loop without creating a second one."""
        on = bool(on)
        if on == self._screenshot_loop_on:
            return
        self._screenshot_loop_on = on
        if on:
            if not self._screenshot_poll.isActive():
                self._screenshot_poll.start()
            self._sync_live_timer()
            self.status_label.setText("Screenshot processing on (F6)")
        else:
            self._live_timer.stop()
            self._screenshot_poll.stop()
            self.status_label.setText("Screenshot processing off (F6)")

    def _ensure_compass(self) -> bool:
        if self.compass is not None:
            return True
        try:
            self.compass = CompassHud(self.heading)
            if self._last_player:
                self.heading.set_authoritative(
                    self._last_player.yaw_deg, self.map_view.map_rotation
                )
                self.compass.set_player_xz(self._last_player.x, self._last_player.z)
            self.compass.set_world_context(
                self.map_view.map_rotation,
                self.map_view.map_transform,
                self.current_map_slug,
            )
            snaps = self.friend_sync.friends_snapshot() if self.friend_sync.room else {}
            self.compass.set_friends(list(snaps.values()), self.current_map_slug)
            return True
        except Exception as exc:
            self.status_label.setText(f"Compass failed: {exc}")
            self.compass = None
            return False

    def _toggle_compass(self):
        if not self._ensure_compass():
            return
        if self._last_player:
            self.heading.set_authoritative(
                self._last_player.yaw_deg, self.map_view.map_rotation
            )
            self.compass.set_player_xz(self._last_player.x, self._last_player.z)
        visible = self.compass.toggle()
        if visible:
            self.status_label.setText("Compass on (F9)")
        else:
            self.status_label.setText("Compass off (F9)")

    def _available_extracts(self) -> list:
        return self.extract_panel.selected_points()

    def start_route(self, kind: str):
        if not self._last_player:
            QMessageBox.information(self, "Route", "No player position yet. Press V in-raid first.")
            return
        extracts = self._available_extracts()
        if not extracts:
            QMessageBox.information(self, "Route", "Select at least one available extract.")
            self.status_label.setText("Select at least one available extract.")
            return
        if kind == "quest" and not self.active_quest_ids:
            QMessageBox.information(self, "Quest Route", "No quests selected.")
            self.status_label.setText("No quests selected.")
            return
        self._route_kind = kind
        self._route_origin = (self._last_player.x, self._last_player.y, self._last_player.z)
        self.status_label.setText("Planning route…")
        self._plan_route_async(kind, force=True)

    def _plan_route_async(self, kind: str, *, force: bool = False):
        player = self._last_player
        if not player:
            return
        extracts = list(self._available_extracts())
        spots = list(self._active_quest_spots())
        loot_spots = list(self.layer_data.loose_loot)
        items = dict(self.map_items)
        allowed = self._price_filter_ids()
        hunt = self._active_hunt_ids()
        if hunt:
            allowed = hunt if allowed is None else (allowed & hunt)
        locked = set(self._locked_loot_ids)
        remaining = remaining_seconds(
            in_raid=self.in_raid,
            raid_started_at=self.raid_started_at,
            duration_min=self.layer_data.raid_duration_min,
        )
        graph = self.nav_graph
        origin = (player.x, player.y, player.z)
        min_price = self.price_slider.value() if self.chk_price.isChecked() else 50_000
        self._route_gen += 1
        gen = self._route_gen
        prev = self._active_route if not force else None
        alert = force
        self._route_alert = alert

        def work():
            try:
                if kind == "quest":
                    result = plan_quest_route(
                        player=origin,
                        quest_spots=spots,
                        extracts=extracts,
                        graph=graph,
                    )
                else:
                    result = plan_loot_route(
                        player=origin,
                        spots=loot_spots,
                        items=items,
                        extracts=extracts,
                        allowed_ids=allowed,
                        locked_ids=locked,
                        remaining_s=remaining,
                        graph=graph,
                        min_value=min_price,
                    )
                result.gen = gen
                if not force and not should_refresh_route(prev, origin, result):
                    result = RouteResult(kind=kind, ok=True, message="__keep__", gen=gen)
                    result.stops = []
                self.bridge.route_ready.emit(result)
            except Exception as exc:
                self.bridge.route_ready.emit(
                    RouteResult(kind=kind, ok=False, message=str(exc), gen=gen)
                )

        threading.Thread(target=work, daemon=True).start()

    @Slot(object)
    def on_route_ready(self, result):
        if not isinstance(result, RouteResult):
            return
        if result.gen != self._route_gen:
            return
        if result.message == "__keep__":
            return
        if not result.ok:
            self.status_label.setText(result.message or "Route failed.")
            if getattr(self, "_route_alert", False) and result.message:
                QMessageBox.information(self, "Route", result.message)
            return
        self._active_route = result
        self._route_kind = result.kind
        color = "#a855f7" if result.kind == "quest" else "#f59e0b"
        self.map_view.set_route(result.waypoints, color=color, stops=result.stops)
        if self._minimap_ok():
            self.minimap.map_view.set_route(result.waypoints, color=color, stops=result.stops)
        note = result.message
        if result.skipped:
            note += " · skipped " + "; ".join(result.skipped[:3])
        self.status_label.setText(note)

    def _maybe_refresh_route(self):
        if not self._route_kind or not self._last_player:
            return
        now = (self._last_player.x, self._last_player.y, self._last_player.z)
        if not player_moved_enough(self._route_origin, now) and self._route_kind != "loot":
            return
        self._route_origin = now
        self._plan_route_async(self._route_kind, force=False)

    def _load_nav_graph(self, slug: str):
        """Optional future nav nodes: data/nav/{slug}.json. Empty = straight-line A* fallback."""
        self.nav_graph = NavGraph()
        path = app_root() / "data" / "nav" / f"{slug}.json"
        if not path.exists():
            return
        try:
            import json

            raw = json.loads(path.read_text(encoding="utf-8"))
            from .nav_graph import NavNode

            nodes = [
                NavNode(float(n["x"]), float(n.get("y") or 0), float(n["z"]), str(n.get("id") or i))
                for i, n in enumerate(raw.get("nodes") or [])
            ]
            edges = []
            for e in raw.get("edges") or []:
                if len(e) >= 2:
                    cost = float(e[2]) if len(e) > 2 else 1.0
                    edges.append((int(e[0]), int(e[1]), cost))
            self.nav_graph.load(nodes, edges or None)
        except Exception:
            self.nav_graph = NavGraph()

    def load_map(self, slug: str, force_fetch: bool = False):
        self.current_map_slug = slug
        meta = get_interactive_map(slug)
        if not meta:
            self.status_label.setText(f"No map data for {slug}")
            return

        svg_url = meta.get("svgPath")
        svg_path = None
        if svg_url:
            try:
                svg_path = str(cache_remote_file(svg_url, f"{slug}.svg"))
            except Exception as exc:
                self.status_label.setText(f"Failed to load map SVG: {exc}")
                return
        elif not meta.get("tilePath"):
            self.status_label.setText(f"No SVG/tiles available for {slug}")
            return

        if force_fetch:
            cache_file = cache_dir() / f"{self.current_game_mode}_{slug}_layers.json"
            if cache_file.exists():
                cache_file.unlink()

        self.layer_data = load_map_layers(slug, self.current_game_mode, map_meta=meta)
        self.map_items = self.layer_data.map_items
        self._locked_loot_ids = compute_locked_loot_ids(
            self.layer_data.loose_loot, self.layer_data.locks
        )
        self._load_nav_graph(slug)
        self.map_view.clear_route()
        if self._minimap_ok():
            self.minimap.map_view.clear_route()
        self._active_route = None
        self._route_kind = None
        self.floor_options = build_floor_options(meta)
        self.floor_combo.blockSignals(True)
        self.floor_combo.clear()
        for floor in self.floor_options:
            self.floor_combo.addItem(floor.label)
        self.floor_combo.setCurrentIndex(0)
        self.floor_combo.blockSignals(False)

        self.map_view.load_svg(
            svg_path,
            int(meta.get("coordinateRotation") or 0),
            meta.get("bounds"),
            meta.get("transform"),
            map_meta=meta,
            map_slug=slug,
        )
        self.map_view.set_floor(self.floor_options[0])
        self.map_view.set_layer_data(self.layer_data)

        prefix = self._layer_settings_prefix()
        self.layer_sidebar.rebuild(self.layer_data, prefix, self.settings)

        if self.player_active_quest_ids is not None or self._quest_log_states:
            self.refresh_player_quests(silent=True)
        else:
            self.player_active_quest_ids = None
            self.anywhere_quests = []
            self.map_quests = load_quests_for_map(slug, self.current_game_mode)
            self._load_active_quests()
            valid = {q.id for q in self.map_quests}
            self.active_quest_ids &= valid
            self._rebuild_quest_menu()

        self._load_selection()
        self._rebuild_extract_panel()
        if self.compass is not None:
            self.compass.set_world_context(
                self.map_view.map_rotation,
                self.map_view.map_transform,
                slug,
            )
        self.apply_item_filters()

        total_extracts = (
            len(self.layer_data.extracts_pmc)
            + len(self.layer_data.extracts_scav)
            + len(self.layer_data.extracts_coop)
        )
        total_containers = sum(len(c.spots) for c in self.layer_data.containers.values())
        note = ""
        if not svg_path:
            note = " · tile map"
        layers_empty = (
            total_extracts == 0
            and total_containers == 0
            and len(self.layer_data.loose_loot) == 0
        )
        if layers_empty:
            note += " · no layer data — check net / Refresh map"
        active_q = len(self.active_quest_ids)
        self.status_label.setText(
            f"{slug.replace('-', ' ').title()} · {total_extracts} extracts · "
            f"{total_containers} containers · {len(self.layer_data.loose_loot)} loose loot · "
            f"{len(self.map_items)} hunt items · {len(self.map_quests)} map quests"
            f"{f' ({active_q} shown)' if active_q else ''}"
            f"{f' · {len(self.anywhere_quests)} anywhere' if self.anywhere_quests else ''}"
            f"{note}"
        )
        self._refresh_friend_markers()
        if self._minimap_ok() and self.minimap.isVisible():
            self._sync_minimap_map()


def run():
    import faulthandler
    import traceback
    from pathlib import Path

    crash_path = Path.home() / "Desktop" / "WMNavigation_crash.log"
    # OneDrive Desktop fallback
    desk2 = Path.home() / "OneDrive" / "Desktop" / "WMNavigation_crash.log"

    def _log_crash(text: str):
        for path in (crash_path, desk2):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except Exception:
                pass

    try:
        fault_file = (crash_path if crash_path.parent.exists() else desk2).open("a", encoding="utf-8")
        faulthandler.enable(file=fault_file, all_threads=True)
    except Exception:
        try:
            faulthandler.enable(all_threads=True)
        except Exception:
            pass

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("WMNavigation")
        window = MainWindow()
        window.resize(1400, 900)
        window.setMinimumSize(480, 320)
        window.show()
        sys.exit(app.exec())
    except Exception:
        _log_crash(traceback.format_exc())
        raise
