"""Pure value math behind the Fusion apply prototype."""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.fusion_apply import (
    build_keyframe_spec,
    fusion_transform_values,
    should_use_fusion,
)
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample
from matchref.transform_convert import ResolvedTransform

SIZE = (1920, 1080)


def _resolved(pan=0.0, tilt=0.0, zoom=1.0, rot=0.0) -> ResolvedTransform:
    return ResolvedTransform(
        edit_zoom_x=zoom, edit_zoom_y=zoom, edit_pan=pan, edit_tilt=tilt, edit_rotation=rot
    )


def test_centered_zoom_maps_to_center_half() -> None:
    xf = fusion_transform_values(_resolved(zoom=1.25), SIZE)
    assert xf.center_x == 0.5
    assert xf.center_y == 0.5
    assert xf.size == 1.25
    assert xf.angle == 0.0


def test_pan_tilt_map_to_normalized_center() -> None:
    xf = fusion_transform_values(_resolved(pan=192.0, tilt=108.0), SIZE)
    assert abs(xf.center_x - 0.6) < 1e-9  # 192 / 1920
    assert abs(xf.center_y - 0.6) < 1e-9  # 108 / 1080


def _animated_result(frames: list[int]) -> ClipAnalysisResult:
    r = ClipAnalysisResult(
        clip_name="ramp",
        timeline_item_id="x",
        track_type="video",
        track_index=1,
        duration_frames=max(frames) + 1,
    )
    r.animated = True
    for i, f in enumerate(frames):
        s = TransformSample(sample_point=SamplePoint.MID, clip_local_frame=f, timeline_frame=f)
        s.ok = True
        s.scale = 1.0 + 0.05 * i
        r.samples.append(s)
    return r


def test_keyframe_spec_is_ordered_per_ok_sample() -> None:
    result = _animated_result([24, 0, 12])  # deliberately unordered
    spec = build_keyframe_spec(result, AppConfig(), SIZE)
    assert [frame for frame, _ in spec] == [0, 12, 24]
    assert len(spec) == 3


def test_should_use_fusion_gated_by_flag_and_animation() -> None:
    cfg = AppConfig()
    result = _animated_result([0, 24])
    assert should_use_fusion(result, cfg) is False  # apply_via_fusion default off
    cfg.set("apply_via_fusion", True)
    assert should_use_fusion(result, cfg) is True
    result.animated = False
    assert should_use_fusion(result, cfg) is False  # not animated
