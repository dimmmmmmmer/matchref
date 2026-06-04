"""Read and simulate Resolve Edit Inspector transforms on frames."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from matchref.config import AppConfig


@dataclass
class ClipEditTransform:
    zoom_x: float = 1.0
    zoom_y: float = 1.0
    pan: float = 0.0
    tilt: float = 0.0
    rotation_deg: float = 0.0

    @property
    def zoom(self) -> float:
        return (self.zoom_x + self.zoom_y) * 0.5


def _get_property(item: Any, key: str, default: float = 0.0) -> float:
    try:
        getter = getattr(item, "GetProperty", None)
        if not callable(getter):
            return default
        value = getter(key)
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def read_clip_edit_transform(timeline_item: Any) -> ClipEditTransform:
    """Read current Edit page transform from a timeline clip."""
    zoom_x = _get_property(timeline_item, "ZoomX", 1.0)
    zoom_y = _get_property(timeline_item, "ZoomY", zoom_x)
    if zoom_x <= 0:
        zoom_x = 1.0
    if zoom_y <= 0:
        zoom_y = zoom_x
    return ClipEditTransform(
        zoom_x=zoom_x,
        zoom_y=zoom_y,
        pan=_get_property(timeline_item, "Pan", 0.0),
        tilt=_get_property(timeline_item, "Tilt", 0.0),
        rotation_deg=_get_property(timeline_item, "RotationAngle", 0.0),
    )


def _fit_frame_to_canvas(frame: np.ndarray, width: int, height: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if w <= 0 or h <= 0:
        raise ValueError("empty frame")
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    scale = min(width / w, height / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)
    x0 = (width - new_w) // 2
    y0 = (height - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def _edit_warp_matrix(
    zoom: float,
    pan: float,
    tilt: float,
    rotation_deg: float,
    width: int,
    height: int,
    *,
    invert_tilt_y: bool,
) -> np.ndarray:
    cx, cy = width * 0.5, height * 0.5
    tilt_px = -tilt if invert_tilt_y else tilt
    angle = math.radians(rotation_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    s = max(float(zoom), 0.001)
    a = cos_a * s
    b = -sin_a * s
    c = sin_a * s
    d = cos_a * s
    tx = cx - a * cx - b * cy + pan
    ty = cy - c * cx - d * cy + tilt_px
    return np.array([[a, b, tx], [c, d, ty]], dtype=np.float32)


def should_simulate_edit_for_match(
    edit: ClipEditTransform,
    canvas_size: tuple[int, int],
    config: AppConfig,
) -> bool:
    """Pre-warp online only when Edit values are in a sane range for OpenCV sim."""
    if not bool(config.get("compensate_clip_transform", False)):
        return False
    width, height = int(canvas_size[0]), int(canvas_size[1])
    if width <= 0 or height <= 0:
        return False
    limit = max(width, height) * 1.25
    if abs(edit.pan) > limit or abs(edit.tilt) > limit:
        return False
    if edit.zoom > 8.0 or edit.zoom < 0.05:
        return False
    return True


def apply_clip_edit_to_frame(
    frame: np.ndarray,
    edit: ClipEditTransform,
    canvas_size: tuple[int, int],
    config: AppConfig,
) -> np.ndarray:
    """
    Approximate how Resolve shows the clip: fit to timeline, then Edit transform.
    """
    width, height = int(canvas_size[0]), int(canvas_size[1])
    if width <= 0 or height <= 0:
        return frame
    canvas = _fit_frame_to_canvas(frame, width, height)
    if (
        abs(edit.zoom - 1.0) < 1e-4
        and abs(edit.pan) < 1e-3
        and abs(edit.tilt) < 1e-3
        and abs(edit.rotation_deg) < 1e-3
    ):
        return canvas
    matrix = _edit_warp_matrix(
        edit.zoom,
        edit.pan,
        edit.tilt,
        edit.rotation_deg,
        width,
        height,
        invert_tilt_y=bool(config.get("invert_tilt_y", True)),
    )
    return cv2.warpAffine(
        canvas,
        matrix,
        (width, height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )


def compose_with_baseline(
    resolved_zoom: float,
    resolved_pan: float,
    resolved_tilt: float,
    resolved_rotation: float,
    baseline: ClipEditTransform,
    *,
    compose: bool,
) -> tuple[float, float, float, float]:
    """Turn alignment delta (on pre-warped view) into absolute Edit Inspector values."""
    if not compose:
        return resolved_zoom, resolved_pan, resolved_tilt, resolved_rotation
    zoom = baseline.zoom_x * resolved_zoom
    pan = baseline.pan + resolved_pan
    tilt = baseline.tilt + resolved_tilt
    rotation = baseline.rotation_deg + resolved_rotation
    return zoom, pan, tilt, rotation
