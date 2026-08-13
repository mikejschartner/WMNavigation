"""Global F7/F8 hotkeys via Win32 RegisterHotKey on the main window HWND."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

user32 = ctypes.windll.user32

VK_F7 = 0x76
VK_F8 = 0x77
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkeys"):
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, eventType, message):
        try:
            if isinstance(eventType, (bytes, bytearray)):
                et = eventType.decode("ascii", errors="ignore")
            else:
                et = str(eventType)
            if "windows" not in et:
                return False, 0
            addr = int(message)
            msg = ctypes.cast(addr, ctypes.POINTER(wintypes.MSG)).contents
            if int(msg.message) != WM_HOTKEY:
                return False, 0
            wid = int(msg.wParam)
            if wid == 1:
                self._owner.pressed.emit("f7")
                return True, 0
            if wid == 2:
                self._owner.pressed.emit("f8")
                return True, 0
        except Exception:
            pass
        return False, 0


class GlobalHotkeys(QObject):
    """Register F7/F8 on the app window so hotkeys work even when Tarkov is focused."""

    pressed = Signal(str)  # "f7" | "f8"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hwnd = 0
        self._filter: _HotkeyFilter | None = None
        self._app = None

    def start(self, hwnd: int):
        """Bind hotkeys to an existing top-level window handle."""
        self.stop()
        hwnd = int(hwnd or 0)
        if hwnd <= 0:
            return False
        self._hwnd = hwnd
        try:
            # Prefer no-repeat; fall back to plain modifiers if the OS rejects it.
            ok7 = bool(user32.RegisterHotKey(self._hwnd, 1, MOD_NOREPEAT, VK_F7))
            if not ok7:
                ok7 = bool(user32.RegisterHotKey(self._hwnd, 1, 0, VK_F7))
            ok8 = bool(user32.RegisterHotKey(self._hwnd, 2, MOD_NOREPEAT, VK_F8))
            if not ok8:
                ok8 = bool(user32.RegisterHotKey(self._hwnd, 2, 0, VK_F8))
            if not ok7 and not ok8:
                self._hwnd = 0
                return False
            from PySide6.QtWidgets import QApplication

            self._app = QApplication.instance()
            self._filter = _HotkeyFilter(self)
            if self._app is not None:
                self._app.installNativeEventFilter(self._filter)
            return True
        except Exception:
            self.stop()
            return False

    def stop(self):
        if self._filter is not None and self._app is not None:
            try:
                self._app.removeNativeEventFilter(self._filter)
            except Exception:
                pass
        self._filter = None
        self._app = None
        if self._hwnd:
            try:
                user32.UnregisterHotKey(self._hwnd, 1)
                user32.UnregisterHotKey(self._hwnd, 2)
            except Exception:
                pass
        self._hwnd = 0
