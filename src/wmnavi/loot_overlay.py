"""Top-right loot value overlay. Same click-through recipe as AudioIndicatorHud."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .compass import (
    GWL_EXSTYLE,
    WS_EX_LAYERED,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TRANSPARENT,
    _get_window_long,
    _set_window_long,
)
from .loot_recognize import LootMatch


class LootValueHud(QWidget):
    def __init__(self):
        flags = (
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.match: LootMatch | None = None
        self.debug = False
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self.setFixedSize(300, 88)
        self.hide()

    def show_match(self, match: LootMatch):
        self.match = match
        self._hide_timer.stop()
        self._place()
        if not self.isVisible():
            self.show()
            self._apply_click_through()
        self.raise_()
        self.update()

    def schedule_hide(self, ms: int = 450):
        self._hide_timer.start(max(80, int(ms)))

    def shutdown(self):
        self._hide_timer.stop()
        self.match = None
        self.hide()

    def _fade_out(self):
        self.match = None
        self.hide()

    def _place(self):
        screen = self.screen()
        if screen is None:
            from PySide6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w = 300 if not self.debug else 380
        h = 92 if not self.debug else 128
        self.setFixedSize(w, h)
        x = geo.x() + geo.width() - w - 16
        y = geo.y() + 10
        self.setGeometry(x, y, w, h)

    def _apply_click_through(self):
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        style = _get_window_long(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        _set_window_long(hwnd, GWL_EXSTYLE, style)

    def paintEvent(self, event):
        m = self.match
        if m is None:
            return
        status = m.status or "searching"
        if status == "found":
            title = m.short_name or m.name or m.item_id
            sub = f"₽{m.price:,}" if m.price else "₽ —"
            badge = "Item found"
            sub_color = QColor(250, 204, 21)
        elif status == "identifying":
            title = "Identifying…"
            sub = "Checking name and picture"
            badge = "Detecting"
            sub_color = QColor(196, 181, 253)
        elif status == "no_match":
            title = "No match"
            sub = "Hover until the name box appears"
            badge = "Detecting"
            sub_color = QColor(156, 163, 175)
        else:
            title = "Searching…"
            sub = "Hover an item"
            badge = "Detecting"
            sub_color = QColor(196, 181, 253)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.setBrush(QColor(10, 10, 16, 210))
        painter.setPen(QPen(QColor(168, 85, 247, 110), 1))
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 8, 8)
        painter.setPen(QColor(168, 85, 247, 220))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.drawText(14, 6, w - 28, 16, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, badge)
        painter.setPen(QColor(243, 232, 255))
        painter.setFont(QFont("Segoe UI", 12, QFont.Weight.DemiBold))
        painter.drawText(14, 24, w - 28, 26, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
        painter.setPen(sub_color)
        painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        painter.drawText(14, 50, w - 28, 26, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub)
        if self.debug:
            painter.setPen(QColor(167, 243, 208, 210))
            painter.setFont(QFont("Segoe UI", 7))
            painter.drawText(
                14,
                76,
                w - 28,
                44,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                f"{m.confidence * 100:.1f}%  ham {m.hamming}  {m.latency_ms:.0f}ms  {m.reason}"
                + (f"  ocr {m.ocr_text}" if m.ocr_text else ""),
            )
        painter.end()
