"""Try multiple Zoom / Position / Rotation refinement orders; keep the best."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from matchref.clip_edit_transform import ClipEditTransform, apply_clip_edit_to_frame
from matchref.config import AppConfig
from matchref.edit_quantize import quantize_clip_edit


def _refine_early_exit_enabled(config: AppConfig) -> bool:
    return bool(config.get("refine_early_exit_on_quality", True))


def _refine_ncc_passes(ncc: float, config: AppConfig) -> bool:
    return float(ncc) >= float(config.get("auto_reframe_ncc_threshold", 0.95))

Stage = str  # zoom | position | rotation

DEFAULT_STRATEGY_ORDERS: list[list[Stage]] = [
    ["zoom", "position", "rotation"],
    ["position", "zoom", "rotation"],
    ["rotation", "zoom", "position"],
    ["zoom", "rotation", "position"],
    ["position", "rotation", "zoom"],
    ["rotation", "position", "zoom"],
]


@dataclass
class RefineOutcome:
    edit: ClipEditTransform
    ncc: float
    used_position: bool
    strategy: str
    gradient_ncc: float = 0.0  # structural agreement; low = intensity-only false match


@dataclass
class _RefineContext:
    online_raw: np.ndarray
    offline_fit: np.ndarray
    canvas_size: tuple[int, int]
    config: AppConfig
    min_zoom: float
    max_zoom: float
    pan_lim: float
    tilt_lim: float
    max_rot: float
    use_rotation: bool
    zoom_steps: list[float]
    pan_steps: list[float]
    rot_steps: list[float]
    cost_fn: Callable[[list[float]], float]
    score_fn: Callable[[np.ndarray], float]


def strategy_orders_from_config(config: AppConfig) -> list[list[Stage]]:
    custom = config.get("refine_strategy_orders")
    if isinstance(custom, list) and custom and isinstance(custom[0], list):
        return [[str(s).lower() for s in order] for order in custom]
    return [list(order) for order in DEFAULT_STRATEGY_ORDERS]


def use_multi_strategy_refine(config: AppConfig) -> bool:
    return bool(config.get("refine_multi_strategy", True))


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


def _run_stage(state: list[float], stage: Stage, ctx: _RefineContext) -> list[float]:
    if stage == "zoom":
        for step in ctx.zoom_steps:
            state = _coordinate_descent(
                state, ctx.cost_fn, deltas=[step, 0.0, 0.0, 0.0]
            )
    elif stage == "position":
        for step in ctx.pan_steps:
            state = _coordinate_descent(
                state, ctx.cost_fn, deltas=[0.0, step, step, 0.0]
            )
    elif stage == "rotation" and ctx.use_rotation:
        for step in ctx.rot_steps:
            state = _coordinate_descent(
                state, ctx.cost_fn, deltas=[0.0, 0.0, 0.0, step]
            )
    return state


def _state_to_edit(state: list[float], ctx: _RefineContext) -> ClipEditTransform:
    z, px, py, rd = state
    return ClipEditTransform(
        zoom_x=max(ctx.min_zoom, min(ctx.max_zoom, z)),
        zoom_y=max(ctx.min_zoom, min(ctx.max_zoom, z)),
        pan=max(-ctx.pan_lim, min(ctx.pan_lim, px)),
        tilt=max(-ctx.tilt_lim, min(ctx.tilt_lim, py)),
        rotation_deg=max(-ctx.max_rot, min(ctx.max_rot, rd)) if ctx.use_rotation else 0.0,
    )


def _order_label(order: list[Stage]) -> str:
    return "→".join(order)


def refine_with_strategy_order(
    initial_state: list[float],
    order: list[Stage],
    ctx: _RefineContext,
) -> tuple[ClipEditTransform, float]:
    state = list(initial_state)
    # Repeat the stage sequence: zoom and position are coupled, so a single pass
    # leaves zoom stale after position moves (and vice-versa). Re-running until the
    # cost stops improving tightens the fit — this is what closes the "almost but
    # not quite" gap on reframed clips.
    passes = max(1, int(ctx.config.get("refine_stage_passes", 4)))
    prev_cost = ctx.cost_fn(state)
    for _ in range(passes):
        for stage in order:
            if stage not in ("zoom", "position", "rotation"):
                continue
            state = _run_stage(state, stage, ctx)
        cost = ctx.cost_fn(state)
        if prev_cost - cost < 1e-5:
            break
        prev_cost = cost
    edit = _state_to_edit(state, ctx)
    rendered = apply_clip_edit_to_frame(
        ctx.online_raw, edit, ctx.canvas_size, ctx.config
    )
    return edit, ctx.score_fn(rendered)


def refine_multi_strategy(
    *,
    online_raw: np.ndarray,
    offline_fit: np.ndarray,
    canvas_size: tuple[int, int],
    config: AppConfig,
    initial: ClipEditTransform,
    cost_fn: Callable[[list[float]], float],
    score_fn: Callable[[np.ndarray], float],
    should_cancel: Callable[[], bool] | None = None,
) -> RefineOutcome:
    width, height = int(canvas_size[0]), int(canvas_size[1])
    max_zoom = float(config.get("refine_zoom_max", 4.0))
    min_zoom = float(config.get("refine_zoom_min", 0.25))
    pan_lim = max(width, height) * float(config.get("refine_pan_limit_scale", 1.5))

    zoom_steps = config.get("refine_zoom_steps")
    if not isinstance(zoom_steps, list):
        zoom_steps = [0.03, 0.01, 0.002, 0.0005]
    pan_steps = config.get("refine_pan_steps")
    if not isinstance(pan_steps, list):
        pan_steps = [4.0, 1.0, 0.25]
    rot_steps = config.get("refine_rotation_steps")
    if not isinstance(rot_steps, list):
        rot_steps = [0.2, 0.05, 0.025]

    ctx = _RefineContext(
        online_raw=online_raw,
        offline_fit=offline_fit,
        canvas_size=canvas_size,
        config=config,
        min_zoom=min_zoom,
        max_zoom=max_zoom,
        pan_lim=pan_lim,
        tilt_lim=pan_lim,
        max_rot=float(config.get("max_alignment_rotation_deg", 3.0)),
        use_rotation=not bool(config.get("lock_alignment_rotation", False)),
        zoom_steps=[float(x) for x in zoom_steps],
        pan_steps=[float(x) for x in pan_steps],
        rot_steps=[float(x) for x in rot_steps],
        cost_fn=cost_fn,
        score_fn=score_fn,
    )

    start = [
        float(initial.zoom_x),
        float(initial.pan),
        float(initial.tilt),
        float(initial.rotation_deg),
    ]

    orders = strategy_orders_from_config(config)
    best_edit: ClipEditTransform | None = None
    best_ncc = -1.0
    best_label = _order_label(orders[0]) if orders else "zoom→position→rotation"

    early_exit = _refine_early_exit_enabled(config)
    for order in orders:
        if should_cancel is not None and should_cancel():
            break
        if not order:
            continue
        edit, ncc = refine_with_strategy_order(start, order, ctx)
        if ncc > best_ncc:
            best_ncc = ncc
            best_edit = edit
            best_label = _order_label(order)
        if early_exit and _refine_ncc_passes(ncc, config):
            break

    if best_edit is None:
        best_edit = initial
        best_ncc = 0.0

    final = quantize_clip_edit(best_edit, config)
    snap = float(config.get("snap_pan_tilt_below_pixels", 2.0))
    used_position = abs(final.pan) >= snap or abs(final.tilt) >= snap
    return RefineOutcome(
        edit=final,
        ncc=best_ncc,
        used_position=used_position,
        strategy=best_label,
    )
