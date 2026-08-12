"""Black / purple glass theme constants."""

BG = "#0a0a0f"
PANEL = "rgba(18, 12, 28, 0.92)"
BORDER = "rgba(168, 85, 247, 0.35)"
ACCENT = "#a855f7"
ACCENT_HOVER = "#c084fc"
TEXT = "#f3e8ff"
MUTED = "#9ca3af"
SUCCESS = "#22c55e"

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
    background-color: rgba(14, 10, 22, 0.95);
    border-right: 1px solid {BORDER};
}}
QPushButton {{
    background-color: rgba(168, 85, 247, 0.15);
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 14px;
    color: {TEXT};
}}
QPushButton:hover {{
    background-color: rgba(168, 85, 247, 0.28);
    border-color: {ACCENT_HOVER};
}}
QPushButton:pressed {{
    background-color: rgba(168, 85, 247, 0.4);
}}
QComboBox {{
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    color: {TEXT};
}}
QComboBox::drop-down {{
    border: none;
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
    background: rgba(255,255,255,0.04);
}}
QCheckBox::indicator:checked {{
    background: rgba(168, 85, 247, 0.55);
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
    background: rgba(255,255,255,0.08);
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    margin: -5px 0;
    background: {ACCENT};
    border-radius: 7px;
}}
QDialog {{
    background-color: {BG};
    color: {TEXT};
}}
QToolButton {{
    background-color: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 8px;
}}
QToolButton:checked {{
    border: 2px solid {ACCENT};
    background-color: rgba(168, 85, 247, 0.25);
}}
QScrollArea {{
    border: 1px solid {BORDER};
    border-radius: 8px;
    background: rgba(0,0,0,0.2);
    min-height: 120px;
}}
QLabel#sectionTitle {{
    color: {MUTED};
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.06em;
    padding: 4px 0 2px 0;
}}
QFrame#mapOverlay {{
    background-color: rgba(14, 10, 22, 0.88);
    border-bottom: 1px solid {BORDER};
}}
QFrame#layerSection {{
    background: transparent;
}}
QFrame#sidebarBottom {{
    background: transparent;
}}
"""
