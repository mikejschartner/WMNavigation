"""Scrollable quest panel grouped by trader (This map / Anywhere)."""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .quest_loader import QuestInfo

# Stable trader display order (known Tarkov traders first).
TRADER_ORDER = [
    "Prapor",
    "Therapist",
    "Fence",
    "Skier",
    "Peacekeeper",
    "Mechanic",
    "Ragman",
    "Jaeger",
    "Ref",
    "Lightkeeper",
    "BTR Driver",
]


def _trader_sort_key(name: str) -> tuple:
    name = name or "Other"
    try:
        return (0, TRADER_ORDER.index(name), name.lower())
    except ValueError:
        return (1, 999, name.lower())


def group_by_trader(quests: list[QuestInfo]) -> list[tuple[str, list[QuestInfo]]]:
    buckets: dict[str, list[QuestInfo]] = defaultdict(list)
    for q in quests:
        buckets[q.trader or "Other"].append(q)
    for lst in buckets.values():
        lst.sort(key=lambda q: q.name.lower())
    return sorted(buckets.items(), key=lambda kv: _trader_sort_key(kv[0]))


class QuestListPanel(QDialog):
    """This map + Anywhere, each grouped by trader."""

    toggled = Signal(str, bool)  # quest_id, checked
    hide_all = Signal()
    show_all = Signal()

    def __init__(
        self,
        parent=None,
        *,
        map_quests: list[QuestInfo],
        anywhere_quests: list[QuestInfo],
        active_ids: set[str],
        map_label: str = "This map",
        from_logs: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle("Active Quests")
        self.setMinimumSize(360, 280)
        self._checks: dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel(
            "Your active quests (from EFT logs)" if from_logs else "Quests"
        )
        title.setObjectName("title")
        root.addWidget(title)
        tip = QLabel(
            "These are quests your logs show as accepted and not finished — "
            "not every quest in the game. Grouped by trader. "
            "Hover a quest for its objectives. "
            "Anywhere = objectives with no map (find/hand-in/build/skill); "
            "other-map quests stay hidden here."
        )
        tip.setWordWrap(True)
        tip.setObjectName("status")
        root.addWidget(tip)

        btn_row = QHBoxLayout()
        hide_btn = QPushButton("Hide all map markers")
        hide_btn.clicked.connect(self.hide_all.emit)
        show_btn = QPushButton("Show all map markers")
        show_btn.clicked.connect(self.show_all.emit)
        btn_row.addWidget(hide_btn)
        btn_row.addWidget(show_btn)
        btn_row.addStretch(1)
        root.addLayout(btn_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(10)

        body_layout.addWidget(self._section_header(f"{map_label} ({len(map_quests)})"))
        if map_quests:
            for trader, quests in group_by_trader(map_quests):
                body_layout.addWidget(self._trader_block(trader, quests, checkable=True, active_ids=active_ids))
        else:
            empty = QLabel("No active quests with objectives on this map.")
            empty.setObjectName("status")
            body_layout.addWidget(empty)

        body_layout.addWidget(
            self._section_header(f"Anywhere (no map) ({len(anywhere_quests)})")
        )
        if anywhere_quests:
            for trader, quests in group_by_trader(anywhere_quests):
                body_layout.addWidget(self._trader_block(trader, quests, checkable=False, active_ids=active_ids))
        else:
            empty = QLabel("No active any-map objectives (find/hand-in/build/skill).")
            empty.setObjectName("status")
            body_layout.addWidget(empty)

        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        root.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)

    def _section_header(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet("font-weight: 700; font-size: 14px; margin-top: 6px;")
        return lab

    def _trader_block(
        self,
        trader: str,
        quests: list[QuestInfo],
        *,
        checkable: bool,
        active_ids: set[str],
    ) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sidebarBottom")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        head = QLabel(f"{trader} ({len(quests)})")
        head.setStyleSheet("font-weight: 600; color: #c4b5a0;")
        layout.addWidget(head)

        for quest in quests:
            row = QHBoxLayout()
            prefix = "K · " if quest.requires_key else ""
            pin = f" · {len(quest.spots)} pin(s)" if quest.spots else " · no map pin"
            label = f"{prefix}{quest.name}{pin}"
            tip = (quest.requirements_text or "").strip() or "No objective details available."
            if quest.spots:
                tip = f"{len(quest.spots)} location(s) on this map.\n\n{tip}"
            else:
                tip = f"No coordinate pin for this map.\n\n{tip}"
            if checkable:
                box = QCheckBox(label)
                box.setChecked(quest.id in active_ids)
                box.setToolTip(tip)
                box.toggled.connect(lambda checked, qid=quest.id: self.toggled.emit(qid, checked))
                self._checks[quest.id] = box
                row.addWidget(box, 1)
            else:
                lab = QLabel(label)
                lab.setWordWrap(True)
                lab.setToolTip(tip)
                row.addWidget(lab, 1)
            layout.addLayout(row)
        return frame

    def set_checked(self, quest_id: str, checked: bool):
        box = self._checks.get(quest_id)
        if not box:
            return
        box.blockSignals(True)
        box.setChecked(checked)
        box.blockSignals(False)

    def set_all_map_checked(self, checked: bool):
        for box in self._checks.values():
            box.blockSignals(True)
            box.setChecked(checked)
            box.blockSignals(False)
