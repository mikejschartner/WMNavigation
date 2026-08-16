"""In-memory capture of the Escape from Tarkov window. Never writes screenshots."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .win_input import find_eft_window

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
PW_RENDERFULLCONTENT = 0x00000002
DIB_RGB_COLORS = 0
BI_RGB = 0

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC, wintypes.HGDIOBJ, wintypes.UINT, wintypes.UINT,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def capture_eft_bgr(max_width: int = 320):
    """Return a downscaled BGR uint8 array, or None if Tarkov is not capturable."""
    try:
        import numpy as np
    except Exception:
        return None
    hwnd = find_eft_window()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width < 64 or height < 64:
        return None

    hdc = user32.GetDC(hwnd)
    if not hdc:
        return None
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(mem, bmp)
    ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    if not ok:
        ok = gdi32.BitBlt(mem, 0, 0, width, height, hdc, 0, 0, SRCCOPY)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    buf = ctypes.create_string_buffer(width * height * 4)
    got = 0
    if ok:
        got = gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(bmi), DIB_RGB_COLORS)

    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not got:
        return None

    bgra = np.frombuffer(buf, dtype=np.uint8).reshape((height, width, 4))
    bgr = bgra[:, :, :3].copy()
    if np.mean(bgr) < 4.0:
        return None
    if width > max_width:
        try:
            import cv2

            scale = max_width / float(width)
            bgr = cv2.resize(bgr, (max_width, max(32, int(height * scale))), interpolation=cv2.INTER_AREA)
        except Exception:
            step = max(1, width // max_width)
            bgr = bgr[::step, ::step]
    return bgr


def capture_eft_patch_bgr(client_x: int, client_y: int, size: int = 192):
    """Native-resolution crop around a Tarkov client point. Hover-only, not a second loop.

    MotionTracker downscales the whole window to ~360px — too small for item icons.
    This BitBlts a small patch at 1:1 only when the cursor has paused.
    """
    try:
        import numpy as np
    except Exception:
        return None
    hwnd = find_eft_window()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    cw = int(rect.right - rect.left)
    ch = int(rect.bottom - rect.top)
    size = max(64, int(size))
    x0 = max(0, min(cw - 1, int(client_x) - size // 2))
    y0 = max(0, min(ch - 1, int(client_y) - size // 2))
    w = min(size, cw - x0)
    h = min(size, ch - y0)
    if w < 32 or h < 32:
        return None

    hdc = user32.GetDC(hwnd)
    if not hdc:
        return None
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    ok = gdi32.BitBlt(mem, 0, 0, w, h, hdc, x0, y0, SRCCOPY)
    if not ok:
        ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    got = 0
    if ok:
        got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)

    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not got:
        return None
    bgra = np.frombuffer(buf, dtype=np.uint8).reshape((h, w, 4))
    bgr = bgra[:, :, :3].copy()
    if float(np.mean(bgr)) < 4.0:
        return None
    return bgr
