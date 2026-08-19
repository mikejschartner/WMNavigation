"""Global hotkey detection via key-state polling (no Win32 hotkey filter)."""

from __future__ import annotations

import ctypes

from PySide6.QtCore import QObject, QTimer, Signal

user32 = ctypes.windll.user32

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
VK_F1 = 0x70
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A

NAME_TO_VK = {
    "lbutton": VK_LBUTTON,
    "rbutton": VK_RBUTTON,
    "mbutton": VK_MBUTTON,
    "xbutton1": VK_XBUTTON1,
    "xbutton2": VK_XBUTTON2,
    "f1": VK_F1,
    "f6": VK_F6,
    "f7": VK_F7,
    "f8": VK_F8,
    "f9": VK_F9,
    "f10": VK_F10,
    "f11": VK_F11,
}
for code in range(0x41, 0x5B):
    NAME_TO_VK[chr(code).lower()] = code
for i in range(1, 13):
    NAME_TO_VK[f"f{i}"] = 0x6F + i

VK_TO_NAME = {vk: name for name, vk in NAME_TO_VK.items()}

_KEYS = (
    (VK_F6, "f6"),
    (VK_F7, "f7"),
    (VK_F8, "f8"),
    (VK_F9, "f9"),
)


def vk_label(name: str) -> str:
    labels = {
        "mbutton": "Middle Mouse",
        "xbutton1": "Mouse 4",
        "xbutton2": "Mouse 5",
        "lbutton": "Left Mouse",
        "rbutton": "Right Mouse",
    }
    if name in labels:
        return labels[name]
    if name.startswith("f") and name[1:].isdigit():
        return name.upper()
    return (name or "").upper()


class GlobalHotkeys(QObject):
    """Poll keys so they work while Tarkov is focused, without RegisterHotKey."""

    pressed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)
        self._watch: list[tuple[int, str]] = list(_KEYS)
        self._prev: dict[int, bool] = {vk: False for vk, _name in self._watch}

    def set_extra(self, names: list[str]):
        watch = list(_KEYS)
        seen = {vk for vk, _n in watch}
        for name in names:
            vk = NAME_TO_VK.get((name or "").lower())
            if vk and vk not in seen:
                watch.append((vk, name.lower()))
                seen.add(vk)
        self._watch = watch
        self._prev = {vk: self._prev.get(vk, False) for vk, _n in watch}

    def start(self, _hwnd: int = 0) -> bool:
        if not self._timer.isActive():
            self._prev = {vk: False for vk, _name in self._watch}
            self._timer.start()
        return True

    def stop(self):
        self._timer.stop()

    def _down(self, vk: int) -> bool:
        try:
            return bool(user32.GetAsyncKeyState(vk) & 0x8000)
        except Exception:
            return False

    def _tick(self):
        try:
            for vk, name in self._watch:
                now = self._down(vk)
                was = self._prev.get(vk, False)
                self._prev[vk] = now
                if now and not was:
                    self.pressed.emit(name)
        except Exception:
            pass
