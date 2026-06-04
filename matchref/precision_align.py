"""Maximum-precision alignment: pyramid ECC + Resolve Edit parameter search."""

from __future__ import annotations

from collections.abc import Callable

import cv2
import numpy as np

from matchref.clip_edit_transform import (
    ClipEditTransform,
    _fit_frame_to_canvas,
    apply_clip_edit_to_frame,
)
from matchref.config import AppConfig
from matchref.edit_quantize import edit_priority_zoom, quantize_clip_edit
from matchref.overlay_crop import content_crop_for_shape, overlay_margins_fraction
from matchref.refine_strategies import (
    RefineOutcome,
    refine_multi_strategy,
    use_multi_strategy_refine,
)
from matchref.reframe_detect import should_refine_position


def is_maximum_precision(config: AppConfig) -> bool:
    return str(config.get("alignment_precision", "normal")).lower() == "maximum"


def normalize_to_canvas(
    online: np.ndarray,
    offline: np.ndarray,
    canvas_size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Fit online to timeline raster; resize offline if needed."""
    width, height = int(canvas_size[0]), int(canvas_size[1])
    if width <= 0 or height <= 0:
        return online, offline
    on = online
    if on.shape[1] != width or on.shape[0] != height:
        on = _fit_frame_to_canvas(on, width, height)
    off = offline
    if off.shape[1] != width or off.shape[0] != height:
        off = cv2.resize(off, (width, height), interpolation=cv2.INTER_AREA)
    return on, off


def _gray_crop_mse(
    rendered: np.ndarray,
    reference: np.ndarray,
    config: AppConfig,
) -> float:
    margins = overlay_margins_fraction(config)
    g1 = cv2.cvtColor(rendered, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY).astype(np.float32)
    crop = content_crop_for_shape(g1.shape[0], g1.shape[1], margins)
    a = g1[crop.y0 : crop.y1, crop.x0 : crop.x1]
    b = g2[crop.y0 : crop.y1, crop.x0 : crop.x1]
    diff = a - b
    return float(np.mean(diff * diff))


def _ncc_score(rendered: np.ndarray, reference: np.ndarray, config: AppConfig) -> float:
    """1.0 = perfect match (for logging / quality)."""
    margins = overlay_margins_fraction(config)
    g1 = cv2.cvtColor(rendered, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY).astype(np.float32)
    crop = content_crop_for_shape(g1.shape[0], g1.shape[1], margins)
    a = g1[crop.y0 : crop.y1, crop.x0 : crop.x1]
    b = g2[crop.y0 : crop.y1, crop.x0 : crop.x1]
    a -= a.mean()
    b -= b.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-6:
        return 0.0
    return float(np.clip(np.sum(a * b) / denom, -1.0, 1.0))


def _render_with_edit(
    online_raw: np.ndarray,
    edit: ClipEditTransform,
    canvas_size: tuple[int, int],
    config: AppConfig,
) -> np.ndarray:
    return apply_clip_edit_to_frame(online_raw, edit, canvas_size, config)


def refine_resolve_edit(
    online_raw: np.ndarray,
    offline_ref: np.ndarray,
    *,
    canvas_size: tuple[int, int],
    config: AppConfig,
    initial: ClipEditTransform,
    warp: np.ndarray | None = None,
) -> RefineOutcome:
    """
    Direct search on Resolve Edit (Zoom/Pan/Tilt/Rotation) to minimize picture error.

    Uses the same render path as the Inspector simulation for 1:1 applicability.
    """
    width, height = int(canvas_size[0]), int(canvas_size[1])
    offline_fit = offline_ref
    if offline_fit.shape[1] != width or offline_fit.shape[0] != height:
        offline_fit = cv2.resize(offline_fit, (width, height), interpolation=cv2.INTER_AREA)

    def cost_full(z: float, px: float, py: float, rd: float) -> float:
        max_zoom = float(config.get("refine_zoom_max", 4.0))
        min_zoom = float(config.get("refine_zoom_min", 0.25))
        pan_lim = max(width, height) * float(config.get("refine_pan_limit_scale", 1.5))
        max_rot = float(config.get("max_alignment_rotation_deg", 3.0))
        use_rot = not bool(config.get("lock_alignment_rotation", False))
        edit = ClipEditTransform(
            zoom_x=max(min_zoom, min(max_zoom, z)),
            zoom_y=max(min_zoom, min(max_zoom, z)),
            pan=max(-pan_lim, min(pan_lim, px)),
            tilt=max(-pan_lim, min(pan_lim, py)),
            rotation_deg=max(-max_rot, min(max_rot, rd)) if use_rot else 0.0,
        )
        try:
            rendered = _render_with_edit(online_raw, edit, canvas_size, config)
            return _gray_crop_mse(rendered, offline_fit, config)
        except Exception:
            return 1e12

    if use_multi_strategy_refine(config):
        return refine_multi_strategy(
            online_raw=online_raw,
            offline_fit=offline_fit,
            canvas_size=canvas_size,
            config=config,
            initial=initial,
            cost_fn=lambda p: cost_full(p[0], p[1], p[2], p[3]),
            score_fn=lambda img: _ncc_score(img, offline_fit, config),
        )

    zoom = float(initial.zoom_x)
    pan = float(initial.pan)
    tilt = float(initial.tilt)
    rot = float(initial.rotation_deg)

    max_zoom = float(config.get("refine_zoom_max", 4.0))
    min_zoom = float(config.get("refine_zoom_min", 0.25))
    use_rot = not bool(config.get("lock_alignment_rotation", False))

    zoom_steps = config.get("refine_zoom_steps")
    if not isinstance(zoom_steps, list):
        zoom_steps = [0.03, 0.01, 0.002, 0.0005]
    pan_steps = config.get("refine_pan_steps")
    if not isinstance(pan_steps, list):
        pan_steps = [4.0, 1.0, 0.25]
    rot_steps = config.get("refine_rotation_steps")
    if not isinstance(rot_steps, list):
        rot_steps = [0.2, 0.05]

    priority_zoom = edit_priority_zoom(config)

    used_position = False
    strategy = "legacy"
    if priority_zoom:
        state = [zoom, 0.0, 0.0, 0.0]
        for z_step in zoom_steps:
            state = _coordinate_descent(
                state,
                lambda p: cost_full(p[0], p[1], p[2], p[3]),
                deltas=[z_step, 0.0, 0.0, 0.0],
            )
        zoom_edit = ClipEditTransform(
            zoom_x=state[0],
            zoom_y=state[0],
            pan=0.0,
            tilt=0.0,
            rotation_deg=0.0,
        )
        rendered_z = _render_with_edit(online_raw, zoom_edit, canvas_size, config)
        ncc_z = _ncc_score(rendered_z, offline_fit, config)

        if should_refine_position(
            config=config,
            initial=initial,
            warp=warp,
            timeline_size=canvas_size,
            ncc_after_zoom=ncc_z,
        ):
            used_position = True
            state[1] = float(initial.pan)
            state[2] = float(initial.tilt)
            for p_step in pan_steps:
                state = _coordinate_descent(
                    state,
                    lambda p: cost_full(p[0], p[1], p[2], p[3]),
                    deltas=[0.0, p_step, p_step, 0.0],
                )
            if use_rot and bool(config.get("refine_rotation_after_zoom", False)):
                for r_step in rot_steps:
                    state = _coordinate_descent(
                        state,
                        lambda p: cost_full(p[0], p[1], p[2], p[3]),
                        deltas=[0.0, 0.0, 0.0, r_step],
                    )
    else:
        used_position = True
        strategy = "legacy-all"
        state = [zoom, pan, tilt, rot]
        pad = max(len(zoom_steps), len(pan_steps), len(rot_steps))
        z_steps = zoom_steps + [zoom_steps[-1]] * (pad - len(zoom_steps))
        p_steps = pan_steps + [pan_steps[-1]] * (pad - len(pan_steps))
        r_steps = rot_steps + [rot_steps[-1]] * (pad - len(rot_steps))
        for z_step, p_step, r_step in zip(z_steps, p_steps, r_steps, strict=True):
            state = _coordinate_descent(
                state,
                lambda p: cost_full(p[0], p[1], p[2], p[3]),
                deltas=[z_step, p_step, p_step, r_step if use_rot else 0.0],
            )

    final = quantize_clip_edit(
        ClipEditTransform(
            zoom_x=max(min_zoom, min(max_zoom, state[0])),
            zoom_y=max(min_zoom, min(max_zoom, state[0])),
            pan=state[1],
            tilt=state[2],
            rotation_deg=state[3] if use_rot else 0.0,
        ),
        config,
    )
    rendered = _render_with_edit(online_raw, final, canvas_size, config)
    score = _ncc_score(rendered, offline_fit, config)
    return RefineOutcome(
        edit=final,
        ncc=score,
        used_position=used_position,
        strategy=strategy,
    )


def _coordinate_descent(
    start: list[float],
    cost_fn: Callable[[list[float]], float],
    *,
    deltas: list[float],
) -> list[float]:
    x = list(start)
    best = cost_fn(x)
    for i, delta in enumerate(deltas):
        if delta <= 0:
            continue
        improved = True
        while improved:
            improved = False
            for sign in (-1.0, 1.0):
                trial = list(x)
                trial[i] += sign * delta
                c = cost_fn(trial)
                if c < best - 1e-9:
                    x = trial
                    best = c
                    improved = True
    return x


def upscale_warp_translation(warp: np.ndarray, scale: float) -> np.ndarray:
    out = np.array(warp, dtype=np.float32, copy=True)
    if out.shape == (2, 3):
        out[0, 2] *= scale
        out[1, 2] *= scale
    return out


def refine_warp_phase_correlation(
    offline_u8: np.ndarray,
    online_u8: np.ndarray,
    warp: np.ndarray,
) -> np.ndarray:
    """Sub-pixel translation polish on top of ECC warp."""
    h, w = online_u8.shape[:2]
    warped = cv2.warpAffine(
        online_u8.astype(np.float32),
        warp[:2, :],
        (w, h),
        flags=cv2.INTER_LINEAR,
    )
    shift, _ = cv2.phaseCorrelate(
        offline_u8.astype(np.float32),
        warped.astype(np.float32),
    )
    dx, dy = float(shift[0]), float(shift[1])
    if abs(dx) < 1e-4 and abs(dy) < 1e-4:
        return warp
    patch = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float32)
    return patch @ np.vstack([warp[:2, :], [0.0, 0.0, 1.0]])[:2, :]


def initial_edit_from_warp(
    warp: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    baseline: ClipEditTransform | None,
) -> ClipEditTransform:
    from matchref.models import SamplePoint, TransformSample
    from matchref.transform_convert import resolve_transform

    width, height = canvas_size
    sample = TransformSample(
        sample_point=SamplePoint.MID,
        clip_local_frame=0,
        timeline_frame=0,
        warp_matrix=warp,
    )
    resolved = resolve_transform(sample, config, canvas_size, baseline=baseline)
    return ClipEditTransform(
        zoom_x=resolved.edit_zoom_x,
        zoom_y=resolved.edit_zoom_y,
        pan=resolved.edit_pan,
        tilt=resolved.edit_tilt,
        rotation_deg=resolved.edit_rotation,
    )
