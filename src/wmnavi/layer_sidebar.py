"""Questie-style collapsible map layer sidebar."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .models import ContainerTypeInfo, MapLayerData

LAYER_PRESETS: dict[str, dict[str, bool]] = {
    "quests": {
        "loose_loot": False,
        "containers_all": False,
        "extracts_all": False,
        "extract_pmc": True,
        "extract_scav": False,
        "extract_coop": False,
        "transits": False,
        "locks": True,
        "switches": False,
        "stationary_weapons": False,
    },
    "loot": {
        "loose_loot": True,
        "containers_all": True,
        "extracts_all": False,
        "extract_pmc": True,
        "extract_scav": False,
        "extract_coop": False,
        "transits": False,
        "locks": False,
        "switches": False,
        "stationary_weapons": False,
    },
    "extracts": {
        "loose_loot": False,
        "containers_all": False,
        "extracts_all": True,
        "extract_pmc": True,
        "extract_scav": True,
        "extract_coop": True,
        "transits": True,
        "locks": False,
        "switches": False,
        "stationary_weapons": False,
    },
}


class CollapsibleSection(QWidget):
    """Checkable header that shows ▾ Title when open and ▸ Title when closed."""

    def __init__(self, title: str, *, expanded: bool = True, parent=None):
        super().__init__(parent)
        self._title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(4)
        self.toggle = QPushButton()
        self.toggle.setObjectName("sectionToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.toggled.connect(self._on_toggled)
        layout.addWidget(self.toggle)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(4, 0, 0, 4)
        self.body_layout.setSpacing(4)
        layout.addWidget(self.body)
        self._sync_header()
        self.body.setVisible(expanded)

    def _on_toggled(self, on: bool):
        self.body.setVisible(on)
        self._sync_header()

    def _sync_header(self):
        arrow = "▾" if self.toggle.isChecked() else "▸"
        self.toggle.setText(f"{arrow} {self._title}")


class LayerSection(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("layerSection")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(4)
        self.title = QLabel(title.upper())
        self.title.setObjectName("sectionTitle")
        layout.addWidget(self.title)
        self.body = QVBoxLayout()
        self.body.setSpacing(3)
        layout.addLayout(self.body)
        self.checkboxes: dict[str, QCheckBox] = {}

    def add_checkbox(self, key: str, label: str, checked: bool = False) -> QCheckBox:
        row = QCheckBox(label)
        row.setChecked(checked)
        row.setProperty("layer_key", key)
        self.body.addWidget(row)
        self.checkboxes[key] = row
        return row

    def set_checked(self, key: str, checked: bool):
        box = self.checkboxes.get(key)
        if box:
            box.setChecked(checked)


class MapLayersSidebar(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self.sections: dict[str, LayerSection] = {}
        self._suppress = False

    def clear_sections(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.sections.clear()

    def _add_section(self, key: str, title: str) -> LayerSection:
        section = LayerSection(title, self)
        self._layout.addWidget(section)
        self.sections[key] = section
        return section

    def rebuild(self, data: MapLayerData, settings_prefix: str, settings):
        self.clear_sections()
        self._suppress = True

        loot = self._add_section("loot", "Loot Visibility")
        loot.add_checkbox(
            "loose_loot",
            f"Loose loot ({len(data.loose_loot)})",
            settings.value(f"{settings_prefix}/loose_loot", False, type=bool),
        ).stateChanged.connect(self._emit_changed)

        containers = self._add_section("containers", "Containers")
        containers.add_checkbox(
            "containers_all",
            f"All containers ({sum(len(c.spots) for c in data.containers.values())})",
            settings.value(f"{settings_prefix}/containers_all", False, type=bool),
        ).stateChanged.connect(self._on_all_containers)
        sorted_types: list[ContainerTypeInfo] = sorted(data.containers.values(), key=lambda c: c.name.lower())
        for ctype in sorted_types:
            key = f"container:{ctype.id}"
            containers.add_checkbox(
                key,
                f"{ctype.name} ({len(ctype.spots)})",
                settings.value(f"{settings_prefix}/{key}", False, type=bool),
            ).stateChanged.connect(self._emit_changed)

        extracts = self._add_section("extracts", "Extracts")
        extracts.add_checkbox(
            "extracts_all",
            f"All extracts ({len(data.extracts_pmc) + len(data.extracts_scav) + len(data.extracts_coop)})",
            settings.value(f"{settings_prefix}/extracts_all", False, type=bool),
        ).stateChanged.connect(self._on_all_extracts)
        extracts.add_checkbox(
            "extract_pmc",
            f"PMC extracts ({len(data.extracts_pmc)})",
            settings.value(f"{settings_prefix}/extract_pmc", True, type=bool),
        ).stateChanged.connect(self._emit_changed)
        extracts.add_checkbox(
            "extract_scav",
            f"SCAV extracts ({len(data.extracts_scav)})",
            settings.value(f"{settings_prefix}/extract_scav", True, type=bool),
        ).stateChanged.connect(self._emit_changed)
        extracts.add_checkbox(
            "extract_coop",
            f"Co-op extracts ({len(data.extracts_coop)})",
            settings.value(f"{settings_prefix}/extract_coop", False, type=bool),
        ).stateChanged.connect(self._emit_changed)
        extracts.add_checkbox(
            "transits",
            f"Transit zones ({len(data.transits)})",
            settings.value(f"{settings_prefix}/transits", False, type=bool),
        ).stateChanged.connect(self._emit_changed)

        usables = self._add_section("usables", "Usables")
        usables.add_checkbox(
            "locks",
            f"Locks ({len(data.locks)})",
            settings.value(f"{settings_prefix}/locks", False, type=bool),
        ).stateChanged.connect(self._emit_changed)
        usables.add_checkbox(
            "switches",
            f"Switches ({len(data.switches)})",
            settings.value(f"{settings_prefix}/switches", False, type=bool),
        ).stateChanged.connect(self._emit_changed)
        usables.add_checkbox(
            "stationary_weapons",
            f"Stationary guns ({len(data.stationary_weapons)})",
            settings.value(f"{settings_prefix}/stationary_weapons", False, type=bool),
        ).stateChanged.connect(self._emit_changed)

        self._suppress = False

    def _emit_changed(self):
        if not self._suppress:
            self.changed.emit()

    def _on_all_containers(self):
        if self._suppress:
            return
        section = self.sections.get("containers")
        if not section:
            return
        checked = section.checkboxes["containers_all"].isChecked()
        self._suppress = True
        for key, box in section.checkboxes.items():
            if key.startswith("container:"):
                box.setChecked(checked)
        self._suppress = False
        self.changed.emit()

    def _on_all_extracts(self):
        if self._suppress:
            return
        section = self.sections.get("extracts")
        if not section:
            return
        checked = section.checkboxes["extracts_all"].isChecked()
        self._suppress = True
        for key in ("extract_pmc", "extract_scav", "extract_coop", "transits"):
            section.checkboxes[key].setChecked(checked)
        self._suppress = False
        self.changed.emit()

    def enabled_layers(self) -> dict[str, bool]:
        enabled: dict[str, bool] = {}
        for section in self.sections.values():
            for key, box in section.checkboxes.items():
                enabled[key] = box.isChecked()
        return enabled

    def set_layer_checked(self, key: str, checked: bool, *, emit: bool = True):
        for section in self.sections.values():
            box = section.checkboxes.get(key)
            if not box:
                continue
            self._suppress = True
            box.setChecked(checked)
            self._suppress = False
            if emit:
                self.changed.emit()
            return

    def enabled_container_ids(self) -> set[str]:
        enabled = self.enabled_layers()
        ids: set[str] = set()
        if enabled.get("containers_all"):
            for key in enabled:
                if key.startswith("container:"):
                    ids.add(key.split(":", 1)[1])
            return ids
        for key, on in enabled.items():
            if on and key.startswith("container:"):
                ids.add(key.split(":", 1)[1])
        return ids

    def save_settings(self, settings_prefix: str, settings):
        for section in self.sections.values():
            for key, box in section.checkboxes.items():
                settings.setValue(f"{settings_prefix}/{key}", box.isChecked())

    def apply_preset(self, spec: dict[str, bool]):
        """Set known layer keys; every container:* box follows spec['containers_all']."""
        all_containers = bool(spec.get("containers_all", False))
        self._suppress = True
        for section in self.sections.values():
            for key, box in section.checkboxes.items():
                if key.startswith("container:"):
                    box.setChecked(all_containers)
                elif key in spec:
                    box.setChecked(bool(spec[key]))
        self._suppress = False
        self.changed.emit()
