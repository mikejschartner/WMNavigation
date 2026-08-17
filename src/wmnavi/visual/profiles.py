"""In-memory visual profiles. Nothing here is written to disk or QSettings."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

DEFAULT_PROFILE_NAMES = (
    "Default",
    "Profile 1",
    "Profile 2",
    "Profile 3",
    "Profile 4",
    "Profile 5",
)


@dataclass
class VisualSettings:
    brightness: float = 0.0  # -0.5 .. 0.5
    gamma: float = 1.0  # 0.30 .. 5.00
    contrast: float = 1.0  # 0.20 .. 3.00
    exposure: float = 0.0  # -2 .. +4 EV
    shadow_boost: float = 0.0  # 0 .. 1
    black_level: float = 0.0  # 0 .. 0.40
    highlight_reduction: float = 0.0  # 0 .. 1
    saturation: float = 1.0  # 0 .. 2.5
    sharpness: float = 0.0  # 0 .. 1
    temperature: int = 6500  # 3000 .. 10000 K

    def is_identity(self) -> bool:
        return (
            abs(self.brightness) < 1e-6
            and abs(self.gamma - 1.0) < 1e-6
            and abs(self.contrast - 1.0) < 1e-6
            and abs(self.exposure) < 1e-6
            and abs(self.shadow_boost) < 1e-6
            and abs(self.black_level) < 1e-6
            and abs(self.highlight_reduction) < 1e-6
            and abs(self.saturation - 1.0) < 1e-6
            and abs(self.sharpness) < 1e-6
            and int(self.temperature) == 6500
        )

    def needs_spatial(self) -> bool:
        return abs(self.saturation - 1.0) > 0.02 or self.sharpness > 0.02

    def copy(self) -> VisualSettings:
        return replace(self)


@dataclass
class VisualProfile:
    name: str
    settings: VisualSettings = field(default_factory=VisualSettings)
    locked: bool = False

    def copy(self) -> VisualProfile:
        return VisualProfile(self.name, self.settings.copy(), self.locked)


class VisualProfileManager:
    """Six profile slots that live only in this process."""

    def __init__(self):
        self.profiles = [
            VisualProfile(name, VisualSettings(), locked=(index == 0))
            for index, name in enumerate(DEFAULT_PROFILE_NAMES)
        ]
        self.active_index = 0
        self.filter_enabled = False
        self.monitor_key = ""
        self.draft = VisualSettings()

    def active(self) -> VisualProfile:
        return self.profiles[self.active_index]

    def stored_settings(self) -> VisualSettings:
        return self.profiles[self.active_index].settings.copy()

    def select(self, index: int) -> VisualSettings:
        self.active_index = max(0, min(len(self.profiles) - 1, int(index)))
        self.draft = self.stored_settings()
        return self.draft.copy()

    def cycle(self) -> VisualSettings:
        return self.select((self.active_index + 1) % len(self.profiles))

    def rename_active(self, name: str) -> str:
        profile = self.active()
        if profile.locked:
            return profile.name
        text = (name or "").strip()[:32] or DEFAULT_PROFILE_NAMES[self.active_index]
        profile.name = text
        return profile.name

    def save_draft_to_active(self) -> bool:
        profile = self.active()
        if profile.locked:
            self.draft = VisualSettings()
            return False
        profile.settings = self.draft.copy()
        return True

    def reset_active(self) -> VisualSettings:
        profile = self.active()
        profile.settings = VisualSettings()
        if not profile.locked:
            profile.name = DEFAULT_PROFILE_NAMES[self.active_index]
        self.draft = VisualSettings()
        return self.draft.copy()

    def duplicate_draft_to(self, index: int) -> bool:
        if index <= 0 or index >= len(self.profiles):
            return False
        self.profiles[index].settings = self.draft.copy()
        return True

    def live_settings(self) -> VisualSettings:
        if not self.filter_enabled or self.active_index == 0:
            return VisualSettings()
        return self.draft.copy()
