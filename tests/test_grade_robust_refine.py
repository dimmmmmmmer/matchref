"""Refine must recover geometry precisely even when the reference is colour-graded."""

import cv2
import numpy as np

from matchref.clip_edit_transform import ClipEditTransform, apply_clip_edit_to_frame
from matchref.config import AppConfig
from matchref.precision_align import _Reference, refine_resolve_edit

_CANVAS = (384, 216)


def _textured_frame() -> np.ndarray:
    """Dense, well-localized edges so gradient matching is well constrained."""
    rng = np.random.default_rng(11)
    img = (rng.random((_CANVAS[1], _CANVAS[0], 3)) * 60 + 20).astype(np.uint8)
    for _ in range(18):
        x, y = int(rng.integers(0, 360)), int(rng.integers(0, 190))
        color = tuple(int(v) for v in rng.integers(40, 255, 3))
        cv2.rectangle(img, (x, y), (x + 30, y + 30), color, -1)
    for _ in range(10):
        p0 = (int(rng.integers(0, 384)), int(rng.integers(0, 216)))
        p1 = (int(rng.integers(0, 384)), int(rng.integers(0, 216)))
        cv2.line(img, p0, p1, (255, 255, 255), 1)
    return img


def _grade(img: np.ndarray) -> np.ndarray:
    """Strong non-linear orange grade + saturation, like a colourist's lock-cut."""
    x = (img.astype(np.float32) / 255.0) ** 0.6
    x[..., 2] *= 1.35  # B,G,R order: push red
    x[..., 0] *= 0.7  # pull blue
    x = np.clip(x, 0.0, 1.0)
    hsv = cv2.cvtColor((x * 255).astype(np.uint8), cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * 1.4, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _config() -> AppConfig:
    cfg = AppConfig()
    cfg.set("refine_strategy_orders", [["zoom", "position"], ["position", "zoom"]])
    cfg.set("lock_alignment_rotation", True)
    cfg.set("refine_early_exit_on_quality", False)
    return cfg


def test_gradient_ncc_is_grade_invariant_and_sharp() -> None:
    cfg = _config()
    frame = _textured_frame()
    ref = _Reference(_grade(frame), cfg)
    # Same geometry, different grade → high; a 25px shift collapses it.
    assert ref.gradient_ncc(frame) > 0.9
    shifted = cv2.warpAffine(frame, np.float32([[1, 0, 25], [0, 1, 0]]), _CANVAS)
    assert ref.gradient_ncc(shifted) < 0.5


def test_refine_recovers_transform_under_grade() -> None:
    cfg = _config()
    online = _textured_frame()
    known = ClipEditTransform(zoom_x=1.2, zoom_y=1.2, pan=18.0, tilt=-12.0)
    offline = _grade(apply_clip_edit_to_frame(online, known, _CANVAS, cfg))

    # Seeded near the answer, as the ECC stage seeds it in production.
    outcome = refine_resolve_edit(
        online,
        offline,
        canvas_size=_CANVAS,
        config=cfg,
        initial=ClipEditTransform(zoom_x=1.1, zoom_y=1.1, pan=10.0, tilt=-7.0),
    )

    assert abs(outcome.edit.zoom_x - 1.2) < 0.02
    assert abs(outcome.edit.pan - 18.0) < 1.5
    assert abs(outcome.edit.tilt + 12.0) < 1.5
    assert outcome.ncc > 0.95
