"""Loot Value hover service. Recognition only while the toggle is on."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .loot_index import ItemIconIndex
from .loot_overlay import LootValueHud
from .loot_recognize import HIDE_MS, HoverRecognizer, LootMatch
from .loot_loader import load_items_catalog
from .models import ItemInfo


class LootValueService(QObject):
    updated = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.index = ItemIconIndex()
        self.recognizer = HoverRecognizer(self.index)
        self.hud = LootValueHud()
        self.catalog: dict[str, ItemInfo] = {}
        self.debug = False
        self._on = False
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.last: LootMatch | None = None
        self.index_size = 0
        self.index_ms = 0.0

    @property
    def active(self) -> bool:
        return self._on

    def set_debug(self, on: bool):
        self.debug = bool(on)
        self.hud.debug = self.debug
        self.recognizer.debug  # keep last

    def start(self, game_mode: str):
        if self._on:
            return
        self.catalog = load_items_catalog(game_mode) or {}
        n = self.index.build(self.catalog)
        self.index_size = n
        self.index_ms = self.index.built_at * 1000.0
        self.recognizer = HoverRecognizer(self.index)
        self._on = True
        self._timer.start()

    def stop(self):
        self._on = False
        self._timer.stop()
        self.recognizer.reset()
        self.hud.shutdown()
        self.last = None

    def shutdown(self):
        self.stop()
        self.hud.shutdown()

    def _lookup(self, item_id: str) -> ItemInfo | None:
        return self.catalog.get(item_id)

    def _tick(self):
        if not self._on:
            return
        match = self.recognizer.tick(self._lookup)
        self.last = self.recognizer.debug
        self.hud.debug = self.debug
        if match is None:
            self.hud.schedule_hide(HIDE_MS)
            self.updated.emit(self.last)
            return
        if match.item_id:
            self.hud.show_match(match)
        else:
            self.hud.schedule_hide(HIDE_MS)
        self.updated.emit(self.last)
