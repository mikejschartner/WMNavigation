"""Connected display enumeration for targeting one monitor."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtGui import QGuiApplication

user32 = ctypes.windll.user32
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
user32.GetMonitorInfoW.restype = wintypes.BOOL
CCHDEVICENAME = 32


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * CCHDEVICENAME),
    ]


MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    ctypes.POINTER(wintypes.RECT),
    wintypes.LPARAM,
)

MONITORINFOF_PRIMARY = 0x00000001


@dataclass
class MonitorInfo:
    key: str
    label: str
    device: str
    hmonitor: int
    x: int
    y: int
    width: int
    height: int
    primary: bool


def _monitor_device(hmonitor: int) -> tuple[str, wintypes.RECT, bool]:
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(MONITORINFOEXW)
    if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
        return "", info.rcMonitor, False
    return str(info.szDevice), info.rcMonitor, bool(info.dwFlags & MONITORINFOF_PRIMARY)


def list_monitors() -> list[MonitorInfo]:
    found: list[MonitorInfo] = []

    try:
        def _cb(hmon, _hdc, _lprect, _data):
            device, rect, primary = _monitor_device(int(hmon))
            width = int(rect.right - rect.left)
            height = int(rect.bottom - rect.top)
            if width < 64 or height < 64:
                return True
            index = len(found) + 1
            name = device or f"DISPLAY{index}"
            tag = "primary" if primary else f"{width}x{height}"
            found.append(
                MonitorInfo(
                    key=name,
                    label=f"Monitor {index} ({tag})",
                    device=name,
                    hmonitor=int(hmon),
                    x=int(rect.left),
                    y=int(rect.top),
                    width=width,
                    height=height,
                    primary=primary,
                )
            )
            return True

        proc = MonitorEnumProc(_cb)
        user32.EnumDisplayMonitors(None, None, proc, 0)
    except Exception:
        found = []
    if found:
        return found

    screens = QGuiApplication.screens() or []
    primary = QGuiApplication.primaryScreen()
    out: list[MonitorInfo] = []
    for i, screen in enumerate(screens, start=1):
        geo = screen.geometry()
        is_primary = screen is primary
        out.append(
            MonitorInfo(
                key=screen.name() or f"screen-{i}",
                label=f"Monitor {i} ({'primary' if is_primary else f'{geo.width()}x{geo.height()}'})",
                device=str(screen.name() or ""),
                hmonitor=0,
                x=int(geo.x()),
                y=int(geo.y()),
                width=int(geo.width()),
                height=int(geo.height()),
                primary=is_primary,
            )
        )
    return out


def monitor_by_key(key: str) -> MonitorInfo | None:
    monitors = list_monitors()
    for item in monitors:
        if item.key == key:
            return item
    for item in monitors:
        if item.primary:
            return item
    return monitors[0] if monitors else None


def default_monitor_key() -> str:
    for item in list_monitors():
        if item.primary:
            return item.key
    monitors = list_monitors()
    return monitors[0].key if monitors else ""
