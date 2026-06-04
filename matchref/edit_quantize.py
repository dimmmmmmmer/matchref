"""Round Inspector values; snap negligible pan/tilt."""

from __future__ import annotations

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig


def _round_to(value: float, places: int) -> float:
    if places <= 0:
        return float(round(value))
    return float(round(value, places))


def edit_priority_zoom(config: AppConfig) -> bool:
    return bool(config.get("edit_priority_zoom", True))


def quantize_clip_edit(edit: ClipEditTransform, config: AppConfig) -> ClipEditTransform:
    z_places = int(config.get("edit_zoom_decimal_places", 4))
    p_places = int(config.get("edit_pan_decimal_places", 1))
    r_places = int(config.get("edit_rotation_decimal_places", 2))

    pan = _round_to(edit.pan, p_places)
    tilt = _round_to(edit.tilt, p_places)
    rot = _round_to(edit.rotation_deg, r_places)

    if edit_priority_zoom(config):
        thresh = float(config.get("snap_pan_tilt_below_pixels", 2.0))
        if abs(pan) < thresh:
            pan = 0.0
        if abs(tilt) < thresh:
            tilt = 0.0

    zoom = _round_to(edit.zoom_x, z_places)
    return ClipEditTransform(
        zoom_x=zoom,
        zoom_y=_round_to(edit.zoom_y, z_places),
        pan=pan,
        tilt=tilt,
        rotation_deg=rot,
    )
