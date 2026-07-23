"""Timebase hub math, AppConfig persistence, TimelineContext construction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from matchref.config import AppConfig, _deep_merge
from matchref.conform_edl import EdlEvent
from matchref.timebase import (
    describe_timebase_log,
    detect_conform_origin_frames,
    resolve_timeline_to_offline_frame,
)
from matchref.timecode import TimecodeFormat
from matchref.timeline_context import TimelineContext, TimelineContextError


def _event(rec_in: int) -> EdlEvent:
    return EdlEvent(
        event_number=1,
        reel="A001",
        track="V",
        transition="C",
        src_in=0,
        src_out=10,
        rec_in=rec_in,
        rec_out=rec_in + 10,
    )


# ---- timebase ---------------------------------------------------------------


def test_conform_origin_empty_events() -> None:
    assert detect_conform_origin_frames([], 0, 25.0) == 0


def test_conform_origin_already_aligned_within_tolerance() -> None:
    # First cut within ~1s of zero → treated as already aligned.
    assert detect_conform_origin_frames([_event(10)], 0, 25.0) == 0


def test_conform_origin_shifts_to_first_cut() -> None:
    assert detect_conform_origin_frames([_event(3600), _event(4000)], 0, 25.0) == 3600


def test_resolve_timeline_to_offline_frame_math() -> None:
    assert resolve_timeline_to_offline_frame(100) == 100
    assert resolve_timeline_to_offline_frame(100, reference_origin_frames=40) == 60
    assert (
        resolve_timeline_to_offline_frame(100, reference_origin_frames=40, timeline_offset_frames=5)
        == 65
    )
    assert resolve_timeline_to_offline_frame(10, reference_origin_frames=50) == 0  # clamped


def test_describe_timebase_log_mentions_shifts() -> None:
    fmt = TimecodeFormat(fps=25.0, drop_frame=False)
    aligned = describe_timebase_log(0, 0, fmt)
    assert any("already aligned" in line for line in aligned)
    assert any("same frame index" in line for line in aligned)
    shifted = describe_timebase_log(3600, 100, fmt)
    assert any("subtract" in line and "3600 frames" in line for line in shifted)
    assert any("subtract" in line and "100 frames" in line for line in shifted)


# ---- AppConfig --------------------------------------------------------------


def test_deep_merge_nested_dicts() -> None:
    base = {"a": 1, "nest": {"x": 1, "y": 2}}
    merged = _deep_merge(base, {"nest": {"y": 3}, "b": 2})
    assert merged == {"a": 1, "nest": {"x": 1, "y": 3}, "b": 2}
    assert base["nest"]["y"] == 2  # base untouched


def test_config_set_get_and_default() -> None:
    config = AppConfig()
    assert config.get("no_such_key", "fallback") == "fallback"
    config.set("no_such_key", 42)
    assert config.get("no_such_key") == 42


def test_config_save_and_reload_roundtrip(tmp_path) -> None:
    config = AppConfig()
    config.set("offline_reference_path", "/ref/lockcut.mov")
    target = tmp_path / "user_config.json"
    config.save(target)
    assert config.path == target

    reloaded = AppConfig(target)
    assert reloaded.get("offline_reference_path") == "/ref/lockcut.mov"
    # Defaults still merged underneath the user file.
    assert reloaded.get("edit_match_mode") is not None


# ---- TimelineContext --------------------------------------------------------


def _timeline(settings: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(GetSetting=lambda key: settings.get(key))


def test_timeline_context_from_resolve() -> None:
    ctx = TimelineContext.from_resolve(
        _timeline(
            {
                "timelineFrameRate": "25",
                "timelineResolutionWidth": "1920",
                "timelineResolutionHeight": "1080",
            }
        )
    )
    assert ctx.fps == 25.0
    assert ctx.resolution == (1920, 1080)


def test_timeline_context_requires_fps_and_resolution() -> None:
    with pytest.raises(TimelineContextError, match="frame rate"):
        TimelineContext.from_resolve(_timeline({}))
    with pytest.raises(TimelineContextError, match="resolution"):
        TimelineContext.from_resolve(_timeline({"timelineFrameRate": "25"}))


def test_analysis_max_width_cap() -> None:
    ctx = TimelineContext(fps=25.0, width=1920, height=1080)
    config = AppConfig()
    config.set("analysis_max_width", 0)
    assert ctx.analysis_max_width(config) == 1920
    config.set("analysis_max_width", 960)
    assert ctx.analysis_max_width(config) == 960
