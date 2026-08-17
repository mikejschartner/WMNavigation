"""Global F6–F11 detection via key-state polling (no Win32 hotkey thread/filter)."""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, QTimer, Signal

user32 = ctypes.windll.user32

VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A

_KEYS = (
    (VK_F6, "f6"),
    (VK_F7, "f7"),
    (VK_F8, "f8"),
    (VK_F9, "f9"),
    (VK_F10, "f10"),
    (VK_F11, "f11"),
)


class GlobalHotkeys(QObject):
    """Poll F6–F11 so they work while Tarkov is focused, without RegisterHotKey."""

    pressed = Signal(str)  # "f6" .. "f11"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._prev = {vk: False for vk, _name in _KEYS}

    def start(self, _hwnd: int = 0) -> bool:
        if not self._timer.isActive():
            self._prev = {vk: False for vk, _name in _KEYS}
            self._timer.start()
        return True

    def stop(self):
        self._timer.stop()

    def _down(self, vk: int) -> bool:
        try:
            # High bit set => key currently down
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def _tick(self):
        try:
            for vk, name in _KEYS:
                now = self._down(vk)
                was = self._prev.get(vk, False)
                self._prev[vk] = now
                if now and not was:
                    self.pressed.emit(name)
        except Exception:
            pass
