"""Logo splash shown while the main window loads."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from . import __version__
from .brand import icon_png_path
from .theme import ACCENT, GLOW


class SplashScreen(QWidget):
    def __init__(self, min_ms: int = 1400):
        super().__init__(None)
        self._min_ms = min_ms
        self._shown_at = time.monotonic()
        self._pulse = 0.0
        self.setObjectName("splashRoot")
        self.setWindowTitle("WMNavigation")
        self.setFixedSize(460, 420)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.SplashScreen
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 32, 36, 28)
        layout.setSpacing(10)

        self.logo = QLabel()
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap(str(icon_png_path()))
        if not pix.isNull():
            self.logo.setPixmap(
                pix.scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        layout.addWidget(self.logo, 1)

        title = QLabel("WMNAVIGATION")
        title.setObjectName("splashTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self.status = QLabel("Loading map…")
        self.status.setObjectName("splashStatus")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status)

        ver = QLabel(f"v{__version__}")
        ver.setObjectName("splashStatus")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(ver)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pulse = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self._pulse))
        glow = QColor(GLOW)
        glow.setAlpha(int(40 + 70 * pulse))
        pen = QPen(glow)
        pen.setWidthF(2.4)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self.rect().adjusted(6, 6, -6, -6), 16, 16)
        accent = QColor(ACCENT)
        accent.setAlpha(int(18 + 36 * pulse))
        pen.setColor(accent)
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(10, 10, -10, -10), 14, 14)

    def _tick(self):
        self._pulse += 0.09
        self.update()

    def set_status(self, text: str):
        self.status.setText(text)
        QApplication.processEvents()

    def show_centered(self):
        self.show()
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )
        self.raise_()
        QApplication.processEvents()
        self._shown_at = time.monotonic()

    def finish(self, window: QWidget | None = None):
        remaining = self._min_ms / 1000.0 - (time.monotonic() - self._shown_at)
        if remaining > 0:
            deadline = time.monotonic() + remaining
            while time.monotonic() < deadline:
                QApplication.processEvents()
                time.sleep(0.02)
        self._timer.stop()
        if window is not None:
            window.show()
            window.raise_()
            window.activateWindow()
        self.close()
        QApplication.processEvents()

    def closeEvent(self, event):
        self._timer.stop()
        super().closeEvent(event)
