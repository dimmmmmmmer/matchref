"""How online frames are prepared vs lock-cut, and how results map to Inspector."""

from __future__ import annotations

from matchref.config import AppConfig


def is_absolute_edit_match(config: AppConfig) -> bool:
    """
    Absolute (default): fit source to canvas with identity Edit, match lock-cut,
    write target Zoom/Pan/Tilt — independent of the clip's current Inspector.

    Delta (legacy): pre-apply current Edit for ECC, then compose onto baseline.
    """
    return str(config.get("edit_match_mode", "absolute")).lower() != "delta"


def use_baseline_compensation_for_match(config: AppConfig) -> bool:
    if is_absolute_edit_match(config):
        return False
    return bool(config.get("compensate_clip_transform", False))


def compose_result_with_baseline(config: AppConfig) -> bool:
    if is_absolute_edit_match(config):
        return False
    return bool(config.get("compose_with_clip_transform", True))
