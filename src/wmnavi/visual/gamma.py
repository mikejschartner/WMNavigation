"""Session-only per-monitor gamma LUT. Restored on disable and process exit."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from ..applog import get_logger
from .profiles import VisualSettings
from .tone import build_gamma_ramp, identity_ramp

log = get_logger("wmnavi.visual")

gdi32 = ctypes.windll.gdi32
gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
gdi32.CreateDCW.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.GetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]

RAMP_SIZE = 256


class _Ramp(ctypes.Structure):
    _fields_ = [
        ("red", ctypes.c_ushort * RAMP_SIZE),
        ("green", ctypes.c_ushort * RAMP_SIZE),
        ("blue", ctypes.c_ushort * RAMP_SIZE),
    ]


def _fill_ramp(dest: _Ramp, red: list[int], green: list[int], blue: list[int]) -> None:
    for i in range(RAMP_SIZE):
        dest.red[i] = red[i]
        dest.green[i] = green[i]
        dest.blue[i] = blue[i]


class GammaFilter:
    def __init__(self):
        self._original: dict[str, _Ramp] = {}
        self._active_device = ""

    def _dc(self, device: str):
        if not device:
            return None
        return gdi32.CreateDCW("DISPLAY", device, None, None)

    def _close(self, hdc) -> None:
        if hdc:
            gdi32.DeleteDC(hdc)

    def _remember(self, device: str, hdc) -> None:
        if device in self._original:
            return
        ramp = _Ramp()
        if gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
            self._original[device] = ramp
        else:
            red, green, blue = identity_ramp()
            stored = _Ramp()
            _fill_ramp(stored, red, green, blue)
            self._original[device] = stored

    def apply(self, device: str, settings: VisualSettings) -> bool:
        if not device:
            return False
        hdc = self._dc(device)
        if not hdc:
            log.info("Renderer failure: CreateDC failed for %s", device)
            return False
        try:
            self._remember(device, hdc)
            if self._active_device and self._active_device != device:
                self.restore_device(self._active_device)
            red, green, blue = build_gamma_ramp(settings)
            ramp = _Ramp()
            _fill_ramp(ramp, red, green, blue)
            ok = bool(gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)))
            if ok:
                self._active_device = device
            else:
                log.info("Renderer failure: SetDeviceGammaRamp failed for %s", device)
            return ok
        finally:
            self._close(hdc)

    def restore_device(self, device: str) -> None:
        original = self._original.get(device)
        if not device or original is None:
            return
        hdc = self._dc(device)
        if not hdc:
            return
        try:
            gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(original))
        finally:
            self._close(hdc)
        if self._active_device == device:
            self._active_device = ""

    def restore_all(self) -> None:
        for device in list(self._original):
            self.restore_device(device)
        self._active_device = ""
        log.info("Renderer shutdown")
