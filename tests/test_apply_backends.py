"""Apply backends against duck-typed Resolve objects (Edit, Fusion, CornerPin)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np
import pytest

from matchref.config import AppConfig
from matchref.cornerpin_apply import CornerPinApplier, cornerpin_for_result, should_use_cornerpin
from matchref.edit_apply import EditTransformApplier
from matchref.fusion_apply import FusionTransformApplier
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample
from matchref.timecode import TimecodeFormat, format_timecode
from matchref.timeline_context import TimelineContext


def _result(**overrides: object) -> ClipAnalysisResult:
    fields: dict = {
        "clip_name": "clip",
        "timeline_item_id": "id",
        "track_type": "video",
        "track_index": 1,
        "duration_frames": 48,
        "use_mid_key": False,
    }
    fields.update(overrides)
    return ClipAnalysisResult(**fields)


def _sample(**overrides: object) -> TransformSample:
    fields: dict = {
        "sample_point": SamplePoint.MID,
        "clip_local_frame": 24,
        "timeline_frame": 100,
        "ok": True,
        "scale": 1.2,
        "ecc_score": 0.97,
    }
    fields.update(overrides)
    return TransformSample(**fields)


class _FakeItem:
    # Records SetProperty writes and serves them back for read-back verification.
    def __init__(self, *, reject: set[str] | None = None) -> None:
        self.props: dict[str, object] = {}
        self.reject = reject or set()
        self.start = 1000

    def GetStart(self) -> int:
        return self.start

    def SetProperty(self, key: str, value: object) -> bool:
        if key in self.reject:
            return False
        self.props[key] = value
        return True

    def GetProperty(self, key: str) -> object:
        return self.props.get(key, 0.0)


class _FakeTimeline:
    def __init__(self, fmt: TimecodeFormat) -> None:
        self.fmt = fmt
        self.tc = format_timecode(0, fmt)
        self.seeks: list[str] = []

    def SetCurrentTimecode(self, tc: str) -> None:
        self.tc = tc
        self.seeks.append(tc)

    def GetCurrentTimecode(self) -> str:
        return self.tc


def _resolve_duck(timeline: _FakeTimeline, *, keyframe_ok: bool = True) -> SimpleNamespace:
    project = SimpleNamespace(GetCurrentTimeline=lambda: timeline)
    manager = SimpleNamespace(GetCurrentProject=lambda: project)

    def _set_kf_mode(mode: int) -> None:
        if not keyframe_ok:
            raise RuntimeError("keyframe mode unsupported")

    return SimpleNamespace(
        GetProjectManager=lambda: manager,
        OpenPage=lambda page: None,
        SetKeyframeMode=_set_kf_mode,
    )


def _applier(resolve: SimpleNamespace, config: AppConfig | None = None) -> EditTransformApplier:
    ctx = TimelineContext(fps=25.0, width=1920, height=1080)
    return EditTransformApplier(
        resolve, config or AppConfig(), ctx, logging.getLogger("matchref-test")
    )


def _edit_setup(
    samples: list[TransformSample], config: AppConfig | None = None, **result_kw: object
) -> tuple[EditTransformApplier, _FakeItem, ClipAnalysisResult, _FakeTimeline]:
    timeline = _FakeTimeline(TimecodeFormat(fps=25.0, drop_frame=False))
    applier = _applier(_resolve_duck(timeline), config)
    item = _FakeItem()
    result = _result(timeline_item=item, **result_kw)
    result.samples = samples
    return applier, item, result, timeline


# ---- EditTransformApplier ---------------------------------------------------


def test_apply_clip_requires_item_and_samples() -> None:
    applier = _applier(_resolve_duck(_FakeTimeline(TimecodeFormat(fps=25.0, drop_frame=False))))
    with pytest.raises(RuntimeError, match="item reference missing"):
        applier.apply_clip(_result(timeline_item=None))
    with pytest.raises(RuntimeError, match="No successful samples"):
        applier.apply_clip(_result(timeline_item=_FakeItem()))


def _full_match_config() -> AppConfig:
    config = AppConfig()
    config.set("match_position", True)
    config.set("match_rotation", True)
    return config


def test_apply_clip_writes_best_static_transform() -> None:
    applier, item, result, _ = _edit_setup(
        [_sample(scale=1.2), _sample(scale=1.1, ecc_score=0.90, sample_point=SamplePoint.END)],
        _full_match_config(),
    )
    applier.apply_clip(result)
    assert item.props["ZoomX"] == pytest.approx(1.2, abs=0.01)
    assert item.props["ZoomGang"] is True
    assert "Pan" in item.props
    assert "RotationAngle" in item.props


def test_apply_clip_respects_match_flags() -> None:
    # Defaults: scale only — position and rotation stay untouched.
    applier, item, result, _ = _edit_setup([_sample()])
    applier.apply_clip(result)
    assert "ZoomX" in item.props
    assert "Pan" not in item.props
    assert "RotationAngle" not in item.props


def test_apply_clip_refuses_insane_pan() -> None:
    # Zoom is clamped to Inspector range upstream; the sanity gate trips on a
    # refined pan far beyond the canvas.
    from matchref.clip_edit_transform import ClipEditTransform

    bad_edit = ClipEditTransform(zoom_x=1.2, zoom_y=1.2, pan=50000.0, tilt=0.0, rotation_deg=0.0)
    applier, _, result, _ = _edit_setup([_sample(refined_edit=bad_edit)], _full_match_config())
    with pytest.raises(RuntimeError, match="insane transform"):
        applier.apply_clip(result)


def test_apply_clip_partial_write_is_hard_error() -> None:
    timeline = _FakeTimeline(TimecodeFormat(fps=25.0, drop_frame=False))
    applier = _applier(_resolve_duck(timeline), _full_match_config())
    item = _FakeItem(reject={"Pan"})
    result = _result(timeline_item=item)
    result.samples = [_sample()]
    with pytest.raises(RuntimeError, match="SetProperty rejected: Pan"):
        applier.apply_clip(result)


def test_apply_clip_readback_mismatch_is_hard_error() -> None:
    applier, item, result, _ = _edit_setup([_sample()])
    item.GetProperty = lambda key: 999.0  # Resolve "kept" a different value
    with pytest.raises(RuntimeError, match="Apply not confirmed"):
        applier.apply_clip(result)


def test_apply_clip_keyframes_animated_ramp() -> None:
    samples = [
        _sample(sample_point=SamplePoint.START, clip_local_frame=0, scale=1.0),
        _sample(sample_point=SamplePoint.MID, clip_local_frame=24, scale=1.2),
        _sample(sample_point=SamplePoint.END, clip_local_frame=47, scale=1.4),
    ]
    applier, item, result, timeline = _edit_setup(samples, animated=True)
    applier.apply_clip(result)
    assert len(timeline.seeks) == 3  # playhead visited every keyframe
    assert item.props["ZoomX"] == pytest.approx(1.4, abs=0.01)  # last written key


def test_apply_clip_animated_refuses_without_keyframe_mode() -> None:
    timeline = _FakeTimeline(TimecodeFormat(fps=25.0, drop_frame=False))
    applier = _applier(_resolve_duck(timeline, keyframe_ok=False))
    item = _FakeItem()
    result = _result(timeline_item=item, animated=True)
    result.samples = [
        _sample(sample_point=SamplePoint.START, clip_local_frame=0),
        _sample(sample_point=SamplePoint.END, clip_local_frame=47),
    ]
    with pytest.raises(RuntimeError, match="keyframe mode"):
        applier.apply_clip(result)


def test_prefer_mid_samples_filters_when_requested() -> None:
    mid = _sample(sample_point=SamplePoint.MID)
    end = _sample(sample_point=SamplePoint.END)
    picked = EditTransformApplier._prefer_mid_samples(_result(use_mid_key=True), [mid, end])
    assert picked == [mid]
    # No MID available → keep everything rather than dropping the clip.
    picked2 = EditTransformApplier._prefer_mid_samples(_result(use_mid_key=True), [end])
    assert picked2 == [end]


def test_median_select_averages_samples() -> None:
    config = AppConfig()
    config.set("apply_transform_select", "median")
    samples = [
        _sample(sample_point=SamplePoint.START, clip_local_frame=0, scale=1.0),
        _sample(sample_point=SamplePoint.MID, clip_local_frame=24, scale=1.2),
        _sample(sample_point=SamplePoint.END, clip_local_frame=47, scale=1.6),
    ]
    applier, item, result, _ = _edit_setup(samples, config)
    applier.apply_clip(result)
    assert item.props["ZoomX"] == pytest.approx(1.2, abs=0.01)  # the median sample


# ---- FusionTransformApplier -------------------------------------------------


class _FakeNode:
    def __init__(self) -> None:
        self.keys: list[tuple[str, object, int]] = []

    def SetInput(self, attr: str, value: object, frame: int | None = None) -> None:
        self.keys.append((attr, value, frame))


def _fusion_item(node: _FakeNode) -> SimpleNamespace:
    comp = SimpleNamespace(AddTool=lambda name: node)
    return SimpleNamespace(
        GetFusionCompCount=lambda: 0,
        AddFusionComp=lambda: comp,
        GetStart=lambda: 1000,
    )


def test_fusion_apply_animated_keyframes_all_samples() -> None:
    node = _FakeNode()
    applier = FusionTransformApplier(AppConfig(), (1920, 1080))
    result = _result(animated=True)
    result.samples = [
        _sample(sample_point=SamplePoint.START, clip_local_frame=0, scale=1.0),
        _sample(sample_point=SamplePoint.END, clip_local_frame=47, scale=1.4),
    ]
    applier.apply_animated(_fusion_item(node), result)
    frames = {frame for (_a, _v, frame) in node.keys}
    assert frames == {1000, 1047}  # clip start + local frames
    assert {a for (a, _v, _f) in node.keys} == {"Center", "Size", "Angle"}


def test_fusion_apply_needs_two_samples() -> None:
    applier = FusionTransformApplier(AppConfig(), (1920, 1080))
    result = _result(animated=True)
    result.samples = [_sample()]
    with pytest.raises(RuntimeError, match=">= 2 ok samples"):
        applier.apply_animated(_fusion_item(_FakeNode()), result)


def test_fusion_apply_raises_when_comp_unavailable() -> None:
    applier = FusionTransformApplier(AppConfig(), (1920, 1080))
    result = _result(animated=True)
    result.samples = [
        _sample(sample_point=SamplePoint.START, clip_local_frame=0),
        _sample(sample_point=SamplePoint.END, clip_local_frame=47),
    ]
    broken = SimpleNamespace(
        GetFusionCompCount=lambda: 0,
        AddFusionComp=lambda: (_ for _ in ()).throw(RuntimeError("no fusion")),
    )
    with pytest.raises(RuntimeError, match="Fusion comp"):
        applier.apply_animated(broken, result)


# ---- CornerPinApplier -------------------------------------------------------


def _homography_result() -> ClipAnalysisResult:
    result = _result()
    result.samples = [_sample(homography=np.eye(3, dtype=np.float64))]
    return result


def test_should_use_cornerpin_gated_by_config() -> None:
    config = AppConfig()
    config.set("perspective_match_enabled", False)
    assert not should_use_cornerpin(_homography_result(), config)
    config.set("perspective_match_enabled", True)
    assert should_use_cornerpin(_homography_result(), config)
    assert not should_use_cornerpin(_result(), config)  # no homography solved


def test_cornerpin_for_result_identity_maps_unit_corners() -> None:
    cp = cornerpin_for_result(_homography_result(), (1920, 1080))
    assert cp is not None
    # Fusion-normalized coordinates: y is flipped (0 = bottom).
    assert cp.top_left == pytest.approx((0.0, 1.0), abs=1e-6)
    assert cp.bottom_right == pytest.approx((1.0, 0.0), abs=1e-6)


def test_cornerpin_applier_sets_four_corners() -> None:
    node = _FakeNode()
    applier = CornerPinApplier(AppConfig(), (1920, 1080))
    applier.apply(_fusion_item(node), _homography_result())
    corners = {a for (a, _v, _f) in node.keys}
    assert corners == {"TopLeft", "TopRight", "BottomRight", "BottomLeft"}


def test_cornerpin_applier_requires_homography() -> None:
    applier = CornerPinApplier(AppConfig(), (1920, 1080))
    with pytest.raises(RuntimeError, match="No homography"):
        applier.apply(_fusion_item(_FakeNode()), _result())
