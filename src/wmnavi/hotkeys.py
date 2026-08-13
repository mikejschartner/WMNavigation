"""Global F7/F8 hotkeys via Win32 RegisterHotKey (works while Tarkov is focused)."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

user32 = ctypes.windll.user32

VK_F7 = 0x76
VK_F8 = 0x77
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
HWND_MESSAGE = wintypes.HWND(-3)


class GlobalHotkeys(QObject):
    """Background message loop; emits key names on the Qt thread via queued signals."""

    pressed = Signal(str)  # "f7" | "f8"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._running = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="wmnavi-hotkeys", daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        tid = self._thread_id
        if tid:
            try:
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        self._thread_id = 0

    def _run(self):
        self._thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())
        hwnd = user32.CreateWindowExW(
            0,
            "STATIC",
            "wmnavi-hotkeys",
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            None,
            None,
            None,
        )
        if not hwnd:
            return
        try:
            user32.RegisterHotKey(hwnd, 1, MOD_NOREPEAT, VK_F7)
            user32.RegisterHotKey(hwnd, 2, MOD_NOREPEAT, VK_F8)
            msg = wintypes.MSG()
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                if msg.message == WM_HOTKEY:
                    if int(msg.wParam) == 1:
                        self.pressed.emit("f7")
                    elif int(msg.wParam) == 2:
                        self.pressed.emit("f8")
                else:
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                user32.UnregisterHotKey(hwnd, 1)
                user32.UnregisterHotKey(hwnd, 2)
            except Exception:
                pass
            try:
                user32.DestroyWindow(hwnd)
            except Exception:
                pass
