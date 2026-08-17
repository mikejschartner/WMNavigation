"""Visual filter engine: session profiles + per-monitor LUT, restored on exit."""

from __future__ import annotations

import atexit

from PySide6.QtCore import QObject, Signal

from ..applog import get_logger
from .gamma import GammaFilter
from .monitors import default_monitor_key, monitor_by_key
from .profiles import VisualProfileManager, VisualSettings

log = get_logger("wmnavi.visual")

_ENGINES: list["VisualFilterEngine"] = []


def _restore_all_atexit():
    for engine in list(_ENGINES):
        try:
            engine.shutdown()
        except Exception:
            pass


atexit.register(_restore_all_atexit)


class VisualFilterEngine(QObject):
    filter_toggled = Signal(bool)
    profile_changed = Signal(str)
    monitor_changed = Signal(str)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = VisualProfileManager()
        self.manager.monitor_key = default_monitor_key()
        self._gamma = GammaFilter()
        self._shut_down = False
        _ENGINES.append(self)
        log.info("Renderer initialized")

    def settings(self) -> VisualSettings:
        return self.manager.draft.copy()

    def set_draft(self, settings: VisualSettings, *, apply_now: bool = True):
        self.manager.draft = settings.copy()
        if apply_now:
            self._push()

    def set_enabled(self, on: bool):
        on = bool(on)
        if self.manager.filter_enabled == on:
            self._push()
            return
        self.manager.filter_enabled = on
        if on:
            log.info("Filter enabled")
        else:
            log.info("Filter disabled")
            self._gamma.restore_all()
        self._push()
        self.filter_toggled.emit(on)

    def toggle_filter(self) -> bool:
        self.set_enabled(not self.manager.filter_enabled)
        return self.manager.filter_enabled

    def select_profile(self, index: int, *, announce: bool = False) -> str:
        settings = self.manager.select(index)
        name = self.manager.active().name
        log.info("Profile switched: %s", name)
        self._push()
        self.profile_changed.emit(name)
        if announce:
            self.status.emit(f"Visual Profile: {name}")
        return name

    def cycle_profile(self) -> str:
        settings = self.manager.cycle()
        name = self.manager.active().name
        log.info("Profile switched: %s", name)
        self._push()
        self.profile_changed.emit(name)
        self.status.emit(f"Visual Profile: {name}")
        return name

    def set_monitor_key(self, key: str):
        key = str(key or "")
        if key == self.manager.monitor_key:
            return
        old = self.manager.monitor_key
        if old:
            self._gamma.restore_device(old)
            log.info("Target monitor changed: %s -> %s", old, key)
        self.manager.monitor_key = key
        self._push()
        self.monitor_changed.emit(key)

    def save_profile(self) -> bool:
        ok = self.manager.save_draft_to_active()
        if ok:
            log.info("Profile saved (session): %s", self.manager.active().name)
            self.status.emit(f"Saved {self.manager.active().name} for this session")
        else:
            self.status.emit("Default cannot be overwritten")
        return ok

    def reset_profile(self):
        self.manager.reset_active()
        self._push()
        self.profile_changed.emit(self.manager.active().name)
        self.status.emit(f"Reset {self.manager.active().name}")

    def live_settings(self) -> VisualSettings:
        if not self.manager.filter_enabled:
            return VisualSettings()
        if self.manager.active_index == 0:
            return VisualSettings()
        return self.manager.draft.copy()

    def _push(self):
        if self._shut_down:
            return
        monitor = monitor_by_key(self.manager.monitor_key)
        device = monitor.device if monitor else self.manager.monitor_key
        settings = self.live_settings()
        if not self.manager.filter_enabled or settings.is_identity():
            if device:
                self._gamma.restore_device(device)
            else:
                self._gamma.restore_all()
            return
        ok = self._gamma.apply(device, settings)
        if not ok:
            self.status.emit("Could not apply filter to that monitor")

    def shutdown(self):
        if self._shut_down:
            return
        self._shut_down = True
        self.manager.filter_enabled = False
        try:
            self._gamma.restore_all()
        except Exception:
            pass
        if self in _ENGINES:
            _ENGINES.remove(self)
        self.manager = VisualProfileManager()
