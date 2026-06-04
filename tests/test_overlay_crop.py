"""Overlay margin cropping."""

from matchref.config import AppConfig
from matchref.overlay_crop import content_crop_for_shape, overlay_margins_fraction


def test_default_margins_crop_center() -> None:
    cfg = AppConfig()
    margins = overlay_margins_fraction(cfg)
    crop = content_crop_for_shape(1000, 2000, margins)
    assert crop.y0 >= 80
    assert crop.y1 <= 880
    assert crop.x0 >= 100
