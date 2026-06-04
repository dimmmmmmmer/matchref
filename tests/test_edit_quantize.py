"""Edit value quantization."""

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.edit_quantize import quantize_clip_edit


def test_snap_small_pan() -> None:
    cfg = AppConfig()
    cfg.set("edit_priority_zoom", True)
    cfg.set("snap_pan_tilt_below_pixels", 2.0)
    out = quantize_clip_edit(
        ClipEditTransform(zoom_x=1.23456789, pan=0.4, tilt=-1.1, rotation_deg=0.01),
        cfg,
    )
    assert out.zoom_x == 1.2346
    assert out.pan == 0.0
    assert out.tilt == 0.0
