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


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ScreenToClient.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short


def cursor_screen_pos() -> tuple[int, int]:
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def cursor_in_eft_client() -> tuple[int, int, int, int, int] | None:
    """(hwnd, client_x, client_y, client_w, client_h) if the cursor is over Tarkov."""
    hwnd = find_eft_window()
    if not hwnd:
        return None
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    if not user32.ScreenToClient(hwnd, ctypes.byref(pt)):
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    w = int(rect.right - rect.left)
    h = int(rect.bottom - rect.top)
    x, y = int(pt.x), int(pt.y)
    if x < 0 or y < 0 or x >= w or y >= h:
        return None
    return int(hwnd), x, y, w, h


VK_SHIFT = 0x10
VK_W = 0x57
VK_A = 0x41
VK_S = 0x53
VK_D = 0x44
VK_C = 0x43
VK_LCONTROL = 0xA2


def movement_keys() -> dict[str, bool]:
    """True if the key is down. Used for loot-idle, audio self-step, and prediction."""
    def down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    return {
        "forward": down(VK_W),
        "back": down(VK_S),
        "left": down(VK_A),
        "right": down(VK_D),
        "sprint": down(VK_SHIFT),
        "crouch": down(VK_C) or down(VK_LCONTROL),
    }


def eft_is_foreground() -> bool:
    hwnd = find_eft_window()
    if not hwnd:
        return False
    return int(user32.GetForegroundWindow() or 0) == hwnd


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
