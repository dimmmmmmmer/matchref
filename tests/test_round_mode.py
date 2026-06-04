"""Round-up Inspector quantization (never under-cover the frame)."""

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.edit_quantize import _round_to, quantize_clip_edit


def test_round_to_up_zoom() -> None:
    assert _round_to(1.1243, 3, "up") == 1.125
    assert _round_to(1.1250, 3, "up") == 1.125  # exact stays
    assert _round_to(1.0001, 3, "up") == 1.001


def test_round_to_up_is_away_from_zero() -> None:
    assert _round_to(-8.31, 1, "up") == -8.4  # bigger magnitude
    assert _round_to(8.31, 1, "up") == 8.4


def test_round_to_nearest_unchanged() -> None:
    assert _round_to(1.1243, 3, "nearest") == 1.124
    assert _round_to(8.34, 1, "nearest") == 8.3


def test_quantize_up_mode() -> None:
    cfg = AppConfig()
    cfg.set("edit_round_mode", "up")
    cfg.set("edit_zoom_decimal_places", 3)
    q = quantize_clip_edit(ClipEditTransform(zoom_x=1.1243, zoom_y=1.1243), cfg)
    assert q.zoom_x == 1.125


def test_up_mode_does_not_resurrect_snapped_pan() -> None:
    cfg = AppConfig()
    cfg.set("edit_round_mode", "up")
    cfg.set("edit_priority_zoom", True)
    cfg.set("snap_pan_tilt_below_pixels", 2.0)
    q = quantize_clip_edit(ClipEditTransform(zoom_x=1.2, pan=0.4, tilt=-0.3), cfg)
    assert q.pan == 0.0 and q.tilt == 0.0
