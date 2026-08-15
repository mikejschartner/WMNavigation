"""Top-of-screen gunshot direction HUD. Independent overlay — does not touch CompassHud."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygon
from PySide6.QtWidgets import QWidget

from .audio_detect import DetectDebug, ShotEventManager, SHOT_LIFETIME_S
from .compass import _get_window_long, _set_window_long, GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT
from .heading import HeadingTracker

FOV_DEG = 180.0
HUD_H = 36


class AudioIndicatorHud(QWidget):
    def __init__(self, heading: HeadingTracker, manager: ShotEventManager):
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
        self.manager = manager
        self.compass_visible = False
        self.debug = False
        self.debug_snapshot = DetectDebug()
        self.debug_fn = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._on_tick)
        self.setFixedHeight(HUD_H)
        self.hide()

    def set_compass_visible(self, on: bool):
        self.compass_visible = bool(on)
        if self.isVisible():
            self._place()

    def note_shot(self):
        self._place()
        if not self.isVisible():
            self.show()
            self.raise_()
            self._apply_click_through()
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def shutdown(self):
        self._timer.stop()
        self.hide()

    def _on_tick(self):
        self.manager.prune()
        if self.debug and callable(self.debug_fn):
            try:
                self.debug_snapshot = self.debug_fn()
            except Exception:
                pass
        if not self.manager.events and not self.debug:
            self._timer.stop()
            self.hide()
            return
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
        width = min(900, max(420, int(geo.width() * 0.58)))
        x = geo.x() + (geo.width() - width) // 2
        y = geo.y() + (54 if self.compass_visible else 8)
        self.setGeometry(x, y, width, HUD_H)

    def _apply_click_through(self):
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        style = _get_window_long(hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        _set_window_long(hwnd, GWL_EXSTYLE, style)

    def _view_yaw(self) -> float | None:
        if self.compass_visible and self.heading.has_heading:
            return self.heading.game_yaw
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        pad = 22
        usable = max(1.0, w - pad * 2)
        half_fov = FOV_DEG / 2.0
        now = time.perf_counter()

        bg = QColor(10, 10, 16, 118)
        painter.setBrush(bg)
        painter.setPen(QPen(QColor(168, 85, 247, 55), 1))
        painter.drawRoundedRect(0, 0, w - 1, h - 1, 7, 7)

        cx = w / 2.0

        def x_for(rel: float) -> float:
            return cx + (max(-half_fov, min(half_fov, rel)) / half_fov) * (usable / 2.0)

        painter.setPen(QPen(QColor(156, 163, 175, 90), 1))
        painter.drawLine(pad, h - 8, w - pad, h - 8)
        painter.setPen(QPen(QColor(168, 85, 247, 160), 1))
        painter.drawLine(int(cx), 10, int(cx), h - 6)

        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        painter.setPen(QColor(196, 181, 253, 190))
        painter.drawText(pad - 6, 2, 40, 14, Qt.AlignmentFlag.AlignLeft, "L")
        painter.drawText(int(cx - 16), 2, 32, 14, Qt.AlignmentFlag.AlignCenter, "0°")
        painter.drawText(w - pad - 28, 2, 40, 14, Qt.AlignmentFlag.AlignRight, "R")

        yaw = self._view_yaw()
        for ev in self.manager.events:
            age = now - ev.last_t
            fade = max(0.0, 1.0 - age / max(0.2, self.manager.lifetime_s or SHOT_LIFETIME_S))
            rel = self.manager.display_rel(ev, yaw)
            x = x_for(rel)
            alpha = int(40 + 200 * fade * min(1.0, 0.45 + ev.dir_conf))
            color = QColor(251, 146, 60, alpha)
            if abs(rel) < 18:
                color = QColor(250, 204, 21, alpha)
            painter.setBrush(color)
            painter.setPen(QPen(QColor(10, 10, 16, min(200, alpha)), 1))
            tri = QPolygon(
                [
                    QPoint(int(x), h - 9),
                    QPoint(int(x - 7), 14),
                    QPoint(int(x + 7), 14),
                ]
            )
            painter.drawPolygon(tri)

        if self.debug:
            d = self.debug_snapshot
            painter.setPen(QColor(167, 243, 208, 210))
            painter.setFont(QFont("Segoe UI", 7))
            painter.drawText(
                8,
                h - 22,
                w - 16,
                14,
                Qt.AlignmentFlag.AlignLeft,
                f"L {d.rms_l:.3f}  R {d.rms_r:.3f}  p {d.gunshot_prob:.2f}  {d.rel_deg:+.0f}° c {d.dir_conf:.2f}",
            )

        painter.end()
