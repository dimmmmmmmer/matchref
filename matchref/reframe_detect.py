"""Detect clips that need pan/tilt (reframe), not zoom-only."""

from __future__ import annotations

import math

import numpy as np

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig


def refine_position_mode(config: AppConfig) -> str:
    if bool(config.get("refine_position_after_zoom", False)):
        return "always"
    return str(config.get("refine_position_mode", "auto")).lower()


def translation_pixels_from_warp(
    warp: np.ndarray | None,
    timeline_size: tuple[int, int],
    config: AppConfig,
) -> tuple[float, float]:
    if warp is None:
        return 0.0, 0.0
    from matchref.transform_convert import decompose_warp_matrix

    width, height = int(timeline_size[0]), int(timeline_size[1])
    _, tx, ty, _ = decompose_warp_matrix(
        np.asarray(warp),
        width,
        height,
        invert=bool(config.get("alignment_invert", False)),
    )
    pan, tilt = tx, ty
    if bool(config.get("invert_pan_x", False)):
        pan = -pan
    if bool(config.get("invert_tilt_y", True)):
        tilt = -tilt
    return float(pan), float(tilt)


def ecc_suggests_reframe(
    warp: np.ndarray | None,
    timeline_size: tuple[int, int],
    config: AppConfig,
) -> bool:
    pan, tilt = translation_pixels_from_warp(warp, timeline_size, config)
    per_axis = float(config.get("auto_reframe_translation_pixels", 12.0))
    total = float(config.get("auto_reframe_translation_total_pixels", 18.0))
    return abs(pan) >= per_axis or abs(tilt) >= per_axis or math.hypot(pan, tilt) >= total


def should_refine_position(
    *,
    config: AppConfig,
    initial: ClipEditTransform,
    warp: np.ndarray | None,
    timeline_size: tuple[int, int],
    ncc_after_zoom: float,
) -> bool:
    mode = refine_position_mode(config)
    if mode == "off":
        return False
    if mode == "always":
        return True

    ncc_floor = float(config.get("auto_reframe_ncc_threshold", 0.95))
    if ncc_after_zoom < ncc_floor:
        return True

    if ecc_suggests_reframe(warp, timeline_size, config):
        return True

    snap = float(config.get("snap_pan_tilt_below_pixels", 2.0))
    if abs(initial.pan) >= snap or abs(initial.tilt) >= snap:
        return True

    return False


def should_apply_position_from_warp(
    config: AppConfig,
    warp: np.ndarray | None,
    timeline_size: tuple[int, int],
) -> bool:
    """Use ECC pan/tilt in resolve_transform when not using Edit refine."""
    if not bool(config.get("edit_priority_zoom", True)):
        return bool(config.get("match_position", True))
    mode = refine_position_mode(config)
    if mode == "always":
        return True
    if mode == "off":
        return False
    return ecc_suggests_reframe(warp, timeline_size, config)
