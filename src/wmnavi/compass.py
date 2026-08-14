"""Horizontal FPS-style compass overlay (F9). Click-through, independent of tracking."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QWidget

from .heading import HeadingTracker, shortest_delta, map_bearing

user32 = ctypes.windll.user32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

CARDINALS = {
    0: "N",
    45: "NE",
    90: "E",
    135: "SE",
    180: "S",
    225: "SW",
    270: "W",
    315: "NW",
}

FOV_DEG = 180.0


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


class CompassHud(QWidget):
    """Thin top-of-screen compass. Rendering only — no screenshot or route work."""

    def __init__(self, heading: HeadingTracker):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.heading = heading
        self._friends: list = []
        self._player_xz: tuple[float, float] | None = None
        self._map_rotation = 0
        self._map_transform: list[float] | None = None
        self._map_slug = ""

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~62 FPS; independent of screenshot cadence
        self._timer.timeout.connect(self._on_tick)

        self.setFixedHeight(44)
        self.hide()

    def set_world_context(self, map_rotation: int, transform: list[float] | None, map_slug: str):
        self._map_rotation = int(map_rotation or 0)
        self._map_transform = transform
        self._map_slug = map_slug or ""

    def set_player_xz(self, x: float, z: float):
        self._player_xz = (float(x), float(z))

    def set_friends(self, pings: list, map_slug: str):
        slug = map_slug or self._map_slug
        self._friends = [p for p in (pings or []) if getattr(p, "map_slug", "") == slug]

    def toggle(self) -> bool:
        if self.isVisible():
            self._timer.stop()
            self.hide()
            return False
        self._place()
        self.show()
        self.raise_()
        self._apply_click_through()
        if not self._timer.isActive():
            self._timer.start()
        self.update()
        return True

    def shutdown(self):
        self._timer.stop()
        self.hide()

    def _on_tick(self):
        if not self.isVisible():
            return
        self.heading.tick()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._place()
        self._apply_click_through()

    def _place(self):
        screen = self.screen()
        if screen is None:
            from PySide6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        width = min(1100, max(520, int(geo.width() * 0.72)))
        x = geo.x() + (geo.width() - width) // 2
        y = geo.y() + 8
        self.setGeometry(x, y, width, 44)

    def _apply_click_through(self):
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        style = _get_window_long(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        _set_window_long(hwnd, GWL_EXSTYLE, style)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad = 18
        usable = max(1.0, w - pad * 2)
        half_fov = FOV_DEG / 2.0
        heading = self.heading.heading if self.heading.has_heading else 0.0

        bg = QColor(10, 10, 16, 150)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(168, 85, 247, 70), 1))
        painter.drawRoundedRect(0, 0, w - 1, h - 1, 8, 8)

        cx = w / 2.0
        tick_base = h - 7

        def x_for(rel: float) -> float:
            return cx + (rel / half_fov) * (usable / 2.0)

        font_card = QFont("Segoe UI", 10)
        font_card.setBold(True)
        font_num = QFont("Segoe UI", 8)

        for angle in range(0, 360, 5):
            rel = shortest_delta(heading, float(angle))
            if abs(rel) > half_fov + 1.5:
                continue
            x = x_for(rel)
            if x < 6 or x > w - 6:
                continue
            if angle % 45 == 0:
                painter.setPen(QPen(QColor(243, 232, 255, 230), 2))
                painter.drawLine(int(x), tick_base - 14, int(x), tick_base)
                painter.setFont(font_card)
                painter.setPen(QColor(243, 232, 255))
                label = CARDINALS.get(angle, str(angle))
                painter.drawText(int(x - 18), 4, 36, 18, Qt.AlignmentFlag.AlignCenter, label)
            elif angle % 15 == 0:
                painter.setPen(QPen(QColor(196, 181, 253, 180), 1))
                painter.drawLine(int(x), tick_base - 10, int(x), tick_base)
                painter.setFont(font_num)
                painter.setPen(QColor(196, 181, 253, 210))
                painter.drawText(int(x - 16), 6, 32, 16, Qt.AlignmentFlag.AlignCenter, str(angle))
            else:
                painter.setPen(QPen(QColor(156, 163, 175, 120), 1))
                painter.drawLine(int(x), tick_base - 5, int(x), tick_base)

        if self._player_xz and self._friends:
            px, pz = self._player_xz
            for ping in self._friends:
                try:
                    bearing = map_bearing(
                        px,
                        pz,
                        float(ping.x),
                        float(ping.z),
                        self._map_rotation,
                        self._map_transform,
                    )
                except Exception:
                    continue
                rel = shortest_delta(heading, bearing)
                if abs(rel) > half_fov:
                    continue
                x = x_for(rel)
                if x < 10 or x > w - 10:
                    continue
                color = QColor(getattr(ping, "color", None) or "#38bdf8")
                if not color.isValid():
                    color = QColor("#38bdf8")
                painter.setBrush(color)
                painter.setPen(QPen(QColor("#0a0a0f"), 1))
                painter.drawEllipse(int(x - 5), 20, 10, 10)

        # Fixed center pointer — current facing.
        painter.setBrush(QColor(168, 85, 247))
        painter.setPen(QPen(QColor("#f3e8ff"), 1))
        pointer = QPolygon(
            [
                QPoint(int(cx), 2),
                QPoint(int(cx - 6), 12),
                QPoint(int(cx + 6), 12),
            ]
        )
        painter.drawPolygon(pointer)
        painter.setPen(QPen(QColor(243, 232, 255, 220), 1))
        painter.drawLine(int(cx), 12, int(cx), h - 6)

        painter.end()
