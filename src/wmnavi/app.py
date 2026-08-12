"""WMNavigation main window."""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import __version__
from .assets import cache_remote_file
from .coords import PlayerState
from .data_loader import get_interactive_map, list_map_names
from .floors import FloorOption, build_floor_options, floor_for_y
from .item_categories import CATEGORY_META, CATEGORY_ORDER, ids_for_categories
from .item_filter_dialog import ItemFilterDialog
from .layer_sidebar import MapLayersSidebar
from .log_watcher import LogWatcher, describe_log_search, find_log_dir
from .loot_filter import items_at_spot, spots_for_selection, spots_passing_price, visible_map_items
from .loot_loader import GAME_MODES
from .map_data_loader import load_map_layers
from .map_view import LayerVisibility, MapView
from .models import ItemInfo, LootSpot, MapLayerData, MapPoint
from .paths import app_root, cache_dir
from .quest_loader import QuestInfo, load_quests_for_map, load_quests_split, objective_to_task_index
from .quest_log_sync import (
    QuestEvent,
    QuestLogState,
    QuestLogWatcher,
    import_quest_states_from_logs,
    load_cached_state,
    save_states,
)
from .account_link_dialog import AccountLinkDialog
from .profile_link import (
    active_ids_from_tracker,
    fetch_started_quest_ids,
    fetch_tracker_progress,
)
from .quest_panel import QuestListPanel
from .screenshot import default_screenshot_dir, is_eft_screenshot_name, parse_screenshot
from .theme import STYLESHEET


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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"WMNavigation v{__version__}")
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
        self.linked_account_id = str(self.settings.value("tarkov_account_id", "") or "")
        self.linked_account_mode = str(self.settings.value("tarkov_account_mode", "regular") or "regular")
        self.linked_nickname = str(self.settings.value("tarkov_nickname", "") or "")
        self.tracker_token = str(self.settings.value("tarkov_tracker_token", "") or "")
        self.player_active_quest_ids: set[str] | None = None  # None = manual catalog mode
        self._tracker_exclude_ids: set[str] = set()
        self._tracker_completed_ids: set[str] = set()
        self._quest_log_states: dict[str, QuestLogState] = {}
        cached = load_cached_state(self.current_game_mode)
        if cached:
            self._quest_log_states[self.current_game_mode] = cached
            self.player_active_quest_ids = cached.active_ids()
        self.raid_started_at: datetime | None = None
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

        self._build_ui()
        self.setStyleSheet(STYLESHEET)
        self.load_map(self.current_map_slug)
        self.start_watchers()

    def _layer_settings_prefix(self) -> str:
        return f"layers/{self.current_game_mode}/{self.current_map_slug}"

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(340)
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
        self.layer_sidebar.setMinimumHeight(280)
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
        bottom_scroll.setMinimumHeight(220)
        bottom_scroll.setMaximumHeight(360)
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
        self.floor_combo.setMinimumWidth(120)
        self.floor_combo.currentIndexChanged.connect(self.on_floor_changed)
        top_row.addWidget(self.floor_combo)

        top_row.addWidget(QLabel("Quests"))
        self.quest_btn = QPushButton("None active")
        self.quest_btn.setMinimumWidth(160)
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
        self.btn_link_account = QPushButton("Link account…")
        self.btn_link_account.setToolTip("Optional fallback (tarkov.dev / Tracker). Logs are preferred.")
        self.btn_link_account.clicked.connect(self.open_account_link)
        top_row.addWidget(self.btn_link_account)
        top_row.addStretch(1)
        map_layout.addWidget(map_top)
        self._update_link_btn_label()

        self.map_view = MapView()
        self.map_view.player_updated.connect(self._update_pos_label)
        self.map_view.marker_clicked.connect(self.on_marker_clicked)
        self.map_view.set_marker_scale(float(self.settings.value("marker_scale", 0.85)))
        map_layout.addWidget(self.map_view, 1)

        layout.addWidget(sidebar)
        layout.addWidget(map_host, 1)

        self._set_mode_combo(self.current_game_mode)
        self.on_topmost()
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
        self.load_map(self.current_map_slug, force_fetch=False)

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

    def on_floor_changed(self, index: int):
        if 0 <= index < len(self.floor_options):
            self.map_view.set_floor(self.floor_options[index])

    def _quest_settings_key(self) -> str:
        return f"active_quests/{self.current_game_mode}/{self.current_map_slug}"

    def _update_link_btn_label(self):
        if self.linked_nickname or self.linked_account_id:
            label = self.linked_nickname or self.linked_account_id
            if self.tracker_token:
                self.btn_link_account.setText(f"Linked: {label}")
            else:
                self.btn_link_account.setText(f"ID: {label}")
        else:
            self.btn_link_account.setText("Link account")

    def open_account_link(self):
        dialog = AccountLinkDialog(
            self,
            account_id=self.linked_account_id,
            game_mode=self.linked_account_mode or self.current_game_mode,
            nickname=self.linked_nickname,
            tracker_token=self.tracker_token,
        )
        dialog.linked.connect(self._on_account_linked)
        dialog.exec()

    def _on_account_linked(self, account_id: str, mode: str, nickname: str, token: str):
        self.linked_account_id = account_id
        self.linked_account_mode = mode or "regular"
        self.linked_nickname = nickname
        self.tracker_token = token
        self.settings.setValue("tarkov_account_id", account_id)
        self.settings.setValue("tarkov_account_mode", self.linked_account_mode)
        self.settings.setValue("tarkov_nickname", nickname)
        self.settings.setValue("tarkov_tracker_token", token)
        self._update_link_btn_label()
        self.refresh_player_quests()

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
        """Prefer EFT log state (Questie method); optional Tracker/tarkov.dev fallback."""
        self._tracker_exclude_ids = set()
        self._tracker_completed_ids = set()

        # 1) Log-derived active quests
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

        # 2) Optional Tracker / tarkov.dev fallbacks
        self.player_active_quest_ids = None
        errors: list[str] = []

        if self.tracker_token:
            try:
                progress = fetch_tracker_progress(self.tracker_token)
                index = objective_to_task_index(self.current_game_mode)
                active = active_ids_from_tracker(progress, index)
                self._tracker_completed_ids = set(progress.completed_ids) | set(progress.failed_ids)
                if active:
                    self.player_active_quest_ids = active
                elif self._tracker_completed_ids:
                    self._tracker_exclude_ids = set(self._tracker_completed_ids)
                if progress.display_name and not self.linked_nickname:
                    self.linked_nickname = progress.display_name
                    self.settings.setValue("tarkov_nickname", self.linked_nickname)
                    self._update_link_btn_label()
            except Exception as exc:
                errors.append(str(exc))

        if self.player_active_quest_ids is None and self.linked_account_id and not self.tracker_token:
            try:
                started = fetch_started_quest_ids(self.linked_account_id, self.linked_account_mode)
                if started:
                    self.player_active_quest_ids = started
                else:
                    errors.append(
                        "No quests in logs yet, and tarkov.dev has no quest list. "
                        "Click Import from logs after accepting quests in-game."
                    )
            except Exception as exc:
                errors.append(str(exc))

        self._reload_quest_lists()
        if not silent and errors and self.player_active_quest_ids is None and not self._tracker_exclude_ids:
            QMessageBox.information(self, "Quests", "\n".join(errors))
        elif not silent and self.player_active_quest_ids is not None:
            n = len(self.player_active_quest_ids)
            self.status_label.setText(
                f"Loaded {n} active quest(s) · "
                f"{len(self.map_quests)} on this map · {len(self.anywhere_quests)} anywhere"
            )


    def _reload_quest_lists(self):
        only_ids = self.player_active_quest_ids
        exclude = getattr(self, "_tracker_exclude_ids", set()) or set()

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
            # Catalog / completed-filtered mode.
            self.map_quests = load_quests_for_map(self.current_map_slug, self.current_game_mode)
            if exclude:
                self.map_quests = [q for q in self.map_quests if q.id not in exclude]
            self.anywhere_quests = []
            self._load_active_quests()
            valid = {q.id for q in self.map_quests}
            self.active_quest_ids &= valid

        self._rebuild_quest_menu()
        self.refresh_map_layers()

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

    def _show_all_map_quests(self):
        self.active_quest_ids = {q.id for q in self.map_quests}
        self._save_active_quests()
        if self._quest_panel and self._quest_panel.isVisible():
            self._quest_panel.set_all_map_checked(True)
        self._update_quest_btn_label()
        self.refresh_map_layers()

    def _on_quest_toggled(self, quest_id: str, checked: bool):
        if checked:
            self.active_quest_ids.add(quest_id)
        else:
            self.active_quest_ids.discard(quest_id)
        self._save_active_quests()
        self._update_quest_btn_label()
        self.refresh_map_layers()

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
            return
        self.floor_combo.blockSignals(True)
        self.floor_combo.setCurrentIndex(idx)
        self.floor_combo.blockSignals(False)
        self.map_view.set_floor(match)

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
        )

    def refresh_map_layers(self):
        self.settings.setValue("item_hunt_enabled", self.chk_item_hunt.isChecked())
        self.map_view.apply_layer_state(
            visibility=self._build_visibility(),
            selected_ids=self._active_hunt_ids(),
            price_filter_ids=self._price_filter_ids(),
            hide_loose_stars=bool(self._active_category_ids()),
            quest_spots=self._active_quest_spots(),
            haze_off_floor=True,
        )

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
        if slug != self.current_map_slug:
            self.load_map(slug)
            self._select_map_combo(slug)
        self.status_label.setText(f"Raid detected → {slug.replace('-', ' ').title()}")

    @Slot()
    def on_raid_end(self):
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
        self._select_floor_for_y(state.y)
        self.map_view.set_player(state)
        self.map_view.center_on_player()
        floor_label = self.floor_combo.currentText()
        self.status_label.setText(
            f"Position locked · X {state.x:.1f}  Y {state.y:.1f}  Z {state.z:.1f}  "
            f"Facing {state.yaw_deg:.0f}° · {floor_label}"
        )

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

    def center_player(self):
        self.map_view.center_on_player()

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

        if self.player_active_quest_ids is not None or self._quest_log_states or self.tracker_token or self.linked_account_id:
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
        linked = ""
        if self.linked_nickname or self.linked_account_id:
            linked = f" · linked {self.linked_nickname or self.linked_account_id}"
        self.status_label.setText(
            f"{slug.replace('-', ' ').title()} · {total_extracts} extracts · "
            f"{total_containers} containers · {len(self.layer_data.loose_loot)} loose loot · "
            f"{len(self.map_items)} hunt items · {len(self.map_quests)} map quests"
            f"{f' ({active_q} shown)' if active_q else ''}"
            f"{f' · {len(self.anywhere_quests)} anywhere' if self.anywhere_quests else ''}"
            f"{linked}{note}"
        )


def run():
    app = QApplication(sys.argv)
    app.setApplicationName("WMNavigation")
    window = MainWindow()
    window.resize(1400, 900)
    window.show()
    sys.exit(app.exec())
