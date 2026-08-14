"""Item filter modal: every map item, expensive first, toggle what appears on the map."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .icon_cache import get_item_icon
from .loot_filter import item_best_price
from .models import ItemInfo


class ItemFilterDialog(QDialog):
    selection_saved = Signal(set)

    def __init__(self, map_items: dict[str, ItemInfo], selected: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter Items")
        self.resize(560, 720)
        self.setModal(True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.map_items = map_items
        self.selected = set(selected)
        self.rows: dict[str, QCheckBox] = {}
        self._icon_queue: list[tuple[str, QCheckBox, str]] = []

        layout = QVBoxLayout(self)

        hint = QLabel("All loot on this map, highest value first. Check items to show their spawn locations.")
        hint.setObjectName("status")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        top = QHBoxLayout()
        top.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type item name...")
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        btn_all = QPushButton("Select All")
        btn_all.clicked.connect(self._select_all_visible)
        btn_none = QPushButton("Deselect All")
        btn_none.clicked.connect(self._deselect_all)
        top.addWidget(btn_all)
        top.addWidget(btn_none)
        layout.addLayout(top)

        self.count_label = QLabel()
        layout.addWidget(self.count_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(6, 6, 6, 6)
        self.list_layout.setSpacing(2)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        self.footer = QLabel()
        bottom.addWidget(self.footer, 1)
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bottom.addWidget(save)
        bottom.addWidget(cancel)
        layout.addLayout(bottom)

        self._populate()
        QTimer.singleShot(0, self._load_icons_batch)

    def _populate(self):
        ordered = sorted(
            self.map_items.values(),
            key=lambda item: (-item_best_price(item), item.name.lower()),
        )
        for item in ordered:
            price = item_best_price(item)
            row = QCheckBox(f"₽{price:,}   {item.name}")
            row.setChecked(item.id in self.selected)
            row.setIconSize(QSize(32, 32))
            row.setProperty("item_id", item.id)
            row.setProperty("item_name", f"{item.name} {item.short_name}".lower())
            row.setToolTip(
                f"{item.name}\n{item.short_name}\n"
                f"Best: ₽{price:,}\nFlea: ₽{item.flea_price:,}\nTrader: ₽{item.trader_price:,}"
            )
            row.stateChanged.connect(self._update_footer)
            self.rows[item.id] = row
            self._icon_queue.append((item.id, row, item.icon_url))
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        self._apply_filter()
        self._update_footer()

    def _load_icons_batch(self):
        batch = self._icon_queue[:16]
        self._icon_queue = self._icon_queue[16:]
        for _iid, row, url in batch:
            pix = get_item_icon(url, 40)
            if not pix.isNull():
                row.setIcon(QIcon(pix))
        if self._icon_queue:
            QTimer.singleShot(5, self._load_icons_batch)

    def _apply_filter(self):
        term = self.search.text().strip().lower()
        visible = 0
        for _iid, row in self.rows.items():
            name = row.property("item_name") or ""
            show = not term or term in name
            row.setVisible(show)
            if show:
                visible += 1
        self.count_label.setText(f"{visible} items · sorted by max price")

    def _select_all_visible(self):
        for row in self.rows.values():
            if row.isVisible():
                row.setChecked(True)

    def _deselect_all(self):
        for row in self.rows.values():
            row.setChecked(False)

    def _update_footer(self):
        selected = sum(1 for row in self.rows.values() if row.isChecked())
        self.footer.setText(f"{selected} items selected")

    def _save(self):
        self.selected = {iid for iid, row in self.rows.items() if row.isChecked()}
        self.selection_saved.emit(self.selected)
        self.accept()

    def get_selected(self) -> set[str]:
        return set(self.selected)
