"""Black-heavy purple glass theme."""

BG = "#050508"
PANEL = "rgba(8, 8, 12, 0.96)"
BORDER = "rgba(168, 85, 247, 0.22)"
ACCENT = "#a855f7"
ACCENT_HOVER = "#c084fc"
TEXT = "#f5f3ff"
MUTED = "#8b8b98"
SUCCESS = "#22c55e"
GLOW = "#7c3aed"

STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: Segoe UI, Arial, sans-serif;
    font-size: 13px;
}}
QMainWindow {{
    background-color: {BG};
}}
QFrame#sidebar {{
    background-color: rgba(5, 5, 8, 0.98);
    border-right: 1px solid {BORDER};
}}
QPushButton {{
    background-color: rgba(255, 255, 255, 0.03);
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 14px;
    color: {TEXT};
}}
QPushButton:hover {{
    background-color: rgba(168, 85, 247, 0.16);
    border-color: {ACCENT_HOVER};
}}
QPushButton:pressed {{
    background-color: rgba(168, 85, 247, 0.28);
}}
QPushButton:checked {{
    background-color: rgba(168, 85, 247, 0.32);
    border-color: {ACCENT};
}}
QPushButton:checked:hover {{
    background-color: rgba(168, 85, 247, 0.42);
}}
QComboBox {{
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background-color: #0a0a0e;
    border: 1px solid {BORDER};
    selection-background-color: rgba(168, 85, 247, 0.28);
    selection-color: {TEXT};
    color: {TEXT};
    outline: none;
}}
QSpinBox {{
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px 8px;
    color: {TEXT};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}
QCheckBox {{
    spacing: 8px;
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: rgba(255,255,255,0.03);
}}
QCheckBox::indicator:checked {{
    background: rgba(168, 85, 247, 0.62);
    border-color: {ACCENT};
}}
QLabel#title {{
    font-size: 18px;
    font-weight: 600;
    color: {TEXT};
}}
QLabel#edition {{
    color: {MUTED};
    font-size: 12px;
    margin-top: -2px;
    margin-bottom: 2px;
}}
QLabel#status {{
    color: {MUTED};
    font-size: 12px;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: rgba(255,255,255,0.06);
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #5b21b6, stop:1 {ACCENT});
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
    border: 1px solid {ACCENT_HOVER};
    background: qradialgradient(cx:0.32, cy:0.28, radius:0.85, fx:0.3, fy:0.28,
        stop:0 #f3e8ff, stop:0.28 {ACCENT_HOVER}, stop:1 #6d28d9);
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: rgba(255,255,255,0.04);
    text-align: center;
    min-height: 18px;
    color: {TEXT};
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #6d28d9, stop:1 {ACCENT});
    border-radius: 7px;
}}
QDialog#updateDialog {{
    background-color: {BG};
}}
QDialog {{
    background-color: {BG};
    color: {TEXT};
}}
QToolButton {{
    background-color: rgba(255,255,255,0.03);
    border: 1px solid rgba(168, 85, 247, 0.16);
    border-radius: 8px;
}}
QToolButton:checked {{
    border: 2px solid {ACCENT};
    background-color: rgba(168, 85, 247, 0.25);
}}
QScrollArea {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: rgba(0,0,0,0.28);
    min-height: 120px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: rgba(168, 85, 247, 0.28);
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: rgba(168, 85, 247, 0.28);
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QLabel#sectionTitle {{
    color: {MUTED};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 4px 0 2px 0;
}}
QFrame#mapOverlay {{
    background-color: rgba(5, 5, 8, 0.92);
    border-bottom: 1px solid {BORDER};
}}
QFrame#layerSection {{
    background: transparent;
}}
QFrame#sidebarBottom {{
    background: transparent;
}}
QPushButton#sectionToggle {{
    text-align: left;
    padding: 6px 10px;
    background-color: rgba(168, 85, 247, 0.08);
    border: 1px solid rgba(168, 85, 247, 0.20);
    border-radius: 8px;
    font-weight: 700;
    letter-spacing: 0.04em;
}}
QPushButton#sectionToggle:checked {{
    background-color: rgba(168, 85, 247, 0.14);
}}
QPushButton#settingsGear {{
    padding: 6px 8px;
    font-size: 16px;
    min-width: 36px;
}}
QFrame#sidebar QScrollArea#sidebarScroll {{
    border: none;
    min-height: 0;
    background: transparent;
}}
QStackedWidget {{
    background-color: {BG};
}}
QLineEdit {{
    background-color: rgba(255, 255, 255, 0.04);
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {ACCENT};
}}
QToolTip {{
    background-color: #0a0a0e;
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 6px 8px;
}}
QWidget#splashRoot {{
    background-color: {BG};
}}
QLabel#splashTitle {{
    font-size: 22px;
    font-weight: 600;
    color: {TEXT};
    letter-spacing: 0.08em;
}}
QLabel#splashStatus {{
    color: {MUTED};
    font-size: 12px;
}}
"""
