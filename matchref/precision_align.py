"""Maximum-precision alignment: pyramid ECC + Resolve Edit parameter search."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

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


def _auto_highlight_clip(reference_gray: np.ndarray, config: AppConfig) -> float:
    """
    Clip level for highlight-dominated dark shots (fire/specular flicker).

    Such shots have a small, very bright, high-variance region on a dark base that
    hijacks the correlation and pulls position off the static structure. Clipping
    highlights lets the rigid dark structure drive the match. Returns 0 (no clip)
    for normal/bright scenes. ``match_highlight_clip``: "auto" | 0/off | <number>.
    """
    setting = config.get("match_highlight_clip", "auto")
    if isinstance(setting, (int, float)):
        return float(setting)
    if str(setting).lower() in ("off", "none", "0", ""):
        return 0.0
    # auto: only dark-dominant scenes, clip relative to the median brightness.
    nonzero = reference_gray[reference_gray > 1.0]
    if nonzero.size == 0:
        return 0.0
    median = float(np.median(nonzero))
    dark_threshold = float(config.get("highlight_clip_dark_median", 48.0))
    if median >= dark_threshold:
        return 0.0
    clip = median * float(config.get("highlight_clip_median_mult", 5.0))
    return clip if clip < 255.0 else 0.0


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
        self._config = config
        gray = _content_gray(reference, config)
        # Clip derived once from the (stable, full-frame) offline reference and
        # applied identically to every rendered online frame, so both sides drop
        # the same flicker highlights.
        self._clip = _auto_highlight_clip(gray, config)
        gray = self._clipped(gray)
        self._intensity = _zero_mean_norm(gray)
        self._gradient = _zero_mean_norm(_gradient_magnitude(gray))

    def _clipped(self, gray: np.ndarray) -> np.ndarray:
        if self._clip > 0:
            return np.minimum(gray, self._clip)
        return gray

    def intensity_ncc(self, rendered: np.ndarray) -> float:
        return _ncc_against(self._clipped(_content_gray(rendered, self._config)), *self._intensity)

    def gradient_ncc(self, rendered: np.ndarray) -> float:
        feat = _gradient_magnitude(self._clipped(_content_gray(rendered, self._config)))
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
            return 1.0 - (
                wg * self.gradient_ncc(rendered) + (1.0 - wg) * self.intensity_ncc(rendered)
            )
        return 1.0 - self.gradient_ncc(rendered)


def _render_with_edit(
    online_raw: np.ndarray,
    edit: ClipEditTransform,
    canvas_size: tuple[int, int],
    config: AppConfig,
) -> np.ndarray:
    return apply_clip_edit_to_frame(online_raw, edit, canvas_size, config)


@dataclass
class _RefineJob:
    """The frame pair and canvas a refine search runs against."""

    online_raw: np.ndarray
    offline_ref: np.ndarray
    canvas_size: tuple[int, int]
    config: AppConfig


@dataclass
class _Steps:
    """Coordinate-descent step schedules per parameter group."""

    zoom: list[Any]
    pan: list[Any]
    rot: list[Any]


def _coarse_then_polish(
    job: _RefineJob,
    initial: ClipEditTransform,
    warp: np.ndarray | None,
    scale: float,
    should_cancel: Callable[[], bool] | None,
) -> RefineOutcome:
    """Search on a downscaled canvas, then polish (or return the upscaled coarse result)."""
    width, height = int(job.canvas_size[0]), int(job.canvas_size[1])
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
    coarse = _refine_at_canvas(
        job.online_raw,
        job.offline_ref,
        (rw, rh),
        job.config,
        seed,
        warp,
        should_cancel=should_cancel,
    )
    edit = coarse.edit
    upscaled = ClipEditTransform(
        zoom_x=edit.zoom_x,
        zoom_y=edit.zoom_y,
        pan=edit.pan / scale,
        tilt=edit.tilt / scale,
        rotation_deg=edit.rotation_deg,
    )
    if bool(job.config.get("refine_fine_polish", True)):
        return _fine_polish(
            job.online_raw,
            job.offline_ref,
            job.canvas_size,
            job.config,
            upscaled,
            coarse.strategy,
            should_cancel=should_cancel,
        )
    return RefineOutcome(
        edit=quantize_clip_edit(upscaled, job.config),
        ncc=coarse.ncc,
        used_position=coarse.used_position,
        strategy=coarse.strategy,
        gradient_ncc=coarse.gradient_ncc,
    )


def refine_resolve_edit(
    online_raw: np.ndarray,
    offline_ref: np.ndarray,
    *,
    canvas_size: tuple[int, int],
    config: AppConfig,
    initial: ClipEditTransform,
    warp: np.ndarray | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> RefineOutcome:
    """
    Direct search on Resolve Edit (Zoom/Pan/Tilt/Rotation) to minimize picture error.

    Coarse-to-fine: the search runs on a resolution-capped canvas. At full timeline
    resolution the picture error landscape is riddled with spurious minima (repeated
    edges, fine detail) and the optimiser wanders hundreds of pixels away; a
    downscaled canvas gives a smooth, robust landscape — and is several times faster.
    Zoom/rotation are scale-invariant; Pan/Tilt are scaled back to full canvas.
    """
    width = int(canvas_size[0])
    refine_w = int(config.get("refine_max_width", 1920))
    if refine_w > 0 and width > refine_w:
        job = _RefineJob(online_raw, offline_ref, canvas_size, config)
        return _coarse_then_polish(job, initial, warp, refine_w / float(width), should_cancel)

    # No coarse downscale (canvas <= refine_max_width). Still run the fine polish so
    # the gradient zoom-lock and the position-parsimony (drop unjustified Pan/Tilt)
    # apply at every timeline resolution — they used to be skipped on small canvases.
    direct = _refine_at_canvas(
        online_raw, offline_ref, canvas_size, config, initial, warp, should_cancel=should_cancel
    )
    if bool(config.get("refine_fine_polish", True)):
        return _fine_polish(
            online_raw,
            offline_ref,
            canvas_size,
            config,
            direct.edit,
            direct.strategy,
            should_cancel=should_cancel,
        )
    return direct


def _fine_polish(
    online_raw: np.ndarray,
    offline_ref: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    start: ClipEditTransform,
    strategy: str,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> RefineOutcome:
    """
    Sub-pixel polish at full resolution, seeded by the coarse result.

    Starting from the (already near-optimal) coarse estimate and using only small
    steps keeps the search inside the correct basin — it cannot wander to a far
    spurious minimum the way a full-resolution search from scratch does — while
    recovering the ~1-2px quantisation lost to the downscaled pass.
    """
    ref = _fitted_reference(offline_ref, canvas_size, config)
    use_rot = not bool(config.get("lock_alignment_rotation", False))

    def cost(p: list[float]) -> float:
        z, px, py, rd = p
        edit = ClipEditTransform(z, z, px, py, rd if use_rot else 0.0)
        try:
            return ref.cost(_render_with_edit(online_raw, edit, canvas_size, config))
        except Exception:
            return 1e12

    state = [float(start.zoom_x), float(start.pan), float(start.tilt), float(start.rotation_deg)]
    fine_steps = config.get("refine_fine_steps")
    if not isinstance(fine_steps, list):
        # (zoom, pan/tilt px, rotation deg) — small enough to stay in the basin.
        fine_steps = [[0.004, 2.0, 0.1], [0.001, 0.5, 0.03], [0.0003, 0.25, 0.0]]
    for z_step, p_step, r_step in fine_steps:
        if should_cancel is not None and should_cancel():
            break
        state = _coordinate_descent(
            state, cost, deltas=[z_step, p_step, p_step, r_step if use_rot else 0.0]
        )

    # Decide the gradient lock on the *aligned* result (not the raw seed, which is
    # still misaligned and scores low). When the aligned result correlates well in
    # the gradient domain the content is rigid with clear structure (e.g. A031), so
    # pin zoom to the sharp gradient peak — its exact value (1.124, vs the ~1.122 the
    # broad intensity term settles for). Non-rigid fire/smoke scores low → left as is.
    aligned_render = _render_state(state, online_raw, canvas_size, config, use_rot)
    if ref.gradient_ncc(aligned_render) >= float(
        config.get("refine_fine_gradient_lock_threshold", 0.6)
    ):
        _apply_gradient_zoom_lock(state, ref, online_raw, canvas_size, config, use_rot)

    _drop_unjustified_position(state, ref, online_raw, canvas_size, config, use_rot)

    final = quantize_clip_edit(
        ClipEditTransform(state[0], state[0], state[1], state[2], state[3] if use_rot else 0.0),
        config,
    )
    final_render = _render_with_edit(online_raw, final, canvas_size, config)
    score = ref.score(final_render)
    used_position = abs(final.pan) >= float(config.get("snap_pan_tilt_below_pixels", 2.0)) or abs(
        final.tilt
    ) >= float(config.get("snap_pan_tilt_below_pixels", 2.0))
    return RefineOutcome(
        edit=final,
        ncc=score,
        used_position=used_position,
        strategy=f"{strategy}+fine",
        gradient_ncc=ref.gradient_ncc(final_render),
    )


def _fitted_reference(
    offline_ref: np.ndarray, canvas_size: tuple[int, int], config: AppConfig
) -> _Reference:
    """Reference resized to the canvas for cost evaluation."""
    width, height = int(canvas_size[0]), int(canvas_size[1])
    if offline_ref.shape[1] != width or offline_ref.shape[0] != height:
        offline_ref = cv2.resize(offline_ref, (width, height), interpolation=cv2.INTER_AREA)
    return _Reference(offline_ref, config)


def _render_state(
    state: list[float],
    online_raw: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    use_rot: bool,
) -> np.ndarray:
    """Render the online frame with the search state's (zoom, pan, tilt, rot)."""
    return _render_with_edit(
        online_raw,
        ClipEditTransform(state[0], state[0], state[1], state[2], state[3] if use_rot else 0.0),
        canvas_size,
        config,
    )


def _apply_gradient_zoom_lock(
    state: list[float],
    ref: _Reference,
    online_raw: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    use_rot: bool,
) -> None:
    """Final zoom-only pass on the gradient with pan/tilt fixed (mutates ``state``)."""
    # The joint search lets position drag zoom a hair off (1.122), but the gradient
    # zoom peak is sharp — pinning zoom alone lands it exactly (1.124). Sub-pixel
    # residual pan/tilt (below the snap threshold, treated as 0 anyway) would shift
    # that peak, so snap them to 0 first.
    snap = float(config.get("snap_pan_tilt_below_pixels", 2.0))
    px = 0.0 if abs(state[1]) < snap else state[1]
    py = 0.0 if abs(state[2]) < snap else state[2]

    def zoom_cost(p: list[float]) -> float:
        edit = ClipEditTransform(p[0], p[0], px, py, state[3] if use_rot else 0.0)
        try:
            return 1.0 - ref.gradient_ncc(_render_with_edit(online_raw, edit, canvas_size, config))
        except Exception:
            return 1e12

    zstate = [state[0], 0.0, 0.0, 0.0]
    for z_step in (0.004, 0.001, 0.0003):
        zstate = _coordinate_descent(zstate, zoom_cost, deltas=[z_step, 0.0, 0.0, 0.0])
    state[0], state[1], state[2] = zstate[0], px, py


def _drop_unjustified_position(
    state: list[float],
    ref: _Reference,
    online_raw: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    use_rot: bool,
) -> None:
    """Position parsimony: zero Pan/Tilt unless it earns a real gradient-match gain."""
    # On low-structure clips (smoke/fire/graded) the position drifts chasing noise
    # while zoom stays right — a genuine reframe lifts the (grade-invariant) gradient
    # match a lot, a spurious one barely moves it (or makes it worse). Verified
    # across clips: real reframes gain +0.27…+0.56, drifts gain < 0.12 (one even -0.27).
    snap = float(config.get("snap_pan_tilt_below_pixels", 2.0))
    if not (abs(state[1]) >= snap or abs(state[2]) >= snap):
        return
    min_gain = float(config.get("refine_position_min_gain", 0.12))
    g_with = ref.gradient_ncc(_render_state(state, online_raw, canvas_size, config, use_rot))
    zero_state = [state[0], 0.0, 0.0, state[3]]
    g_zero = ref.gradient_ncc(_render_state(zero_state, online_raw, canvas_size, config, use_rot))
    if g_with - g_zero < min_gain:
        state[1] = state[2] = 0.0


def _refine_at_canvas(
    online_raw: np.ndarray,
    offline_ref: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    initial: ClipEditTransform,
    warp: np.ndarray | None = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
) -> RefineOutcome:
    job = _RefineJob(online_raw, offline_ref, canvas_size, config)
    ref = _fitted_reference(offline_ref, canvas_size, config)
    cost_full = _make_edit_cost(online_raw, canvas_size, config, ref)

    if use_multi_strategy_refine(config):
        return _refine_via_strategies(job, initial, cost_full, ref, should_cancel)

    use_rot = not bool(config.get("lock_alignment_rotation", False))
    steps = _Steps(
        zoom=_config_steps(config, "refine_zoom_steps", [0.03, 0.01, 0.002, 0.0005]),
        pan=_config_steps(config, "refine_pan_steps", [4.0, 1.0, 0.25]),
        rot=_config_steps(config, "refine_rotation_steps", [0.2, 0.05]),
    )

    if edit_priority_zoom(config):
        strategy = "legacy"
        state, used_position = _descend_priority_zoom(
            job, ref, initial, warp, cost_full, steps, use_rot
        )
    else:
        strategy = "legacy-all"
        used_position = True
        state = _descend_all_params(initial, cost_full, steps, use_rot)

    return _state_outcome(state, used_position, strategy, job, ref, use_rot)


def _refine_via_strategies(
    job: _RefineJob,
    initial: ClipEditTransform,
    cost_full: Callable[[float, float, float, float], float],
    ref: _Reference,
    should_cancel: Callable[[], bool] | None,
) -> RefineOutcome:
    """Delegate to the multi-strategy search; gradient score computed on its winner."""
    width, height = int(job.canvas_size[0]), int(job.canvas_size[1])
    offline_fit = job.offline_ref
    if offline_fit.shape[1] != width or offline_fit.shape[0] != height:
        offline_fit = cv2.resize(offline_fit, (width, height), interpolation=cv2.INTER_AREA)
    outcome = refine_multi_strategy(
        online_raw=job.online_raw,
        offline_fit=offline_fit,
        canvas_size=job.canvas_size,
        config=job.config,
        initial=initial,
        cost_fn=lambda p: cost_full(p[0], p[1], p[2], p[3]),
        score_fn=ref.score,
        should_cancel=should_cancel,
    )
    outcome.gradient_ncc = ref.gradient_ncc(
        _render_with_edit(job.online_raw, outcome.edit, job.canvas_size, job.config)
    )
    return outcome


def _state_outcome(
    state: list[float],
    used_position: bool,
    strategy: str,
    job: _RefineJob,
    ref: _Reference,
    use_rot: bool,
) -> RefineOutcome:
    """Quantize the search state into an Edit and score its render."""
    config = job.config
    max_zoom = float(config.get("refine_zoom_max", 4.0))
    min_zoom = float(config.get("refine_zoom_min", 0.25))
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
    rendered = _render_with_edit(job.online_raw, final, job.canvas_size, config)
    return RefineOutcome(
        edit=final,
        ncc=ref.score(rendered),
        used_position=used_position,
        strategy=strategy,
        gradient_ncc=ref.gradient_ncc(rendered),
    )


def _make_edit_cost(
    online_raw: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    ref: _Reference,
) -> Callable[[float, float, float, float], float]:
    """Bounded picture-error cost over (zoom, pan, tilt, rotation)."""
    width, height = int(canvas_size[0]), int(canvas_size[1])

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

    return cost_full


def _config_steps(config: AppConfig, key: str, default: list[Any]) -> list[Any]:
    """Step schedule from config, falling back to the tuned default."""
    steps = config.get(key)
    return steps if isinstance(steps, list) else default


def _descend_priority_zoom(
    job: _RefineJob,
    ref: _Reference,
    initial: ClipEditTransform,
    warp: np.ndarray | None,
    cost_full: Callable[[float, float, float, float], float],
    steps: _Steps,
    use_rot: bool,
) -> tuple[list[float], bool]:
    """Zoom-first descent; position (and optional rotation) only when justified."""
    state = [float(initial.zoom_x), 0.0, 0.0, 0.0]
    for z_step in steps.zoom:
        state = _coordinate_descent(
            state,
            lambda p: cost_full(p[0], p[1], p[2], p[3]),
            deltas=[z_step, 0.0, 0.0, 0.0],
        )
    zoom_edit = ClipEditTransform(
        zoom_x=state[0], zoom_y=state[0], pan=0.0, tilt=0.0, rotation_deg=0.0
    )
    ncc_z = ref.score(_render_with_edit(job.online_raw, zoom_edit, job.canvas_size, job.config))

    if not should_refine_position(
        config=job.config,
        initial=initial,
        warp=warp,
        timeline_size=job.canvas_size,
        ncc_after_zoom=ncc_z,
    ):
        return state, False

    state[1] = float(initial.pan)
    state[2] = float(initial.tilt)
    for p_step in steps.pan:
        state = _coordinate_descent(
            state,
            lambda p: cost_full(p[0], p[1], p[2], p[3]),
            deltas=[0.0, p_step, p_step, 0.0],
        )
    if use_rot and bool(job.config.get("refine_rotation_after_zoom", False)):
        for r_step in steps.rot:
            state = _coordinate_descent(
                state,
                lambda p: cost_full(p[0], p[1], p[2], p[3]),
                deltas=[0.0, 0.0, 0.0, r_step],
            )
    return state, True


def _descend_all_params(
    initial: ClipEditTransform,
    cost_full: Callable[[float, float, float, float], float],
    steps: _Steps,
    use_rot: bool,
) -> list[float]:
    """Joint descent over all parameters (legacy-all strategy)."""
    state = [
        float(initial.zoom_x),
        float(initial.pan),
        float(initial.tilt),
        float(initial.rotation_deg),
    ]
    pad = max(len(steps.zoom), len(steps.pan), len(steps.rot))
    z_steps = steps.zoom + [steps.zoom[-1]] * (pad - len(steps.zoom))
    p_steps = steps.pan + [steps.pan[-1]] * (pad - len(steps.pan))
    r_steps = steps.rot + [steps.rot[-1]] * (pad - len(steps.rot))
    for z_step, p_step, r_step in zip(z_steps, p_steps, r_steps, strict=True):
        state = _coordinate_descent(
            state,
            lambda p: cost_full(p[0], p[1], p[2], p[3]),
            deltas=[z_step, p_step, p_step, r_step if use_rot else 0.0],
        )
    return state


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
    # Compose the residual sub-pixel translation onto the ECC warp in homogeneous
    # 3x3 space, then drop back to 2x3. Both operands must be 3x3 for the matmul —
    # slicing to 2x3 before multiplying makes it a (2,3)@(2,3) shape error.
    patch = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]], dtype=np.float32)
    warp_h = np.vstack([warp[:2, :], [0.0, 0.0, 1.0]]).astype(np.float32)
    return (patch @ warp_h)[:2, :]


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
