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
    mode: str = "fit",
) -> tuple[np.ndarray, np.ndarray]:
    """Fit online to timeline raster; resize offline if needed."""
    width, height = int(canvas_size[0]), int(canvas_size[1])
    if width <= 0 or height <= 0:
        return online, offline
    on = online
    if on.shape[1] != width or on.shape[0] != height:
        on = _fit_frame_to_canvas(on, width, height, mode)
    off = offline
    if off.shape[1] != width or off.shape[0] != height:
        off = cv2.resize(off, (width, height), interpolation=cv2.INTER_AREA)
    return on, off


def _content_gray(image: np.ndarray, config: AppConfig) -> np.ndarray:
    margins = overlay_margins_fraction(config)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    crop = content_crop_for_shape(gray.shape[0], gray.shape[1], margins)
    return gray[crop.y0 : crop.y1, crop.x0 : crop.x1]


def _gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude — invariant to grade/exposure, sharp at edges."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def _zero_mean_norm(arr: np.ndarray) -> tuple[np.ndarray, float]:
    centered = arr - float(arr.mean())
    return centered, float(np.linalg.norm(centered))


def _ncc_against(feature: np.ndarray, ref_centered: np.ndarray, ref_norm: float) -> float:
    a, a_norm = _zero_mean_norm(feature)
    denom = a_norm * ref_norm
    if denom < 1e-6:
        return 0.0
    return float(np.clip(float(np.sum(a * ref_centered)) / denom, -1.0, 1.0))


class _Reference:
    """
    Pre-computed offline features so each cost evaluation only processes the render.

    Holds both an intensity and a gradient-domain view of the (fixed) offline frame.
    Gradient NCC is robust to the grade difference between raw online media and the
    colour-graded lock-cut, and falls off sharply with misalignment (precise fit);
    intensity NCC keeps ungraded clips scoring high. ``score`` blends them per
    ``refine_score_metric``; ``cost`` drives the search per ``refine_cost_metric``.
    """

    def __init__(self, reference: np.ndarray, config: AppConfig) -> None:
        gray = _content_gray(reference, config)
        self._intensity = _zero_mean_norm(gray)
        self._gradient = _zero_mean_norm(_gradient_magnitude(gray))
        self._config = config

    def intensity_ncc(self, rendered: np.ndarray) -> float:
        return _ncc_against(_content_gray(rendered, self._config), *self._intensity)

    def gradient_ncc(self, rendered: np.ndarray) -> float:
        feat = _gradient_magnitude(_content_gray(rendered, self._config))
        return _ncc_against(feat, *self._gradient)

    def score(self, rendered: np.ndarray) -> float:
        metric = str(self._config.get("refine_score_metric", "max")).lower()
        if metric == "intensity":
            return self.intensity_ncc(rendered)
        if metric == "gradient":
            return self.gradient_ncc(rendered)
        return max(self.intensity_ncc(rendered), self.gradient_ncc(rendered))

    def cost(self, rendered: np.ndarray) -> float:
        metric = str(self._config.get("refine_cost_metric", "blend")).lower()
        if metric == "intensity":
            return 1.0 - self.intensity_ncc(rendered)
        if metric == "blend":
            # Weight toward the gradient term: its sharp peak fixes zoom precisely,
            # while a little intensity keeps pan/tilt smooth and survives non-rigid
            # content (fire/smoke) where gradient alone is unreliable.
            wg = float(self._config.get("refine_blend_gradient_weight", 0.5))
            wg = min(1.0, max(0.0, wg))
            return 1.0 - (wg * self.gradient_ncc(rendered) + (1.0 - wg) * self.intensity_ncc(rendered))
        return 1.0 - self.gradient_ncc(rendered)


def _ncc_score(rendered: np.ndarray, reference: np.ndarray, config: AppConfig) -> float:
    """Back-compat: report match quality (default blends intensity + gradient)."""
    return _Reference(reference, config).score(rendered)


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

    Coarse-to-fine: the search runs on a resolution-capped canvas. At full timeline
    resolution the picture error landscape is riddled with spurious minima (repeated
    edges, fine detail) and the optimiser wanders hundreds of pixels away; a
    downscaled canvas gives a smooth, robust landscape — and is several times faster.
    Zoom/rotation are scale-invariant; Pan/Tilt are scaled back to full canvas.
    """
    width, height = int(canvas_size[0]), int(canvas_size[1])
    refine_w = int(config.get("refine_max_width", 1920))
    if refine_w > 0 and width > refine_w:
        scale = refine_w / float(width)
        rw, rh = max(2, int(round(width * scale))), max(2, int(round(height * scale)))
        # Only shrink the *canvas* — pass the raw frames through so _fit_frame_to_canvas
        # letterboxes them aspect-correctly at the reduced size (pre-resizing online_raw
        # to the canvas would stretch a non-2:1 source and wreck the match).
        seed = ClipEditTransform(
            zoom_x=initial.zoom_x,
            zoom_y=initial.zoom_y,
            pan=initial.pan * scale,
            tilt=initial.tilt * scale,
            rotation_deg=initial.rotation_deg,
        )
        coarse = _refine_at_canvas(online_raw, offline_ref, (rw, rh), config, seed, warp)
        edit = coarse.edit
        upscaled = ClipEditTransform(
            zoom_x=edit.zoom_x,
            zoom_y=edit.zoom_y,
            pan=edit.pan / scale,
            tilt=edit.tilt / scale,
            rotation_deg=edit.rotation_deg,
        )
        if bool(config.get("refine_fine_polish", True)):
            return _fine_polish(
                online_raw, offline_ref, canvas_size, config, upscaled, coarse.strategy
            )
        return RefineOutcome(
            edit=quantize_clip_edit(upscaled, config),
            ncc=coarse.ncc,
            used_position=coarse.used_position,
            strategy=coarse.strategy,
        )
    return _refine_at_canvas(online_raw, offline_ref, canvas_size, config, initial, warp)


def _fine_polish(
    online_raw: np.ndarray,
    offline_ref: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    start: ClipEditTransform,
    strategy: str,
) -> RefineOutcome:
    """
    Sub-pixel polish at full resolution, seeded by the coarse result.

    Starting from the (already near-optimal) coarse estimate and using only small
    steps keeps the search inside the correct basin — it cannot wander to a far
    spurious minimum the way a full-resolution search from scratch does — while
    recovering the ~1-2px quantisation lost to the downscaled pass.
    """
    width, height = int(canvas_size[0]), int(canvas_size[1])
    offline_fit = offline_ref
    if offline_fit.shape[1] != width or offline_fit.shape[0] != height:
        offline_fit = cv2.resize(offline_ref, (width, height), interpolation=cv2.INTER_AREA)
    ref = _Reference(offline_fit, config)
    use_rot = not bool(config.get("lock_alignment_rotation", False))

    # When the seed already correlates well in the gradient domain the content is
    # rigid with clear structure (e.g. A031), so polish on the gradient alone — its
    # sharp peak locks zoom to the exact value (1.124, not the ~1.122 the broad
    # intensity term settles for). When gradient is weak (non-rigid fire/smoke) keep
    # the configured blend so position stays stable.
    seed_render = _render_with_edit(
        online_raw,
        ClipEditTransform(
            start.zoom_x, start.zoom_y, start.pan, start.tilt, start.rotation_deg
        ),
        canvas_size,
        config,
    )
    gradient_lock = ref.gradient_ncc(seed_render) >= float(
        config.get("refine_fine_gradient_lock_threshold", 0.6)
    )

    def cost(p: list[float]) -> float:
        z, px, py, rd = p
        edit = ClipEditTransform(z, z, px, py, rd if use_rot else 0.0)
        try:
            rendered = _render_with_edit(online_raw, edit, canvas_size, config)
            return (1.0 - ref.gradient_ncc(rendered)) if gradient_lock else ref.cost(rendered)
        except Exception:
            return 1e12

    state = [float(start.zoom_x), float(start.pan), float(start.tilt), float(start.rotation_deg)]
    fine_steps = config.get("refine_fine_steps")
    if not isinstance(fine_steps, list):
        # (zoom, pan/tilt px, rotation deg) — small enough to stay in the basin.
        fine_steps = [[0.004, 2.0, 0.1], [0.001, 0.5, 0.03], [0.0003, 0.25, 0.0]]
    for z_step, p_step, r_step in fine_steps:
        state = _coordinate_descent(
            state, cost, deltas=[z_step, p_step, p_step, r_step if use_rot else 0.0]
        )

    final = quantize_clip_edit(
        ClipEditTransform(state[0], state[0], state[1], state[2], state[3] if use_rot else 0.0),
        config,
    )
    score = ref.score(_render_with_edit(online_raw, final, canvas_size, config))
    used_position = abs(final.pan) >= float(config.get("snap_pan_tilt_below_pixels", 2.0)) or abs(
        final.tilt
    ) >= float(config.get("snap_pan_tilt_below_pixels", 2.0))
    return RefineOutcome(edit=final, ncc=score, used_position=used_position, strategy=f"{strategy}+fine")


def _refine_at_canvas(
    online_raw: np.ndarray,
    offline_ref: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    initial: ClipEditTransform,
    warp: np.ndarray | None = None,
) -> RefineOutcome:
    width, height = int(canvas_size[0]), int(canvas_size[1])
    offline_fit = offline_ref
    if offline_fit.shape[1] != width or offline_fit.shape[0] != height:
        offline_fit = cv2.resize(offline_fit, (width, height), interpolation=cv2.INTER_AREA)

    ref = _Reference(offline_fit, config)

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
            return ref.cost(rendered)
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
            score_fn=ref.score,
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
        ncc_z = ref.score(rendered_z)

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
    score = ref.score(rendered)
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
