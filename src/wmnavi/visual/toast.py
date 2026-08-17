"""Short-lived on-screen profile name toast."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class ProfileToast(QWidget):
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
        self.label = QLabel(self)
        self.label.setStyleSheet(
            "QLabel { background: rgba(14, 10, 22, 210); color: #f3e8ff;"
            " border: 1px solid rgba(168, 85, 247, 0.55); border-radius: 10px;"
            " padding: 10px 16px; font-size: 15px; font-weight: 600; }"
        )
        self._fade = QTimer(self)
        self._fade.setSingleShot(True)
        self._fade.timeout.connect(self.hide)

    def show_message(self, text: str, ms: int = 1600):
        self.label.setText(text)
        self.label.adjustSize()
        self.resize(self.label.size())
        screen = self.screen()
        geo = screen.availableGeometry() if screen else None
        if geo is not None:
            self.move(geo.center().x() - self.width() // 2, geo.top() + 48)
        self.setWindowOpacity(0.96)
        self.show()
        self.raise_()
        self._fade.start(max(400, int(ms)))
