"""Start-timecode offset math."""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.timecode import TimecodeFormat
from matchref.timecode_align import (
    auto_align_enabled,
    parse_tc_or_none,
    start_offset_frames,
)

FMT = TimecodeFormat(fps=24.0, drop_frame=False)


def test_offset_is_timeline_minus_reference() -> None:
    # Timeline starts at 01:00:00:00, reference at 00:00:00:00 → 86400 frames @ 24.
    assert start_offset_frames("01:00:00:00", "00:00:00:00", FMT) == 86400


def test_equal_start_is_zero() -> None:
    assert start_offset_frames("01:00:00:00", "01:00:00:00", FMT) == 0


def test_reference_ahead_is_negative() -> None:
    assert start_offset_frames("00:00:00:00", "00:00:01:00", FMT) == -24


def test_missing_or_invalid_tc_is_zero() -> None:
    assert start_offset_frames("", "01:00:00:00", FMT) == 0
    assert start_offset_frames("01:00:00:00", "", FMT) == 0
    assert start_offset_frames("garbage", "01:00:00:00", FMT) == 0


def test_parse_tc_or_none() -> None:
    assert parse_tc_or_none("00:00:01:00", FMT) == 24
    assert parse_tc_or_none("", FMT) is None
    assert parse_tc_or_none("nope", FMT) is None


def test_auto_align_default_off() -> None:
    cfg = AppConfig()
    assert auto_align_enabled(cfg) is False
    cfg.set("auto_align_start_timecode", True)
    assert auto_align_enabled(cfg) is True
