"""Debug bundle writing: _save_match_debug and the debug_frames helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from matchref.analysis_context import _ClipContext, _SampleFrames
from matchref.config import AppConfig
from matchref.debug_frames import (
    DebugImages,
    _normalized_difference,
    _safe_name,
    _stack_horizontal,
    resolve_debug_dir,
    save_match_debug,
)
from matchref.models import ClipAnalysisResult, OfflineMappingSource, SamplePoint, TransformSample
from matchref.transform_analysis import TransformAnalyzer


def _analyzer(debug_dir: Path | None) -> TransformAnalyzer:
    analyzer = TransformAnalyzer.__new__(TransformAnalyzer)
    analyzer.config = AppConfig()
    analyzer.logger = logging.getLogger("matchref-test")
    analyzer._debug_dir = debug_dir
    analyzer.timeline_ctx = SimpleNamespace(resolution=(64, 36))
    return analyzer


def _frame(w: int = 64, h: int = 36) -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, size=(h, w, 3), dtype=np.uint8)


def _ctx() -> _ClipContext:
    result = ClipAnalysisResult(
        clip_name="Shot/A: v2",
        timeline_item_id="id",
        track_type="video",
        track_index=1,
        duration_frames=48,
    )
    return _ClipContext(
        timeline_item=None,
        result=result,
        baseline=None,
        media_path="/media/clip.mov",
        canvas_size=(64, 36),
        control_points=[],
        stop_on_confident=True,
        confident_score=0.96,
        min_scale_apply=0.4,
        max_scale_apply=2.5,
        match_base_scale=1.0,
        match_base_center=(0.0, 0.0),
        match_base_angle=0.0,
    )


def _loaded(sample: TransformSample, *, with_raw: bool = True) -> _SampleFrames:
    return _SampleFrames(
        sample=sample,
        terminal=False,
        online=_frame(),
        offline=_frame(),
        online_raw=_frame(128, 72) if with_raw else None,
        mapping=SimpleNamespace(
            source=OfflineMappingSource.TIMELINE, offline_frame=42, detail="hub math"
        ),
        compare_line="online vs offline",
        admit=0.85,
        apply_floor=0.95,
    )


def _sample(**overrides: object) -> TransformSample:
    fields: dict = {"sample_point": SamplePoint.MID, "clip_local_frame": 24, "timeline_frame": 100}
    fields.update(overrides)
    return TransformSample(**fields)


# ---- debug_frames helpers ---------------------------------------------------


def test_resolve_debug_dir_disabled_returns_none() -> None:
    config = AppConfig()
    config.set("debug_save_frames", False)
    assert resolve_debug_dir(config) is None


def test_resolve_debug_dir_creates_configured_path(tmp_path) -> None:
    config = AppConfig()
    config.set("debug_save_frames", True)
    config.set("debug_output_dir", str(tmp_path / "dbg"))
    out = resolve_debug_dir(config)
    assert out == tmp_path / "dbg"
    assert out.is_dir()


def test_safe_name_sanitizes_and_truncates() -> None:
    assert _safe_name("Shot/A: v2") == "Shot_A_v2"
    assert _safe_name("") == "clip"
    assert len(_safe_name("x" * 100)) == 48


def test_stack_horizontal_pads_and_converts_gray() -> None:
    left = np.zeros((10, 8), dtype=np.uint8)  # grayscale, shorter
    right = np.zeros((20, 6, 3), dtype=np.uint8)
    out = _stack_horizontal(left, right, gap=4)
    assert out.shape == (20, 8 + 4 + 6, 3)


def test_normalized_difference_dark_for_identical_frames() -> None:
    frame = _frame()
    diff, near_black = _normalized_difference(frame, frame.copy())
    assert diff.shape == frame.shape[:2]
    assert near_black > 0.95  # identical frames align perfectly


def test_save_match_debug_writes_bundle(tmp_path) -> None:
    images = DebugImages(
        online_for_match=_frame(),
        offline_ref=_frame(),
        online_raw=_frame(128, 72),
        result_render=_frame(),
    )
    path = save_match_debug(
        tmp_path,
        clip_name="Shot A",
        sample_point="mid",
        images=images,
        mapping_detail="hub math",
        extra_lines=["extra"],
    )
    base = tmp_path / "Shot_A" / "mid"
    assert Path(path) == base / "compare_online_vs_offline.jpg"
    for name in (
        "compare_online_vs_offline.jpg",
        "online_for_match.jpg",
        "offline_reference.jpg",
        "online_raw_source.jpg",
        "result_vs_offline.jpg",
        "difference.jpg",
        "comparison.txt",
    ):
        assert (base / name).exists()
    manifest = (base / "comparison.txt").read_text(encoding="utf-8")
    assert "extra" in manifest
    assert "difference:" in manifest


# ---- _SampleDebugMixin ------------------------------------------------------


def test_save_match_debug_noop_without_debug_dir() -> None:
    analyzer = _analyzer(None)
    analyzer._save_match_debug(_ctx(), SamplePoint.MID, _loaded(_sample()), None)


def test_save_match_debug_ok_sample_renders_result(tmp_path) -> None:
    analyzer = _analyzer(tmp_path)
    sample = _sample(ok=True, scale=1.1, ecc_score=0.97)
    loaded = _loaded(sample)
    analyzer._save_match_debug(_ctx(), SamplePoint.MID, loaded, None)
    base = tmp_path / "Shot_A_v2" / "mid"
    assert (base / "compare_online_vs_offline.jpg").exists()
    manifest = (base / "comparison.txt").read_text(encoding="utf-8")
    assert "match: OK" in manifest
    assert (base / "result_vs_offline.jpg").exists()  # applied result was rendered


def test_save_match_debug_failed_sample_reports_scores(tmp_path) -> None:
    analyzer = _analyzer(tmp_path)
    sample = _sample(ok=False, message="refine rejected")
    loaded = _loaded(sample, with_raw=False)
    alignment = SimpleNamespace(ecc_score=0.42)
    analyzer._save_match_debug(_ctx(), SamplePoint.MID, loaded, alignment)
    manifest = (tmp_path / "Shot_A_v2" / "mid" / "comparison.txt").read_text(encoding="utf-8")
    assert "match: FAIL ecc=0.4200" in manifest
    assert "refine rejected" in manifest
