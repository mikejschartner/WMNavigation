"""Per-raid available-extract checkboxes (hard constraint for route planners)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QVBoxLayout, QWidget

from .models import MapPoint


def unique_extracts(pmc: list[MapPoint], scav: list[MapPoint], coop: list[MapPoint]) -> list[MapPoint]:
    """PMC first, then coop, then scav-only names."""
    seen: set[str] = set()
    out: list[MapPoint] = []
    for point in list(pmc) + list(coop) + list(scav):
        key = (point.label or point.id or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(point)
    out.sort(key=lambda p: (p.label or p.id or "").lower())
    return out


class ExtractAvailabilityPanel(QFrame):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("extractAvail")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        title = QLabel("Available extracts (this raid)")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        hint = QLabel("Routes may only finish at checked extracts.")
        hint.setObjectName("status")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._host = QWidget()
        self._list = QVBoxLayout(self._host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(2)
        layout.addWidget(self._host)
        self._boxes: dict[str, QCheckBox] = {}
        self._points: dict[str, MapPoint] = {}
        self._suppress = False

    def rebuild(self, extracts: list[MapPoint], selected_keys: set[str] | None):
        self._suppress = True
        while self._list.count():
            item = self._list.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._boxes.clear()
        self._points.clear()
        keys = selected_keys
        for point in extracts:
            key = (point.label or point.id or "").strip()
            if not key:
                continue
            self._points[key] = point
            box = QCheckBox(key)
            # Default: all PMC-first unique extracts on, unless we have a saved set.
            box.setChecked(True if keys is None else key in keys)
            box.stateChanged.connect(self._emit)
            self._boxes[key] = box
            self._list.addWidget(box)
        self._suppress = False

    def _emit(self):
        if not self._suppress:
            self.changed.emit()

    def selected_keys(self) -> set[str]:
        return {key for key, box in self._boxes.items() if box.isChecked()}

    def selected_points(self) -> list[MapPoint]:
        return [self._points[k] for k, box in self._boxes.items() if box.isChecked() and k in self._points]
