"""Send Tarkov in-raid screenshot key (V) to the EFT window."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

VK_V = 0x56
SCAN_V = 0x2F
KEYEVENTF_KEYUP = 0x0002
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

EFT_TITLE_HINTS = (
    "escapefromtarkov",
    "escape from tarkov",
)


def _enum_windows() -> list[int]:
    hwnds: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            hwnds.append(int(hwnd))
        return True

    user32.EnumWindows(_cb, 0)
    return hwnds


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value or ""


def find_eft_window() -> int | None:
    for hwnd in _enum_windows():
        title = _window_title(hwnd).lower()
        if any(hint in title for hint in EFT_TITLE_HINTS):
            return hwnd
    return None


def press_v_in_raid() -> bool:
    """Press V for Tarkov. SendInput-style when EFT is focused; else post to its HWND."""
    hwnd = find_eft_window()
    if not hwnd:
        return False
    foreground = int(user32.GetForegroundWindow() or 0)
    if foreground == hwnd:
        user32.keybd_event(VK_V, SCAN_V, 0, 0)
        user32.keybd_event(VK_V, SCAN_V, KEYEVENTF_KEYUP, 0)
        return True
    # Overlay may be focused — don't steal Tarkov. Post key to the game window.
    lparam_down = 1 | (SCAN_V << 16)
    lparam_up = 1 | (SCAN_V << 16) | (1 << 30) | (1 << 31)
    user32.PostMessageW(hwnd, WM_KEYDOWN, VK_V, lparam_down)
    user32.PostMessageW(hwnd, WM_KEYUP, VK_V, lparam_up)
    return True
