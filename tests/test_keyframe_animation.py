"""Animated (keyframed) reframe detection: ramp -> keyframe, fluke -> reject."""

from matchref.config import AppConfig
from matchref.match_quality import clip_motion_profile
from matchref.models import SamplePoint, TransformSample


def _s(point, frame, scale=1.0, px=0.0, py=0.0):
    return TransformSample(
        sample_point=point,
        clip_local_frame=frame,
        timeline_frame=frame,
        scale=scale,
        position_x=px,
        position_y=py,
        ok=True,
    )


def test_static_clip_not_animated() -> None:
    cfg = AppConfig()
    s = [
        _s(SamplePoint.START, 0, 1.12),
        _s(SamplePoint.MID, 50, 1.121),
        _s(SamplePoint.END, 100, 1.119),
    ]
    varies, animated = clip_motion_profile(s, cfg)
    assert not varies and not animated


def test_zoom_ramp_is_animated() -> None:
    cfg = AppConfig()
    s = [
        _s(SamplePoint.START, 0, 1.05),
        _s(SamplePoint.MID, 50, 1.20),
        _s(SamplePoint.END, 100, 1.40),
    ]
    varies, animated = clip_motion_profile(s, cfg)
    assert varies and animated


def test_pan_ramp_is_animated() -> None:
    cfg = AppConfig()
    s = [
        _s(SamplePoint.START, 0, 1.1, px=-0.10),
        _s(SamplePoint.MID, 50, 1.1, px=0.0),
        _s(SamplePoint.END, 100, 1.1, px=0.12),
    ]
    varies, animated = clip_motion_profile(s, cfg)
    assert varies and animated


def test_mid_outlier_is_not_animated() -> None:
    """Big disagreement where mid is an outlier (not a ramp) -> noise, reject."""
    cfg = AppConfig()
    s = [
        _s(SamplePoint.START, 0, 1.10),
        _s(SamplePoint.MID, 50, 1.60),
        _s(SamplePoint.END, 100, 1.12),
    ]
    varies, animated = clip_motion_profile(s, cfg)
    assert varies and not animated
