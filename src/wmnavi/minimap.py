"""Always-on-top click-through mini map overlay (F7 / F8)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from .map_view import MapView

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

OPACITY_TIERS = (0.30, 0.60, 0.90)


def _set_window_long(hwnd: int, index: int, value: int) -> int:
    if hasattr(user32, "SetWindowLongPtrW"):
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
        user32.SetWindowLongPtrW.restype = ctypes.c_longlong
        return int(user32.SetWindowLongPtrW(hwnd, index, value))
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowLongW.restype = ctypes.c_long
    return int(user32.SetWindowLongW(hwnd, index, value))


def _get_window_long(hwnd: int, index: int) -> int:
    if hasattr(user32, "GetWindowLongPtrW"):
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_longlong
        return int(user32.GetWindowLongPtrW(hwnd, index))
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    return int(user32.GetWindowLongW(hwnd, index))


class MiniMapWindow(QWidget):
    """Small top-left overlay hosting a MapView. Click-through so Tarkov keeps mouse."""

    def __init__(self, size_px: int = 300):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._opacity_index = 1  # mid tier by default
        self._size_px = max(160, min(640, int(size_px)))

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 2, 2)
        root.setSpacing(0)

        frame = QFrame()
        frame.setObjectName("minimapFrame")
        frame.setStyleSheet(
            "#minimapFrame {"
            "  background: #0a0a0f;"
            "  border: 2px solid #3a3a48;"
            "}"
        )
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)

        self.map_view = MapView()
        self.map_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.map_view.setInteractive(False)
        self.map_view.setDragMode(self.map_view.DragMode.NoDrag)
        self.map_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.map_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.map_view.setStyleSheet("background: #0a0a0f; border: none;")
        # Smaller markers so the box stays readable.
        self.map_view.set_marker_scale(0.7)
        frame_layout.addWidget(self.map_view)
        root.addWidget(frame)

        self._apply_size()
        self._place_top_left()
        self.setWindowOpacity(OPACITY_TIERS[self._opacity_index])
        self.hide()

    def _apply_size(self):
        self.setFixedSize(self._size_px, self._size_px)

    def set_size_px(self, size_px: int):
        self._size_px = max(160, min(640, int(size_px)))
        self._apply_size()
        if self.isVisible():
            self._place_top_left()
            self._apply_click_through()

    def _place_top_left(self):
        margin = 12
        self.move(margin, margin)

    def opacity_tier(self) -> float:
        return OPACITY_TIERS[self._opacity_index]

    def cycle_opacity(self):
        self._opacity_index = (self._opacity_index + 1) % len(OPACITY_TIERS)
        self.setWindowOpacity(OPACITY_TIERS[self._opacity_index])

    def toggle(self) -> bool:
        """Show or hide. Returns True if now visible."""
        if self.isVisible():
            self.hide()
            return False
        self._place_top_left()
        self.setWindowOpacity(OPACITY_TIERS[self._opacity_index])
        self.show()
        self.raise_()
        self._apply_click_through()
        return True

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_click_through()

    def _apply_click_through(self):
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        style = _get_window_long(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        _set_window_long(hwnd, GWL_EXSTYLE, style)
