"""Precision alignment helpers."""

import numpy as np

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.precision_align import is_maximum_precision, refine_resolve_edit


def test_refine_improves_constant_patch() -> None:
    cfg = AppConfig()
    cfg.set("alignment_precision", "maximum")
    cfg.set("refine_multi_strategy", False)
    cfg.set("match_ignore_overlay", False)
    cfg.set("refine_zoom_steps", [0.05, 0.01])
    cfg.set("refine_pan_steps", [10, 2])
    cfg.set("refine_rotation_steps", [0.0, 0.0])
    h, w = 240, 320
    rng = np.random.default_rng(0)
    patch = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
    online = np.zeros((h, w, 3), dtype=np.uint8)
    online[40:200, 50:270] = patch[40:200, 50:270]
    offline = online.copy()
    initial = ClipEditTransform(zoom_x=0.92, pan=30.0, tilt=-10.0)
    outcome = refine_resolve_edit(
        online,
        offline,
        canvas_size=(w, h),
        config=cfg,
        initial=initial,
    )
    assert outcome.ncc > 0.85
    assert abs(outcome.edit.zoom_x - 1.0) < 0.08
    assert abs(outcome.edit.pan) < 15
    assert abs(outcome.edit.tilt) < 15


def test_maximum_flag() -> None:
    cfg = AppConfig()
    cfg.set("alignment_precision", "maximum")
    assert is_maximum_precision(cfg)
