"""Clip sample point selection."""

from matchref.config import AppConfig
from matchref.frame_provider import sample_points_for_clip
from matchref.models import SamplePoint


def test_single_mid() -> None:
    cfg = AppConfig()
    cfg.set("match_sample_mode", "single")
    cfg.set("match_sample_point", "mid")
    pts = sample_points_for_clip(76, config=cfg)
    assert len(pts) == 1
    assert pts[0] == (SamplePoint.MID, 38)


def test_three_points() -> None:
    cfg = AppConfig()
    cfg.set("match_sample_mode", "three")
    pts = sample_points_for_clip(76, config=cfg)
    assert len(pts) == 3
