"""Clip-level decision logic in TransformAnalyzer (begin/finalize stages)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import matchref.transform_analysis as ta_mod
from matchref.analysis_context import _ClipContext
from matchref.config import AppConfig
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample
from matchref.transform_analysis import TransformAnalyzer, _clip_name, confident_break_allowed


def _analyzer(config: AppConfig | None = None) -> TransformAnalyzer:
    # Skip __init__ (needs Resolve/conform/frame providers); set only what the
    # stage methods under test read.
    analyzer = TransformAnalyzer.__new__(TransformAnalyzer)
    analyzer.config = config or AppConfig()
    analyzer.logger = logging.getLogger("matchref-test")
    analyzer._variance_threshold = 0.02
    analyzer._sample_n = 0
    analyzer._debug_dir = None
    analyzer._batch_ready = True
    return analyzer


def _result(**overrides: object) -> ClipAnalysisResult:
    fields: dict = {
        "clip_name": "clip",
        "timeline_item_id": "id",
        "track_type": "video",
        "track_index": 1,
        "duration_frames": 48,
    }
    fields.update(overrides)
    return ClipAnalysisResult(**fields)


def _ctx(result: ClipAnalysisResult, **overrides: object) -> _ClipContext:
    fields: dict = {
        "timeline_item": None,
        "result": result,
        "baseline": None,
        "media_path": "/media/clip.mov",
        "canvas_size": (1920, 1080),
        "control_points": [],
        "stop_on_confident": True,
        "confident_score": 0.96,
        "min_scale_apply": 0.4,
        "max_scale_apply": 2.5,
        "match_base_scale": 1.0,
        "match_base_center": (0.0, 0.0),
        "match_base_angle": 0.0,
    }
    fields.update(overrides)
    return _ClipContext(**fields)


def _sample(**overrides: object) -> TransformSample:
    fields: dict = {
        "sample_point": SamplePoint.MID,
        "clip_local_frame": 24,
        "timeline_frame": 100,
    }
    fields.update(overrides)
    return TransformSample(**fields)


# ---- confident_break_allowed ------------------------------------------------


def test_confident_break_immediate_when_keyframing_off() -> None:
    assert confident_break_allowed([1.2], keyframing=False, zoom_spread_limit=1.06)


def test_confident_break_needs_two_samples_with_keyframing() -> None:
    assert not confident_break_allowed([1.2], keyframing=True, zoom_spread_limit=1.06)


def test_confident_break_when_zooms_agree() -> None:
    assert confident_break_allowed([1.20, 1.21], keyframing=True, zoom_spread_limit=1.06)


def test_confident_break_denied_on_zoom_spread() -> None:
    assert not confident_break_allowed([1.0, 1.3], keyframing=True, zoom_spread_limit=1.06)


# ---- small helpers ----------------------------------------------------------


def test_clip_name_falls_back_on_error() -> None:
    class _Broken:
        def GetName(self) -> str:
            raise RuntimeError("no api")

    assert _clip_name(_Broken()) == "?"
    assert _clip_name(SimpleNamespace(GetName=lambda: "Shot A")) == "Shot A"


def test_min_ok_samples_single_and_best_modes() -> None:
    analyzer = _analyzer()
    analyzer.config.set("match_sample_mode", "single")
    assert analyzer._min_ok_samples_required() == 1
    analyzer.config.set("match_sample_mode", "three")
    analyzer.config.set("apply_transform_select", "best")
    assert analyzer._min_ok_samples_required() == 1
    analyzer.config.set("apply_transform_select", "all")
    analyzer.config.set("min_ok_samples_per_clip", 3)
    assert analyzer._min_ok_samples_required() == 3


def test_ordered_control_points_mid_first_for_early_stop() -> None:
    analyzer = _analyzer()
    analyzer.config.set("match_sample_mode", "three")
    points = analyzer._ordered_control_points(48, stop_on_confident=True)
    assert points[0][0] == SamplePoint.MID
    unordered = analyzer._ordered_control_points(48, stop_on_confident=False)
    assert unordered[0][0] == SamplePoint.START


def test_match_base_values_identity_in_absolute_mode() -> None:
    analyzer = _analyzer()
    analyzer.config.set("edit_match_mode", "absolute")
    assert analyzer._match_base_values() == (1.0, (0.0, 0.0), 0.0)


def test_match_base_values_read_from_config_in_delta_mode() -> None:
    analyzer = _analyzer()
    analyzer.config.set("edit_match_mode", "delta")
    analyzer.config.set("transform_base_scale", 1.5)
    analyzer.config.set("transform_base_center", [0.25, 0.75])
    analyzer.config.set("transform_base_angle", 2.0)
    assert analyzer._match_base_values() == (1.5, (0.25, 0.75), 2.0)


def test_clip_media_path_skips_audio_and_missing_media() -> None:
    analyzer = _analyzer()
    audio = SimpleNamespace(GetTrackTypeAndIndex=lambda: ("audio", 1))
    assert analyzer._clip_media_path(audio) is None

    no_media = SimpleNamespace(
        GetTrackTypeAndIndex=lambda: ("video", 1), GetMediaPoolItem=lambda: None
    )
    assert analyzer._clip_media_path(no_media) is None


def test_clip_media_path_reads_media_pool_property() -> None:
    analyzer = _analyzer()
    media = SimpleNamespace(GetClipProperty=lambda key=None: "/media/shot.mov")
    item = SimpleNamespace(
        GetTrackTypeAndIndex=lambda: ("video", 2), GetMediaPoolItem=lambda: media
    )
    assert analyzer._clip_media_path(item) == "/media/shot.mov"


def test_warn_clip_durations_flags_long_and_overrunning_clips() -> None:
    analyzer = _analyzer()
    analyzer.config.set("max_clip_duration_frames", 100)
    analyzer.frames = SimpleNamespace(get_media_frame_count=lambda path: 120)
    result = _result()
    meta = {"duration": 150}
    analyzer._warn_clip_durations(result, meta, "/media/clip.mov")
    assert any("limit 100f" in w for w in result.warnings)
    assert any("longer than media file" in w for w in result.warnings)


def test_log_match_mode_absolute_and_delta(caplog) -> None:
    analyzer = _analyzer()
    with caplog.at_level(logging.INFO, logger="matchref-test"):
        analyzer.config.set("edit_match_mode", "absolute")
        analyzer._log_match_mode()
        analyzer.config.set("edit_match_mode", "delta")
        analyzer._log_match_mode()
    assert "absolute" in caplog.text
    assert "delta" in caplog.text


# ---- _should_stop_after -----------------------------------------------------


def test_stop_after_requires_confident_flag_and_ok_sample() -> None:
    analyzer = _analyzer()
    ctx = _ctx(_result(), stop_on_confident=False)
    assert not analyzer._should_stop_after(ctx, _sample(ok=True, ecc_score=0.99))
    ctx2 = _ctx(_result())
    assert not analyzer._should_stop_after(ctx2, _sample(ok=False, ecc_score=0.99))


def test_stop_after_rejects_low_score_or_out_of_range_scale() -> None:
    analyzer = _analyzer()
    ctx = _ctx(_result())
    assert not analyzer._should_stop_after(ctx, _sample(ok=True, ecc_score=0.90, scale=1.0))
    assert not analyzer._should_stop_after(ctx, _sample(ok=True, ecc_score=0.99, scale=3.0))


def test_stop_after_confident_static_confirmed() -> None:
    analyzer = _analyzer()
    analyzer.config.set("apply_keyframes_on_variance", False)
    result = _result()
    ctx = _ctx(result)
    sample = _sample(ok=True, ecc_score=0.99, scale=1.2)
    result.samples.append(sample)
    assert analyzer._should_stop_after(ctx, sample)


def test_stop_after_keeps_sampling_when_ramp_not_ruled_out() -> None:
    analyzer = _analyzer()
    analyzer.config.set("apply_keyframes_on_variance", True)
    result = _result()
    ctx = _ctx(result)
    sample = _sample(ok=True, ecc_score=0.99, scale=1.2)
    result.samples.append(sample)  # only one ok sample — a ramp is still possible
    assert not analyzer._should_stop_after(ctx, sample)


# ---- consensus rescue -------------------------------------------------------


def test_consensus_rescue_disabled_or_empty_returns_nothing() -> None:
    analyzer = _analyzer()
    ctx = _ctx(_result())
    assert analyzer._consensus_rescued(ctx) == []
    analyzer.config.set("ecc_consensus_rescue", False)
    ctx.ecc_candidates.append(_sample(scale=1.2, ecc_score=0.8))
    assert analyzer._consensus_rescued(ctx) == []


def test_consensus_rescue_accepts_agreeing_candidates(monkeypatch) -> None:
    analyzer = _analyzer()
    result = _result()
    ctx = _ctx(result)
    ctx.ecc_candidates = [
        _sample(scale=1.20, ecc_score=0.80),
        _sample(scale=1.22, ecc_score=0.85),
    ]
    monkeypatch.setattr(ta_mod, "ecc_consensus_passes", lambda scales, score, config: True)
    rescued = analyzer._consensus_rescued(ctx)
    assert len(rescued) == 1
    assert rescued[0].ok
    assert rescued[0].accept_via == "consensus"
    assert rescued[0].ecc_score == 0.85  # best-scoring candidate represents the clip
    assert any("ECC-consensus rescue" in w for w in result.warnings)


def test_consensus_rescue_rejected_by_gate(monkeypatch) -> None:
    analyzer = _analyzer()
    ctx = _ctx(_result())
    ctx.ecc_candidates = [_sample(scale=0.6, ecc_score=0.5), _sample(scale=2.4, ecc_score=0.5)]
    monkeypatch.setattr(ta_mod, "ecc_consensus_passes", lambda scales, score, config: False)
    assert analyzer._consensus_rescued(ctx) == []


# ---- best-select finalize ---------------------------------------------------


def test_best_select_problems_scale_out_of_range() -> None:
    analyzer = _analyzer()
    # 5.0 exceeds even the escalated high-confidence window (0.25–4.0).
    best = _sample(ok=True, scale=5.0, ecc_score=0.97)
    reasons, animated = analyzer._best_select_problems(_ctx(_result()), [best], best)
    assert reasons and "outside" in reasons[0]
    assert not animated


def test_best_select_problems_detects_animated_ramp(monkeypatch) -> None:
    analyzer = _analyzer()
    analyzer.config.set("apply_keyframes_on_variance", True)
    monkeypatch.setattr(ta_mod, "clip_motion_profile", lambda samples, config: (True, True))
    samples = [_sample(ok=True, scale=1.0, ecc_score=0.97), _sample(ok=True, scale=1.4)]
    reasons, animated = analyzer._best_select_problems(_ctx(_result()), samples, samples[0])
    assert animated
    assert reasons == []


def test_best_select_problems_flags_erratic_disagreement(monkeypatch) -> None:
    analyzer = _analyzer()
    monkeypatch.setattr(ta_mod, "clip_motion_profile", lambda samples, config: (True, False))
    monkeypatch.setattr(ta_mod, "max_scale_spread", lambda config: 1.15)
    samples = [_sample(ok=True, scale=1.0, ecc_score=0.97), _sample(ok=True, scale=1.6)]
    reasons, animated = analyzer._best_select_problems(_ctx(_result()), samples, samples[0])
    assert not animated
    assert reasons and "not a smooth ramp" in reasons[0]


def test_finalize_best_select_accepts_and_disables_mid_key() -> None:
    analyzer = _analyzer()
    result = _result(use_mid_key=True)
    ctx = _ctx(result)
    ok = [_sample(ok=True, scale=1.2, ecc_score=0.98)]
    analyzer._finalize_best_select(ctx, ok)
    assert not result.problem
    assert not result.use_mid_key


def test_finalize_best_select_warns_on_non_score_acceptance() -> None:
    analyzer = _analyzer()
    result = _result()
    ctx = _ctx(result)
    ok = [_sample(ok=True, scale=1.2, ecc_score=0.90, accept_via="consensus")]
    analyzer._finalize_best_select(ctx, ok)
    assert any("accepted via consensus" in w for w in result.warnings)


# ---- finalize_clip ----------------------------------------------------------


def test_finalize_clip_marks_problem_without_ok_samples() -> None:
    analyzer = _analyzer()
    result = _result()
    ctx = _ctx(result)
    analyzer._finalize_clip(ctx)
    assert result.problem


def test_finalize_clip_multi_sample_quality_gate(monkeypatch) -> None:
    analyzer = _analyzer()
    analyzer.config.set("apply_transform_select", "all")
    analyzer.config.set("match_sample_mode", "three")
    analyzer.config.set("min_ok_samples_per_clip", 2)
    result = _result()
    ctx = _ctx(result)
    result.samples = [
        _sample(ok=True, scale=1.2, ecc_score=0.97),
        _sample(ok=True, scale=1.2, ecc_score=0.96, sample_point=SamplePoint.END),
    ]
    monkeypatch.setattr(ta_mod, "assess_clip_match_quality", lambda samples, config: (True, []))
    analyzer._finalize_clip(ctx)
    assert not result.problem


def test_finalize_clip_multi_sample_records_failure_reasons(monkeypatch) -> None:
    analyzer = _analyzer()
    analyzer.config.set("apply_transform_select", "all")
    analyzer.config.set("match_sample_mode", "three")
    analyzer.config.set("min_ok_samples_per_clip", 2)
    result = _result()
    ctx = _ctx(result)
    result.samples = [
        _sample(ok=True, scale=1.0, ecc_score=0.90),
        _sample(ok=True, scale=1.9, ecc_score=0.91, sample_point=SamplePoint.END),
    ]
    monkeypatch.setattr(
        ta_mod, "assess_clip_match_quality", lambda samples, config: (False, ["bad spread"])
    )
    analyzer._finalize_clip(ctx)
    assert result.problem
    assert "bad spread" in result.warnings


def test_finalize_clip_uses_mid_keyframe_when_unstable() -> None:
    analyzer = _analyzer()
    analyzer.config.set("apply_transform_select", "all")
    analyzer.config.set("match_sample_mode", "three")
    analyzer.config.set("min_ok_samples_per_clip", 2)
    analyzer.config.set("use_midpoint_keyframe", True)
    result = _result()
    ctx = _ctx(result)
    # No ok samples and wildly varying raw vectors → problem, mid key requested.
    ctx.transform_vectors = [(1.0, 0, 0, 0), (2.0, 300, 300, 0)]
    analyzer._finalize_clip(ctx)
    assert result.problem
    assert result.use_mid_key


# ---- prepare_batch / _begin_clip -------------------------------------------


def _frames_stub(offline_frames: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        set_offline_reference=lambda path, timeline_fps: None,
        get_offline_frame_count=lambda: offline_frames,
        get_media_frame_count=lambda path: 0,
        offline_resolver=SimpleNamespace(configure_lock_cut=lambda lock_cut_hub_origin: None),
    )


def test_prepare_batch_requires_offline_path() -> None:
    analyzer = _analyzer()
    analyzer.config.set("offline_reference_path", "")
    try:
        analyzer.prepare_batch([])
    except ValueError as exc:
        assert "Offline reference" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty offline path")


def test_prepare_batch_filters_clips_and_sets_ready(monkeypatch) -> None:
    analyzer = _analyzer()
    analyzer._batch_ready = False
    analyzer.config.set("offline_reference_path", "/ref/lockcut.mov")
    analyzer.config.set("debug_save_frames", False)
    analyzer.frames = _frames_stub()
    analyzer.resolve = None
    analyzer.timeline_ctx = SimpleNamespace(fps=25.0)
    clip = SimpleNamespace(GetStart=lambda: 100, GetDuration=lambda: 50)
    monkeypatch.setattr(
        ta_mod, "filter_target_clips", lambda items, config, **kw: ([clip], ["skipped one"])
    )
    clips, skipped = analyzer.prepare_batch([clip, object()])
    assert clips == [clip]
    assert skipped == ["skipped one"]
    assert analyzer._batch_ready


def test_analyze_one_requires_prepare_batch() -> None:
    analyzer = _analyzer()
    analyzer._batch_ready = False
    try:
        analyzer.analyze_one(object())
    except RuntimeError as exc:
        assert "prepare_batch" in str(exc)
    else:
        raise AssertionError("expected RuntimeError before prepare_batch")


def _duck_timeline_item() -> SimpleNamespace:
    media = SimpleNamespace(GetClipProperty=lambda key=None: "/media/shot.mov")
    return SimpleNamespace(
        GetTrackTypeAndIndex=lambda: ("video", 1),
        GetMediaPoolItem=lambda: media,
        GetName=lambda: "Shot A",
        GetDuration=lambda: 48,
        GetUniqueId=lambda: "uid-1",
        GetStart=lambda: 1000,
        GetSourceStartFrame=lambda: 0,
        GetSourceEndFrame=lambda: 47,
        GetProperty=lambda key=None: {},
    )


def test_begin_clip_builds_context_from_duck_item() -> None:
    analyzer = _analyzer()
    analyzer.frames = _frames_stub()
    analyzer.timeline_ctx = SimpleNamespace(resolution=(1920, 1080), fps=25.0)
    ctx = analyzer._begin_clip(_duck_timeline_item())
    assert ctx is not None
    assert ctx.media_path == "/media/shot.mov"
    assert ctx.result.clip_name == "Shot A"
    assert ctx.canvas_size == (1920, 1080)
    assert ctx.control_points  # sampling produced at least one point
    assert ctx.max_scale_apply == float(analyzer.config.get("max_alignment_scale", 2.5))


def test_begin_clip_returns_none_for_audio_item() -> None:
    analyzer = _analyzer()
    audio = SimpleNamespace(GetTrackTypeAndIndex=lambda: ("audio", 1))
    assert analyzer._begin_clip(audio) is None
