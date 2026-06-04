"""Round Inspector values; snap negligible pan/tilt."""

from __future__ import annotations

import math

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig


def _round_to(value: float, places: int, mode: str = "nearest") -> float:
    """Round to ``places`` decimals. mode="up" rounds away from zero (the "bigger
    direction": zoom 1.1243 → 1.125), so a match never under-covers the frame."""
    if mode == "up":
        factor = 10**places if places > 0 else 1
        magnitude = math.ceil(abs(value) * factor) / factor
        return float(magnitude if value >= 0 else -magnitude)
    if places <= 0:
        return float(round(value))
    return float(round(value, places))


def edit_priority_zoom(config: AppConfig) -> bool:
    return bool(config.get("edit_priority_zoom", True))


def quantize_clip_edit(edit: ClipEditTransform, config: AppConfig) -> ClipEditTransform:
    z_places = int(config.get("edit_zoom_decimal_places", 4))
    p_places = int(config.get("edit_pan_decimal_places", 1))
    r_places = int(config.get("edit_rotation_decimal_places", 2))
    mode = str(config.get("edit_round_mode", "nearest")).lower()

    # Snap negligible pan/tilt to 0 *before* rounding so "up" mode does not bump a
    # sub-threshold residual (e.g. 0.4px) up to a kept value.
    pan, tilt = edit.pan, edit.tilt
    if edit_priority_zoom(config):
        thresh = float(config.get("snap_pan_tilt_below_pixels", 2.0))
        if abs(pan) < thresh:
            pan = 0.0
        if abs(tilt) < thresh:
            tilt = 0.0

    return ClipEditTransform(
        zoom_x=_round_to(edit.zoom_x, z_places, mode),
        zoom_y=_round_to(edit.zoom_y, z_places, mode),
        pan=_round_to(pan, p_places, mode),
        tilt=_round_to(tilt, p_places, mode),
        rotation_deg=_round_to(edit.rotation_deg, r_places, mode),
    )
