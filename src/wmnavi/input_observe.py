"""Passive keyboard + raw mouse observation. No injection, no blocking Tarkov."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Qt, QTimer, Signal

user32 = ctypes.windll.user32

VK_SHIFT = 0x10
VK_W = 0x57
VK_A = 0x41
VK_S = 0x53
VK_D = 0x44
VK_C = 0x43
VK_LCONTROL = 0xA2
VK_MAP = {
    "forward": VK_W,
    "back": VK_S,
    "left": VK_A,
    "right": VK_D,
    "sprint": VK_SHIFT,
    "crouch": VK_C,
}

RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIDEV_INPUTSINK = 0x00000100
WM_INPUT = 0x00FF
HWND_MESSAGE = -3

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetRawInputData.argtypes = [
    wintypes.HANDLE,
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint),
    ctypes.c_uint,
]
user32.GetRawInputData.restype = ctypes.c_uint


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_ushort),
        ("usUsage", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_ulong),
        ("dwSize", ctypes.c_ulong),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", ctypes.c_ushort),
        ("ulButtons", ctypes.c_ulong),
        ("ulRawButtons", ctypes.c_ulong),
        ("lLastX", ctypes.c_long),
        ("lLastY", ctypes.c_long),
        ("ulExtraInformation", ctypes.c_ulong),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), ctypes.c_uint, ctypes.c_uint]
user32.RegisterRawInputDevices.restype = wintypes.BOOL


class _RawInputFilter(QAbstractNativeEventFilter):
    def __init__(self, sink: "InputObserver"):
        super().__init__()
        self._sink = sink

    def nativeEventFilter(self, eventType, message):
        try:
            if bytes(eventType).decode("ascii", errors="ignore") not in {"windows_generic_MSG", "windows_dispatcher_MSG"}:
                return False, 0
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message != WM_INPUT:
                return False, 0
            size = ctypes.c_uint(ctypes.sizeof(RAWINPUT))
            buf = RAWINPUT()
            if user32.GetRawInputData(msg.lParam, RID_INPUT, ctypes.byref(buf), ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER)) == 0xFFFFFFFF:
                return False, 0
            if buf.header.dwType == RIM_TYPEMOUSE:
                self._sink._on_raw_mouse(int(buf.mouse.lLastX), int(buf.mouse.lLastY))
        except Exception:
            pass
        return False, 0


class InputObserver(QObject):
    """Factual WASD/modifiers + accumulated raw mouse counts since last drain."""

    sample = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dx = 0
        self._dy = 0
        self._filter: _RawInputFilter | None = None
        self._registered = False
        self._host = None
        self.bind = dict(VK_MAP)
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.sample)

    def set_binds(self, binds: dict[str, int] | None):
        if binds:
            self.bind.update({k: int(v) for k, v in binds.items() if k in VK_MAP})

    def start(self):
        self._install_raw()
        if not self.timer.isActive():
            self.timer.start()

    def stop(self):
        self.timer.stop()

    def keys(self) -> dict[str, bool]:
        def down(vk: int) -> bool:
            try:
                return bool(user32.GetAsyncKeyState(int(vk)) & 0x8000)
            except Exception:
                return False

        crouch_vk = int(self.bind.get("crouch", VK_C))
        return {
            "forward": down(self.bind.get("forward", VK_W)),
            "back": down(self.bind.get("back", VK_S)),
            "left": down(self.bind.get("left", VK_A)),
            "right": down(self.bind.get("right", VK_D)),
            "sprint": down(self.bind.get("sprint", VK_SHIFT)),
            "crouch": down(crouch_vk) or down(VK_LCONTROL),
        }

    def drain_mouse(self) -> tuple[int, int]:
        dx, dy = self._dx, self._dy
        self._dx = 0
        self._dy = 0
        return dx, dy

    def _on_raw_mouse(self, dx: int, dy: int):
        self._dx += int(dx)
        self._dy += int(dy)

    def _hwnd(self) -> int:
        parent = self.parent()
        if parent is not None and hasattr(parent, "winId"):
            try:
                value = int(parent.winId())
                if value:
                    return value
            except Exception:
                pass
        try:
            from PySide6.QtWidgets import QWidget

            if self._host is None:
                host = QWidget()
                host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
                host.setFixedSize(1, 1)
                host.move(-32000, -32000)
                host.show()
                host.hide()
                self._host = host
            return int(self._host.winId())
        except Exception:
            return 0

    def _install_raw(self):
        if self._registered:
            return
        app = QCoreApplication.instance()
        if app is None:
            return
        hwnd = self._hwnd()
        if hwnd == 0:
            return
        dev = RAWINPUTDEVICE()
        dev.usUsagePage = 0x01
        dev.usUsage = 0x02
        dev.dwFlags = RIDEV_INPUTSINK
        dev.hwndTarget = hwnd
        ok = user32.RegisterRawInputDevices(ctypes.byref(dev), 1, ctypes.sizeof(RAWINPUTDEVICE))
        if not ok:
            return
        self._filter = _RawInputFilter(self)
        app.installNativeEventFilter(self._filter)
        self._registered = True
