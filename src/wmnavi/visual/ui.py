"""Visual Profiles page: session-only sliders, monitor picker, Original|Filtered preview."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import numpy as np

from ..win_capture import capture_eft_bgr, capture_screen_rect_bgr
from .engine import VisualFilterEngine
from .monitors import list_monitors, monitor_by_key
from .profiles import DEFAULT_PROFILE_NAMES, VisualSettings
from .tone import apply_preview_bgr


def _bgr_to_pixmap(bgr: np.ndarray) -> QPixmap:
    rgb = bgr[:, :, ::-1].copy()
    h, w, _ = rgb.shape
    image = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())


def _test_pattern(width: int = 360, height: int = 200) -> np.ndarray:
    img = np.zeros((height, width, 3), dtype=np.uint8)
    for y in range(height):
        v = int(220 * (y / max(height - 1, 1)) ** 2.4)
        img[y, :, :] = (v, v, v)
    for i, color in enumerate(((40, 40, 180), (40, 180, 40), (180, 40, 40), (20, 20, 20), (200, 200, 200))):
        x0 = 8 + i * (width - 16) // 5
        x1 = 8 + (i + 1) * (width - 16) // 5 - 4
        img[height - 36 : height - 8, x0:x1] = color
    return img


class _SliderRow(QWidget):
    def __init__(self, title: str, minimum: int, maximum: int, default: int, fmt, parent=None):
        super().__init__(parent)
        self.default = default
        self._fmt = fmt
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(2)
        head = QHBoxLayout()
        self.caption = QLabel(title)
        self.value_label = QLabel()
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setFixedWidth(64)
        head.addWidget(self.caption, 1)
        head.addWidget(self.value_label)
        head.addWidget(self.btn_reset)
        layout.addLayout(head)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(default)
        layout.addWidget(self.slider)
        self.slider.valueChanged.connect(self._sync)
        self.btn_reset.clicked.connect(lambda: self.slider.setValue(self.default))
        self._sync(self.slider.value())

    def _sync(self, value: int):
        self.value_label.setText(self._fmt(value))

    def set_enabled_row(self, on: bool):
        self.slider.setEnabled(on)
        self.btn_reset.setEnabled(on)


class VisualProfilesPage(QWidget):
    def __init__(self, engine: VisualFilterEngine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._preview_src: np.ndarray | None = None
        self._updating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        title = QLabel("Visual Profiles")
        title.setObjectName("title")
        root.addWidget(title)
        blurb = QLabel(
            "Session-only monitor tone controls. Nothing is saved when you close the app. "
            "Live filter uses the selected display’s gamma ramp (Tarkov keeps full FPS). "
            "Requires windowed or borderless Tarkov — exclusive fullscreen can ignore the LUT. "
            "Saturation and sharpness are shown in the preview; they cannot be applied at "
            "scanout without recapturing the desktop and capping frame rate."
        )
        blurb.setObjectName("status")
        blurb.setWordWrap(True)
        root.addWidget(blurb)

        top = QHBoxLayout()
        self.chk_filter = QCheckBox("Visual Filter")
        self.chk_filter.setToolTip("F10 toggles. Off restores the selected monitor immediately.")
        top.addWidget(self.chk_filter)
        top.addWidget(QLabel("Apply Filter To:"))
        self.monitor_combo = QComboBox()
        top.addWidget(self.monitor_combo, 1)
        self.btn_refresh_monitors = QPushButton("Refresh")
        self.btn_refresh_monitors.setFixedWidth(88)
        top.addWidget(self.btn_refresh_monitors)
        root.addLayout(top)

        slot_row = QHBoxLayout()
        self.profile_btns: list[QPushButton] = []
        for i, name in enumerate(DEFAULT_PROFILE_NAMES):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, idx=i: self._pick_profile(idx))
            self.profile_btns.append(btn)
            slot_row.addWidget(btn)
        root.addLayout(slot_row)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Rename"))
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(32)
        name_row.addWidget(self.name_edit, 1)
        self.btn_rename = QPushButton("Apply name")
        name_row.addWidget(self.btn_rename)
        root.addLayout(name_row)

        dup_row = QHBoxLayout()
        dup_row.addWidget(QLabel("Copy current settings to"))
        self.dup_combo = QComboBox()
        for i, name in enumerate(DEFAULT_PROFILE_NAMES):
            if i == 0:
                continue
            self.dup_combo.addItem(name, i)
        dup_row.addWidget(self.dup_combo, 1)
        self.btn_duplicate = QPushButton("Duplicate")
        dup_row.addWidget(self.btn_duplicate)
        root.addLayout(dup_row)

        actions = QHBoxLayout()
        self.btn_apply = QPushButton("Apply")
        self.btn_save = QPushButton("Save Profile")
        self.btn_reset = QPushButton("Reset Profile")
        actions.addWidget(self.btn_apply)
        actions.addWidget(self.btn_save)
        actions.addWidget(self.btn_reset)
        actions.addStretch(1)
        root.addLayout(actions)

        self.status = QLabel("Visual Filter off · Default")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        form = QVBoxLayout(host)
        form.setContentsMargins(0, 0, 8, 0)
        self.row_brightness = _SliderRow("Brightness", -100, 100, 0, lambda v: f"{v/100:+.2f}")
        self.row_gamma = _SliderRow("Gamma", 30, 500, 100, lambda v: f"{v/100:.2f}")
        self.row_contrast = _SliderRow("Contrast", 20, 300, 100, lambda v: f"{v/100:.2f}")
        self.row_exposure = _SliderRow("Exposure", -200, 400, 0, lambda v: f"{v/100:+.2f} EV")
        self.row_shadow = _SliderRow("Shadow Boost / Shadow Lift", 0, 100, 0, lambda v: f"{v}")
        self.row_black = _SliderRow("Black Level", 0, 40, 0, lambda v: f"{v/100:.2f}")
        self.row_highlight = _SliderRow("Highlight Reduction", 0, 100, 0, lambda v: f"{v}")
        self.row_sat = _SliderRow("Saturation (preview)", 0, 250, 100, lambda v: f"{v/100:.2f}")
        self.row_sharp = _SliderRow("Sharpness (preview)", 0, 100, 0, lambda v: f"{v}")
        self.row_temp = _SliderRow("Color Temperature", 3000, 10000, 6500, lambda v: f"{v} K")
        for row in (
            self.row_brightness,
            self.row_gamma,
            self.row_contrast,
            self.row_exposure,
            self.row_shadow,
            self.row_black,
            self.row_highlight,
            self.row_sat,
            self.row_sharp,
            self.row_temp,
        ):
            form.addWidget(row)
            row.slider.valueChanged.connect(self._on_slider)
        form.addStretch(1)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        preview_frame = QFrame()
        preview_frame.setObjectName("mapOverlay")
        preview_layout = QVBoxLayout(preview_frame)
        preview_head = QHBoxLayout()
        preview_head.addWidget(QLabel("Original"))
        preview_head.addStretch(1)
        preview_head.addWidget(QLabel("Filtered"))
        preview_layout.addLayout(preview_head)
        pics = QHBoxLayout()
        self.preview_original = QLabel()
        self.preview_filtered = QLabel()
        for label in (self.preview_original, self.preview_filtered):
            label.setMinimumHeight(160)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("background: #050508; border: 1px solid rgba(168,85,247,0.25);")
        pics.addWidget(self.preview_original, 1)
        pics.addWidget(self.preview_filtered, 1)
        preview_layout.addLayout(pics)
        root.addWidget(preview_frame)

        self._preview_timer = QTimer(self)
        self._preview_timer.setInterval(900)
        self._preview_timer.timeout.connect(self._recapture_preview)

        self.chk_filter.toggled.connect(self._on_filter_toggled)
        self.monitor_combo.currentIndexChanged.connect(self._on_monitor_changed)
        self.btn_refresh_monitors.clicked.connect(self.reload_monitors)
        self.btn_rename.clicked.connect(self._rename)
        self.name_edit.returnPressed.connect(self._rename)
        self.btn_duplicate.clicked.connect(self._duplicate)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_save.clicked.connect(self._save)
        self.btn_reset.clicked.connect(self._reset)

        engine.filter_toggled.connect(self._sync_from_engine)
        engine.profile_changed.connect(lambda _n: self._sync_from_engine())
        engine.status.connect(self._set_status)

        self.reload_monitors()
        self._sync_from_engine()
        self._recapture_preview()

    def showEvent(self, event):
        super().showEvent(event)
        self._preview_timer.start()
        self.reload_monitors()

    def hideEvent(self, event):
        self._preview_timer.stop()
        super().hideEvent(event)

    def reload_monitors(self):
        self._updating = True
        current = self.engine.manager.monitor_key
        self.monitor_combo.clear()
        monitors = list_monitors()
        select = 0
        for i, item in enumerate(monitors):
            self.monitor_combo.addItem(item.label, item.key)
            if item.key == current or (not current and item.primary):
                select = i
        if monitors:
            self.monitor_combo.setCurrentIndex(select)
            self.engine.manager.monitor_key = str(self.monitor_combo.currentData() or monitors[0].key)
        self._updating = False

    def _settings_from_sliders(self) -> VisualSettings:
        return VisualSettings(
            brightness=self.row_brightness.slider.value() / 100.0,
            gamma=self.row_gamma.slider.value() / 100.0,
            contrast=self.row_contrast.slider.value() / 100.0,
            exposure=self.row_exposure.slider.value() / 100.0,
            shadow_boost=self.row_shadow.slider.value() / 100.0,
            black_level=self.row_black.slider.value() / 100.0,
            highlight_reduction=self.row_highlight.slider.value() / 100.0,
            saturation=self.row_sat.slider.value() / 100.0,
            sharpness=self.row_sharp.slider.value() / 100.0,
            temperature=int(self.row_temp.slider.value()),
        )

    def _sliders_from_settings(self, settings: VisualSettings):
        self._updating = True
        self.row_brightness.slider.setValue(int(round(settings.brightness * 100)))
        self.row_gamma.slider.setValue(int(round(settings.gamma * 100)))
        self.row_contrast.slider.setValue(int(round(settings.contrast * 100)))
        self.row_exposure.slider.setValue(int(round(settings.exposure * 100)))
        self.row_shadow.slider.setValue(int(round(settings.shadow_boost * 100)))
        self.row_black.slider.setValue(int(round(settings.black_level * 100)))
        self.row_highlight.slider.setValue(int(round(settings.highlight_reduction * 100)))
        self.row_sat.slider.setValue(int(round(settings.saturation * 100)))
        self.row_sharp.slider.setValue(int(round(settings.sharpness * 100)))
        self.row_temp.slider.setValue(int(settings.temperature))
        self._updating = False

    def _on_slider(self, _value: int = 0):
        if self._updating:
            return
        self.engine.set_draft(self._settings_from_sliders(), apply_now=True)
        self._render_preview()

    def _on_filter_toggled(self, on: bool):
        if self._updating:
            return
        self.engine.set_enabled(on)
        self._refresh_status()
        self._render_preview()

    def _on_monitor_changed(self, _index: int):
        if self._updating:
            return
        key = str(self.monitor_combo.currentData() or "")
        self.engine.set_monitor_key(key)
        self._recapture_preview()

    def _pick_profile(self, index: int):
        self.engine.select_profile(index)
        self._sync_from_engine()

    def _rename(self):
        name = self.engine.manager.rename_active(self.name_edit.text())
        self.name_edit.setText(name)
        self._refresh_profile_buttons()
        self._refresh_status()

    def _duplicate(self):
        index = int(self.dup_combo.currentData() or 0)
        if self.engine.manager.duplicate_draft_to(index):
            self._set_status(f"Copied settings to {self.engine.manager.profiles[index].name}")

    def _apply(self):
        self.engine.set_draft(self._settings_from_sliders(), apply_now=True)
        if not self.engine.manager.filter_enabled:
            self.engine.set_enabled(True)
            self._sync_from_engine()
        self._set_status("Applied current sliders")

    def _save(self):
        self.engine.set_draft(self._settings_from_sliders(), apply_now=False)
        self.engine.save_profile()
        self._refresh_profile_buttons()

    def _reset(self):
        self.engine.reset_profile()
        self._sync_from_engine()

    def _sync_from_engine(self):
        self._updating = True
        self.chk_filter.setChecked(self.engine.manager.filter_enabled)
        self._updating = False
        self._sliders_from_settings(self.engine.manager.draft)
        self.name_edit.setText(self.engine.manager.active().name)
        locked = self.engine.manager.active().locked
        self.name_edit.setEnabled(not locked)
        self.btn_rename.setEnabled(not locked)
        self.btn_save.setEnabled(not locked)
        for row in (
            self.row_brightness,
            self.row_gamma,
            self.row_contrast,
            self.row_exposure,
            self.row_shadow,
            self.row_black,
            self.row_highlight,
            self.row_sat,
            self.row_sharp,
            self.row_temp,
        ):
            row.set_enabled_row(not locked)
        self._refresh_profile_buttons()
        self._refresh_status()
        self._render_preview()

    def _refresh_profile_buttons(self):
        for i, btn in enumerate(self.profile_btns):
            btn.setText(self.engine.manager.profiles[i].name)
            btn.setChecked(i == self.engine.manager.active_index)

    def _refresh_status(self):
        state = "ON" if self.engine.manager.filter_enabled else "OFF"
        name = self.engine.manager.active().name
        extra = " · Default is unmodified" if self.engine.manager.active_index == 0 else ""
        self.status.setText(f"Visual Filter: {state} · {name}{extra} · F10 toggle · F11 cycle")

    def _set_status(self, text: str):
        self.status.setText(text)

    def _recapture_preview(self):
        monitor = monitor_by_key(self.engine.manager.monitor_key)
        frame = None
        if monitor is not None:
            frame = capture_screen_rect_bgr(monitor.x, monitor.y, monitor.width, monitor.height, min_size=32)
        if frame is None:
            frame = capture_eft_bgr(max_width=360)
        if frame is None:
            frame = _test_pattern()
        else:
            h, w = frame.shape[:2]
            if w > 420:
                scale = 420 / float(w)
                new_h = max(80, int(h * scale))
                xs = np.linspace(0, w - 1, 420).astype(np.int32)
                ys = np.linspace(0, h - 1, new_h).astype(np.int32)
                frame = frame[ys][:, xs]
        self._preview_src = frame
        self._render_preview()

    def _render_preview(self):
        src = self._preview_src
        if src is None:
            src = _test_pattern()
            self._preview_src = src
        self.preview_original.setPixmap(
            _bgr_to_pixmap(src).scaled(
                self.preview_original.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        filtered = apply_preview_bgr(src, self.engine.manager.draft)
        self.preview_filtered.setPixmap(
            _bgr_to_pixmap(filtered).scaled(
                self.preview_filtered.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
