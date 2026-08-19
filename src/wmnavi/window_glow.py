"""Eerie pulsing glow just outside the main window edges."""

from __future__ import annotations

import math
import os
import sys

from PySide6.QtCore import QEvent, QObject, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .theme import ACCENT, GLOW

PAD = 30

_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000


def glow_supported() -> bool:
    platform = os.environ.get("QT_QPA_PLATFORM", "").lower()
    if platform in {"offscreen", "minimal"}:
        return False
    return True


def _hwnd(widget: QWidget) -> int:
    return int(widget.winId())


def _place_behind(halo: QWidget, host: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = _hwnd(halo)
        host_hwnd = _hwnd(host)
        ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            _GWL_EXSTYLE,
            ex | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
        )
        user32.SetWindowPos(
            hwnd,
            host_hwnd,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
        )
    except Exception:
        pass


class WindowGlow(QWidget):
    def __init__(self, host: QWidget):
        super().__init__(None)
        self._host = host
        self._enabled = True
        self._t = 0.0
        self.setWindowTitle("WMNavigation glow")
        self._apply_flags()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._follow)
        self._follow_timer.start(250)
        self._filter = _HostFilter(self)
        host.installEventFilter(self._filter)
        self._follow()

    def _apply_flags(self):
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        if self._host.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_enabled(self, on: bool):
        self._enabled = bool(on)
        if not on:
            self.hide()
            return
        self._follow()

    def attach(self):
        want_top = bool(self._host.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        have_top = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if want_top != have_top:
            self._apply_flags()
        self._follow()

    def shutdown(self):
        try:
            self._host.removeEventFilter(self._filter)
        except Exception:
            pass
        self._timer.stop()
        self._follow_timer.stop()
        self.hide()
        self.close()

    def _tick(self):
        self._t += 0.055
        if self._enabled and self.isVisible():
            self.update()

    def _should_show(self) -> bool:
        if not self._enabled:
            return False
        host = self._host
        if not host.isVisible() or host.isMinimized():
            return False
        if host.isMaximized() or host.windowState() & Qt.WindowState.WindowFullScreen:
            return False
        return True

    def _follow(self):
        if not self._should_show():
            if self.isVisible():
                self.hide()
            return
        frame = self._host.frameGeometry()
        geo = QRect(
            frame.x() - PAD,
            frame.y() - PAD,
            frame.width() + PAD * 2,
            frame.height() + PAD * 2,
        )
        if self.geometry() != geo:
            self.setGeometry(geo)
        if not self.isVisible():
            self.show()
        _place_behind(self, self._host)

    def paintEvent(self, event):
        if not self._enabled:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # Slow breath with a sickly second harmonic so it never feels like a clean loop.
        breath = 0.5 + 0.5 * math.sin(self._t)
        shiver = 0.5 + 0.5 * math.sin(self._t * 0.37 + 1.2)
        strength = 0.18 + 0.55 * breath * (0.7 + 0.3 * shiver)

        inner = self.rect().adjusted(PAD, PAD, -PAD, -PAD)
        layers = 16
        for i in range(layers, 0, -1):
            dist = i * (PAD / layers)
            rect = inner.adjusted(-dist, -dist, dist, dist)
            fade = (1.0 - i / layers) ** 1.55
            alpha = int(strength * 95 * fade)
            if i > layers * 0.55:
                color = QColor(GLOW)
            else:
                color = QColor(ACCENT)
            color.setAlpha(max(0, min(255, alpha)))
            pen = QPen(color)
            pen.setWidthF(2.2 if i < 4 else 3.4)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 12 + dist * 0.15, 12 + dist * 0.15)


class _HostFilter(QObject):
    def __init__(self, glow: WindowGlow):
        super().__init__(glow)
        self._glow = glow

    def eventFilter(self, watched, event):
        et = event.type()
        if et in {
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.WindowStateChange,
            QEvent.Type.ZOrderChange,
            QEvent.Type.Close,
        }:
            if et == QEvent.Type.Close:
                self._glow.hide()
            else:
                self._glow._follow()
        return False
