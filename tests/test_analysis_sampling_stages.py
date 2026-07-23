"""Per-sample pipeline stages in _SamplingMixin (load → admit → refine → record)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import numpy as np

import matchref.analysis_sampling as sampling_mod
from matchref.analysis_context import _ClipContext, _SampleFrames
from matchref.config import AppConfig
from matchref.models import (
    ClipAnalysisResult,
    OfflineMappingSource,
    SamplePoint,
    TransformSample,
)
from matchref.transform_analysis import TransformAnalyzer


def _analyzer(config: AppConfig | None = None) -> TransformAnalyzer:
    # Skip __init__; the sampling stages only touch config/logger/frames/conform.
    analyzer = TransformAnalyzer.__new__(TransformAnalyzer)
    analyzer.config = config or AppConfig()
    analyzer.logger = logging.getLogger("matchref-test")
    analyzer._debug_dir = None
    analyzer.timeline_ctx = SimpleNamespace(
        resolution=(1920, 1080), analysis_max_width=lambda config: 960
    )
    return analyzer


def _result() -> ClipAnalysisResult:
    return ClipAnalysisResult(
        clip_name="clip",
        timeline_item_id="id",
        track_type="video",
        track_index=1,
        duration_frames=48,
    )


def _ctx(result: ClipAnalysisResult | None = None, **overrides: object) -> _ClipContext:
    fields: dict = {
        "timeline_item": None,
        "result": result or _result(),
        "baseline": SimpleNamespace(zoom_x=1.0, zoom_y=1.0, pan=0.0, tilt=0.0, rotation_deg=0.0),
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


def _mapping(source: OfflineMappingSource = OfflineMappingSource.TIMELINE) -> SimpleNamespace:
    return SimpleNamespace(source=source, offline_frame=42, detail="hub math")


def _loaded(sample: TransformSample, **overrides: object) -> _SampleFrames:
    fields: dict = {"sample": sample, "terminal": False, "mapping": _mapping()}
    fields.update(overrides)
    return _SampleFrames(**fields)


def _src_info() -> SimpleNamespace:
    return SimpleNamespace(
        clip_name="clip",
        reel_name="A001",
        source_frame=24,
        timeline_frame=1024,
        media_basename="clip",
    )


# ---- terminal / bookkeeping helpers ----------------------------------------


def test_terminal_sample_records_problem_on_result() -> None:
    analyzer = _analyzer()
    result = _result()
    ctx = _ctx(result)
    sample = _sample()
    out = analyzer._terminal_sample(ctx, sample, SamplePoint.MID, "gone", "frame missing")
    assert out.terminal
    assert result.problem
    assert result.samples == [sample]
    assert sample.message == "gone"
    assert result.warnings == ["mid: frame missing"]


def test_make_sample_carries_mapping_bookkeeping() -> None:
    sample = sampling_mod._SamplingMixin._make_sample(
        SamplePoint.END, 47, _src_info(), _mapping(OfflineMappingSource.EDL)
    )
    assert sample.sample_point == SamplePoint.END
    assert sample.offline_frame == 42
    assert sample.offline_mapping == "edl"
    assert sample.reel_name == "A001"
    assert sample.source_frame == 24


def test_conform_match_missing_only_in_strict_mode() -> None:
    analyzer = _analyzer()
    analyzer.conform = SimpleNamespace(is_available=True)
    analyzer.config.set("offline_mapping_mode", "conform")
    assert analyzer._conform_match_missing(_mapping(OfflineMappingSource.TIMELINE))
    assert not analyzer._conform_match_missing(_mapping(OfflineMappingSource.EDL))
    analyzer.config.set("offline_mapping_mode", "auto")
    assert not analyzer._conform_match_missing(_mapping(OfflineMappingSource.TIMELINE))


def test_record_sample_reject_appends_warning_and_message() -> None:
    analyzer = _analyzer()
    result = _result()
    ctx = _ctx(result)
    loaded = _loaded(_sample())
    out = analyzer._record_sample_reject(
        ctx, SamplePoint.MID, loaded, None, message="refine rejected: drift", warning="drift"
    )
    assert out.message == "refine rejected: drift"
    assert result.samples == [out]
    assert result.warnings == ["mid: drift"]


# ---- decode stages ----------------------------------------------------------


def _frames_stub(online: np.ndarray | None, offline: np.ndarray | None) -> SimpleNamespace:
    return SimpleNamespace(
        get_online_frame=lambda path, frame: online,
        get_offline_frame_at_index_with_meta=lambda frame: (offline, frame),
        clamp_source_frame=lambda path, frame: frame,
        offline_resolver=SimpleNamespace(resolve=lambda **kw: _mapping()),
    )


def test_decode_online_frame_fits_canvas_in_absolute_mode() -> None:
    analyzer = _analyzer()
    analyzer.config.set("edit_match_mode", "absolute")
    raw = np.zeros((10, 20, 3), dtype=np.uint8)
    analyzer.frames = _frames_stub(raw, None)
    ctx = _ctx(canvas_size=(192, 108))
    online, online_raw, compensate, absolute = analyzer._decode_online_frame(ctx, 5)
    assert absolute
    assert not compensate
    assert online_raw is raw
    assert online.shape[0] == 108
    assert online.shape[1] == 192


def test_decode_online_frame_passes_none_through() -> None:
    analyzer = _analyzer()
    analyzer.frames = _frames_stub(None, None)
    online, online_raw, _, _ = analyzer._decode_online_frame(_ctx(), 5)
    assert online is None
    assert online_raw is None


def test_decode_offline_frame_warns_on_decoder_drift() -> None:
    analyzer = _analyzer()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    analyzer.frames = SimpleNamespace(
        get_offline_frame_at_index_with_meta=lambda idx: (frame, idx + 2)
    )
    result = _result()
    ctx = _ctx(result)
    out, reported = analyzer._decode_offline_frame(ctx, SamplePoint.MID, 40)
    assert out is frame
    assert reported == 42
    assert any("decode index 42" in w for w in result.warnings)


# ---- _load_sample terminal paths -------------------------------------------


def _patch_source_info(monkeypatch) -> None:
    monkeypatch.setattr(
        sampling_mod, "build_clip_source_info", lambda item, frame, name, config: _src_info()
    )


def test_load_sample_terminal_when_online_missing(monkeypatch) -> None:
    analyzer = _analyzer()
    analyzer.frames = _frames_stub(None, np.zeros((4, 4, 3), dtype=np.uint8))
    _patch_source_info(monkeypatch)
    loaded = analyzer._load_sample(_ctx(), SamplePoint.MID, 24)
    assert loaded.terminal
    assert loaded.sample.message == "online frame unavailable"


def test_load_sample_terminal_when_offline_missing(monkeypatch) -> None:
    analyzer = _analyzer()
    analyzer.frames = _frames_stub(np.zeros((4, 4, 3), dtype=np.uint8), None)
    analyzer.config.set("edit_match_mode", "absolute")
    _patch_source_info(monkeypatch)
    ctx = _ctx(canvas_size=(4, 4))
    loaded = analyzer._load_sample(ctx, SamplePoint.MID, 24)
    assert loaded.terminal
    assert loaded.sample.message == "offline frame unavailable"


def test_load_sample_terminal_on_strict_conform_miss(monkeypatch) -> None:
    analyzer = _analyzer()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    analyzer.frames = _frames_stub(frame, frame)
    analyzer.conform = SimpleNamespace(is_available=True)
    analyzer.config.set("offline_mapping_mode", "conform")
    _patch_source_info(monkeypatch)
    ctx = _ctx(canvas_size=(4, 4))
    loaded = analyzer._load_sample(ctx, SamplePoint.MID, 24)
    assert loaded.terminal
    assert loaded.sample.message == "conform match not found"


def test_load_sample_happy_path_bundles_frames(monkeypatch) -> None:
    analyzer = _analyzer()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    analyzer.frames = _frames_stub(frame, frame)
    analyzer.conform = SimpleNamespace(is_available=False)
    _patch_source_info(monkeypatch)
    ctx = _ctx(canvas_size=(4, 4))
    loaded = analyzer._load_sample(ctx, SamplePoint.MID, 24)
    assert not loaded.terminal
    assert loaded.online is not None
    assert loaded.offline is frame
    assert loaded.compare_line


def test_load_sample_warns_when_source_frame_clamped(monkeypatch) -> None:
    analyzer = _analyzer()
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    analyzer.frames = _frames_stub(frame, frame)
    analyzer.frames.clamp_source_frame = lambda path, f: min(f, 10)
    analyzer.conform = SimpleNamespace(is_available=False)
    _patch_source_info(monkeypatch)
    result = _result()
    ctx = _ctx(result, canvas_size=(4, 4))
    analyzer._load_sample(ctx, SamplePoint.MID, 24)
    assert any("clamped to 10" in w for w in result.warnings)


# ---- admission & refine gating ---------------------------------------------


def test_passes_admission_above_threshold() -> None:
    analyzer = _analyzer()
    loaded = _loaded(_sample(), admit=0.9)
    alignment = SimpleNamespace(ecc_score=0.95)
    assert analyzer._passes_admission(_ctx(), SamplePoint.MID, loaded, alignment, False)


def test_passes_admission_near_miss_allowed_for_refine() -> None:
    analyzer = _analyzer()
    analyzer.config.set("min_ecc_for_refine", 0.5)
    loaded = _loaded(_sample(), admit=0.9)
    alignment = SimpleNamespace(ecc_score=0.7)
    assert analyzer._passes_admission(_ctx(), SamplePoint.MID, loaded, alignment, True)


def test_passes_admission_rejects_and_records() -> None:
    analyzer = _analyzer()
    result = _result()
    ctx = _ctx(result)
    loaded = _loaded(_sample(), admit=0.9, apply_floor=0.95)
    alignment = SimpleNamespace(ecc_score=0.2)
    assert not analyzer._passes_admission(ctx, SamplePoint.MID, loaded, alignment, True)
    assert result.samples == [loaded.sample]
    assert "below admission" in loaded.sample.message


def test_refine_skipped_by_score_only_with_early_exit() -> None:
    analyzer = _analyzer()
    analyzer.config.set("refine_early_exit_on_quality", True)
    analyzer.config.set("min_match_score", 0.95)
    alignment = SimpleNamespace(ecc_score=0.99)
    assert analyzer._refine_skipped_by_score(_ctx(), SamplePoint.MID, alignment)
    analyzer.config.set("refine_early_exit_on_quality", False)
    assert not analyzer._refine_skipped_by_score(_ctx(), SamplePoint.MID, alignment)


# ---- ECC candidate stash ----------------------------------------------------


def _alignment(scale: float = 1.2) -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        ecc_score=0.8,
        scale=scale,
        position_x=10.0,
        position_y=-5.0,
        rotation_deg=0.0,
        warp_matrix=None,
        analysis_width=0,
        analysis_height=0,
        message="",
    )


def test_stash_ecc_candidate_keeps_plausible_geometry() -> None:
    analyzer = _analyzer()
    ctx = _ctx()
    sample = _sample(refined_edit="wandered")
    analyzer._stash_ecc_candidate(ctx, sample, _alignment(scale=1.2))
    assert ctx.ecc_candidates == [sample]
    assert sample.refined_edit is None  # wandered refine dropped; ECC geometry kept
    assert sample.scale == 1.2


def test_stash_ecc_candidate_drops_out_of_range_scale() -> None:
    analyzer = _analyzer()
    ctx = _ctx()
    sample = _sample()
    analyzer._stash_ecc_candidate(ctx, sample, _alignment(scale=5.0))
    assert ctx.ecc_candidates == []


# ---- accept path ------------------------------------------------------------


def test_accept_sample_derives_geometry_and_records() -> None:
    analyzer = _analyzer()
    analyzer.config.set("perspective_match_enabled", False)
    result = _result()
    ctx = _ctx(result)
    loaded = _loaded(_sample())
    analyzer._accept_sample(ctx, SamplePoint.MID, loaded, _alignment(scale=1.3))
    sample = loaded.sample
    assert sample.ok
    assert sample.accept_via == "score"
    assert sample.scale == 1.3
    assert result.samples == [sample]
    assert ctx.transform_vectors and ctx.transform_vectors[0][0] == 1.3


def test_accept_sample_keeps_existing_accept_via() -> None:
    analyzer = _analyzer()
    ctx = _ctx()
    loaded = _loaded(_sample(accept_via="escalated"))
    analyzer._accept_sample(ctx, SamplePoint.MID, loaded, _alignment())
    assert loaded.sample.accept_via == "escalated"


# ---- _process_sample orchestration ------------------------------------------


def _wire_process_sample(
    analyzer: TransformAnalyzer, monkeypatch, alignment: SimpleNamespace
) -> None:
    frame = np.zeros((4, 4, 3), dtype=np.uint8)
    analyzer.frames = _frames_stub(frame, frame)
    analyzer.conform = SimpleNamespace(is_available=False)
    _patch_source_info(monkeypatch)
    monkeypatch.setattr(analyzer, "_align_sample", lambda ctx, loaded: alignment, raising=False)


def test_process_sample_records_failed_alignment(monkeypatch) -> None:
    analyzer = _analyzer()
    alignment = _alignment()
    alignment.ok = False
    alignment.message = "ECC diverged"
    _wire_process_sample(analyzer, monkeypatch, alignment)
    result = _result()
    ctx = _ctx(result, canvas_size=(4, 4))
    sample = analyzer._process_sample(ctx, SamplePoint.MID, 24)
    assert not sample.ok
    assert sample.message == "ECC diverged"
    assert any("match failed" in w for w in result.warnings)


def test_process_sample_accepts_by_score_without_refine(monkeypatch) -> None:
    analyzer = _analyzer()
    analyzer.config.set("resolve_edit_refine", False)  # no refine leg
    alignment = _alignment(scale=1.1)
    alignment.ecc_score = 0.97  # clears the admission threshold outright
    _wire_process_sample(analyzer, monkeypatch, alignment)
    result = _result()
    ctx = _ctx(result, canvas_size=(4, 4))
    sample = analyzer._process_sample(ctx, SamplePoint.MID, 24)
    assert sample.ok
    assert sample.accept_via == "score"
    assert result.samples == [sample]
