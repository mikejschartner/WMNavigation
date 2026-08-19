"""Camera pose for ping rays. Screenshot quaternion is truth; mouse fills gaps."""

from __future__ import annotations

from dataclasses import dataclass

from .coords import PlayerState
from .heading import wrap_deg


@dataclass
class CameraPose:
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    fov: float
    yaw_confidence: float
    pitch_confidence: float
    origin_source: str
    predicted_origin: bool


def pose_from_confirmed(
    player: PlayerState,
    *,
    fov: float,
    height_offset: float | None,
    yaw_bias: float = 0.0,
    pitch_bias: float = 0.0,
) -> CameraPose:
    y = float(player.y)
    source = "screenshot tracker"
    if height_offset is not None:
        y += float(height_offset)
        source = "screenshot tracker + calibrated camera offset"
    return CameraPose(
        x=float(player.x),
        y=y,
        z=float(player.z),
        yaw=wrap_deg(float(player.yaw_deg) + float(yaw_bias or 0.0)),
        pitch=float(player.pitch_deg) + float(pitch_bias or 0.0),
        fov=float(fov),
        yaw_confidence=1.0,
        pitch_confidence=1.0,
        origin_source=source,
        predicted_origin=False,
    )


def pose_from_predicted(
    player: PlayerState,
    *,
    fov: float,
    height_offset: float | None,
    yaw_bias: float = 0.0,
    pitch_bias: float = 0.0,
    confidence: float,
) -> CameraPose:
    y = float(player.y)
    source = "predicted + screenshot origin"
    if height_offset is not None:
        y += float(height_offset)
        source = "predicted + calibrated camera offset"
    conf = max(0.0, min(1.0, float(confidence)))
    return CameraPose(
        x=float(player.x),
        y=y,
        z=float(player.z),
        yaw=wrap_deg(float(player.yaw_deg) + float(yaw_bias or 0.0)),
        pitch=float(player.pitch_deg) + float(pitch_bias or 0.0),
        fov=float(fov),
        yaw_confidence=conf,
        pitch_confidence=conf,
        origin_source=source,
        predicted_origin=True,
    )


def pitch_ok(pose: CameraPose, min_conf: float = 0.35) -> bool:
    return pose.pitch_confidence >= min_conf


def standing_eye_y(player_y: float, ground_y: float | None, extra_offset: float | None) -> float:
    """Screenshot Y matches player root/spawns. Lift to eye height when sitting on the dirt."""
    y = float(player_y)
    if extra_offset is not None:
        y += float(extra_offset)
    if ground_y is None:
        return y
    ground = float(ground_y)
    clearance = y - ground
    if -0.5 <= clearance < 1.15:
        return ground + 1.5
    return y
