"""Perspective CornerPin apply gating + spec (pure parts)."""

from __future__ import annotations

import numpy as np

from matchref.config import AppConfig
from matchref.cornerpin_apply import cornerpin_for_result, should_use_cornerpin
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample

SIZE = (1920, 1080)


def _result(*, with_homography: bool) -> ClipAnalysisResult:
    r = ClipAnalysisResult(
        clip_name="c", timeline_item_id="c", track_type="video",
        track_index=1, duration_frames=100,
    )
    s = TransformSample(sample_point=SamplePoint.MID, clip_local_frame=50, timeline_frame=50)
    s.ok = True
    s.ecc_score = 0.9
    if with_homography:
        s.homography = np.eye(3)
    r.samples.append(s)
    return r


def test_not_used_when_flag_off() -> None:
    assert should_use_cornerpin(_result(with_homography=True), AppConfig()) is False


def test_used_when_enabled_and_homography_present() -> None:
    cfg = AppConfig()
    cfg.set("perspective_match_enabled", True)
    assert should_use_cornerpin(_result(with_homography=True), cfg) is True


def test_not_used_without_homography() -> None:
    cfg = AppConfig()
    cfg.set("perspective_match_enabled", True)
    assert should_use_cornerpin(_result(with_homography=False), cfg) is False


def test_cornerpin_for_result_identity() -> None:
    cp = cornerpin_for_result(_result(with_homography=True), SIZE)
    assert cp is not None
    assert cp.top_left == (0.0, 1.0)
    assert cp.bottom_right == (1.0, 0.0)


def test_cornerpin_for_result_none_without_homography() -> None:
    assert cornerpin_for_result(_result(with_homography=False), SIZE) is None
