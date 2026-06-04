"""Reframe auto-detection."""

import numpy as np

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.reframe_detect import ecc_suggests_reframe, should_refine_position


def test_large_translation_triggers_reframe() -> None:
    cfg = AppConfig()
    warp = np.array([[1.0, 0.0, 80.0], [0.0, 1.0, -40.0]], dtype=np.float32)
    assert ecc_suggests_reframe(warp, (3840, 1920), cfg)


def test_good_ncc_zoom_only_skips_position() -> None:
    cfg = AppConfig()
    cfg.set("auto_reframe_translation_pixels", 50.0)
    initial = ClipEditTransform(zoom_x=1.0, pan=0.0, tilt=0.0)
    assert not should_refine_position(
        config=cfg,
        initial=initial,
        warp=None,
        timeline_size=(3840, 1920),
        ncc_after_zoom=0.95,
    )
