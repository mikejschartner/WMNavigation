"""WASAPI loopback of system/game output. Does not mute or reroute audio."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CaptureInfo:
    ok: bool
    device_name: str = ""
    samplerate: int = 48000
    channels: int = 2
    error: str = ""


def find_wasapi_loopback():
    """Return (loopback_mic_or_None, name, samplerate, error)."""
    try:
        import soundcard as sc
    except Exception as exc:
        return None, "", 48000, f"soundcard missing: {exc}"

    try:
        spk = sc.default_speaker()
        name = str(getattr(spk, "name", "") or "Default output")
        mic = sc.get_microphone(id=name, include_loopback=True)
        if mic is None:
            return None, name, 48000, "No loopback microphone for default output"
        sr = 48000
        return mic, f"Loopback {name}", sr, ""
    except Exception as exc:
        return None, "", 48000, str(exc)
