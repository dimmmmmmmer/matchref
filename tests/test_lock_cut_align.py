"""Lock-cut hub origin detection."""

from matchref.config import AppConfig
from matchref.lock_cut_align import (
    detect_lock_cut_hub_origin,
    hub_to_lock_cut_frame,
    reference_coverage_warning,
)


def test_coverage_ok_when_reference_covers_timeline() -> None:
    assert reference_coverage_warning(48_000, 50_000, 24.0) is None


def test_coverage_warns_when_reference_too_short() -> None:
    msg = reference_coverage_warning(1_000, 50_000, 24.0)
    assert msg is not None and "shorter" in msg


def test_coverage_warns_on_zero_frames() -> None:
    msg = reference_coverage_warning(0, 50_000, 24.0)
    assert msg is not None and "0 readable frames" in msg


def test_coverage_none_when_timeline_span_unknown() -> None:
    assert reference_coverage_warning(1_000, 0, 24.0) is None


def test_full_timeline_origin_zero() -> None:
    cfg = AppConfig()
    assert (
        detect_lock_cut_hub_origin(
            timeline_end_frame=50_000,
            lock_cut_frame_count=50_000,
            clip_starts=[3939, 4307],
            config=cfg,
        )
        == 0
    )


def test_trimmed_lock_cut_origin_at_first_clip() -> None:
    cfg = AppConfig()
    origin = detect_lock_cut_hub_origin(
        timeline_end_frame=50_000,
        lock_cut_frame_count=46_000,
        clip_starts=[3939, 4307],
        config=cfg,
    )
    assert origin == 3939


def test_hub_to_lock_cut() -> None:
    assert hub_to_lock_cut_frame(4307, lock_cut_hub_origin=3939) == 368
