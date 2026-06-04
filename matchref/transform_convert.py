"""Convert OpenCV alignment to Edit Inspector parameters."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from matchref.clip_edit_transform import ClipEditTransform, compose_with_baseline
from matchref.config import AppConfig
from matchref.edit_match_mode import compose_result_with_baseline
from matchref.edit_quantize import edit_priority_zoom, quantize_clip_edit
from matchref.models import ClipAnalysisResult, TransformSample
from matchref.reframe_detect import (
    should_apply_position_from_warp,
    translation_pixels_from_warp,
)


@dataclass
class ResolvedTransform:
    """Values for Edit page Inspector (Pan, Tilt, Zoom, Rotation)."""

    scale: float = 1.0
    rotation_deg: float = 0.0
    edit_zoom_x: float = 1.0
    edit_zoom_y: float = 1.0
    edit_pan: float = 0.0
    edit_tilt: float = 0.0
    edit_rotation: float = 0.0


def decompose_warp_matrix(
    warp: np.ndarray,
    width: int,
    height: int,
    *,
    invert: bool = False,
) -> tuple[float, float, float, float]:
    matrix = np.asarray(warp, dtype=np.float64)
    if matrix.shape == (3, 3):
        matrix = matrix[:2, :]
    if invert:
        aug = np.vstack([matrix, [0.0, 0.0, 1.0]])
        matrix = np.linalg.inv(aug)[:2, :]

    a, b, tx = matrix[0]
    c, d, ty = matrix[1]
    scale_x = math.hypot(a, c)
    scale_y = math.hypot(b, d)
    scale = (scale_x + scale_y) * 0.5
    rotation_deg = math.degrees(math.atan2(c, a))
    return scale, tx, ty, rotation_deg


def resolve_transform(
    sample: TransformSample,
    config: AppConfig,
    timeline_size: tuple[int, int],
    baseline: ClipEditTransform | None = None,
) -> ResolvedTransform:
    """
    Map alignment sample → Edit Inspector.

    OpenCV warp maps online pixels toward offline reference appearance.
    Pan/Tilt are in timeline pixels (Resolve API).
    """
    width = max(1, int(timeline_size[0]))
    height = max(1, int(timeline_size[1]))

    refined = getattr(sample, "refined_edit", None)
    if refined is not None:
        rot_out = float(refined.rotation_deg)
        if bool(config.get("invert_rotation", False)):
            rot_out = -rot_out
        q = quantize_clip_edit(
            ClipEditTransform(
                zoom_x=max(0.001, min(16.0, float(refined.zoom_x))),
                zoom_y=max(0.001, min(16.0, float(refined.zoom_y))),
                pan=float(refined.pan),
                tilt=float(refined.tilt),
                rotation_deg=rot_out,
            ),
            config,
        )
        return ResolvedTransform(
            scale=q.zoom_x,
            rotation_deg=q.rotation_deg,
            edit_zoom_x=q.zoom_x,
            edit_zoom_y=q.zoom_y,
            edit_pan=q.pan,
            edit_tilt=q.tilt,
            edit_rotation=q.rotation_deg,
        )

    warp = getattr(sample, "warp_matrix", None)
    if warp is not None:
        scale, tx, ty, rot = decompose_warp_matrix(
            warp,
            width,
            height,
            invert=bool(config.get("alignment_invert", False)),
        )
    else:
        scale = float(sample.scale)
        rot = float(sample.rotation_deg)
        tx = float(sample.position_x) * width
        ty = float(sample.position_y) * height

    # Resolve Edit: Zoom 1.0 = 100%. Значения >10 в edit_zoom_base трактуются как устаревший «процентный» режим.
    zoom_base = float(config.get("edit_zoom_base", 1.0))
    use_percent = bool(config.get("edit_zoom_as_percent", False)) or zoom_base > 10.0
    if use_percent:
        zoom_x = zoom_y = (zoom_base / 100.0) * scale
    else:
        zoom_x = zoom_y = zoom_base * scale
    if not config.get("match_scale", True):
        zoom_x = zoom_y = (zoom_base / 100.0) if use_percent else zoom_base

    zoom_x = max(0.001, min(zoom_x, 16.0))
    zoom_y = max(0.001, min(zoom_y, 16.0))

    if edit_priority_zoom(config):
        if warp is not None and should_apply_position_from_warp(config, warp, timeline_size):
            pan, tilt = translation_pixels_from_warp(warp, timeline_size, config)
        else:
            pan, tilt = 0.0, 0.0
    elif bool(config.get("match_position", True)):
        if bool(config.get("edit_pan_normalized", False)):
            pan, tilt = tx / width, ty / height
        else:
            pan, tilt = tx, ty
        if bool(config.get("invert_pan_x", False)):
            pan = -pan
        if bool(config.get("invert_tilt_y", True)):
            tilt = -tilt
    else:
        pan, tilt = 0.0, 0.0

    rot_out = rot if config.get("match_rotation", True) and not edit_priority_zoom(config) else 0.0
    if bool(config.get("invert_rotation", False)):
        rot_out = -rot_out

    max_rot = float(config.get("max_alignment_rotation_deg", 8.0))
    if abs(rot_out) > max_rot:
        rot_out = 0.0

    compose = compose_result_with_baseline(config) and baseline is not None
    if compose and baseline is not None:
        zoom_x, pan, tilt, rot_out = compose_with_baseline(
            zoom_x,
            pan,
            tilt,
            rot_out,
            baseline,
            compose=True,
        )
        zoom_y = zoom_x

    q = quantize_clip_edit(
        ClipEditTransform(
            zoom_x=zoom_x,
            zoom_y=zoom_y,
            pan=pan,
            tilt=tilt,
            rotation_deg=rot_out,
        ),
        config,
    )
    return ResolvedTransform(
        scale=scale,
        rotation_deg=q.rotation_deg,
        edit_zoom_x=q.zoom_x,
        edit_zoom_y=q.zoom_y,
        edit_pan=q.pan,
        edit_tilt=q.tilt,
        edit_rotation=q.rotation_deg,
    )


def baseline_from_result(result: ClipAnalysisResult) -> ClipEditTransform:
    return ClipEditTransform(
        zoom_x=result.baseline_zoom_x,
        zoom_y=result.baseline_zoom_y,
        pan=result.baseline_pan,
        tilt=result.baseline_tilt,
        rotation_deg=result.baseline_rotation,
    )
