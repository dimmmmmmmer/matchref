"""Lock-cut hub origin detection."""

from matchref.config import AppConfig
from matchref.lock_cut_align import detect_lock_cut_hub_origin, hub_to_lock_cut_frame


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
