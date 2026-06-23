"""SMPTE timecode parse/format conversions."""

from __future__ import annotations

import pytest

from matchref.timecode import (
    TimecodeFormat,
    format_timecode,
    infer_fps_from_edl_header,
    parse_timecode,
)


# Integer rates only: non-drop parse uses the fractional rate on purpose, so
# 23.976 deliberately does not round-trip (see test_fps).
@pytest.mark.parametrize("fps", [24.0, 25.0, 30.0])
@pytest.mark.parametrize("frame", [0, 1, 23, 24, 25, 1000, 86399])
def test_non_drop_roundtrip(fps: float, frame: int) -> None:
    fmt = TimecodeFormat(fps=fps, drop_frame=False)
    assert parse_timecode(format_timecode(frame, fmt), fmt) == frame


def test_non_drop_known_value() -> None:
    fmt = TimecodeFormat(fps=24.0)
    assert format_timecode(24, fmt) == "00:00:01:00"
    assert parse_timecode("00:00:01:00", fmt) == 24
    assert parse_timecode("01:00:00:00", fmt) == 86400


def test_drop_frame_roundtrip() -> None:
    fmt = TimecodeFormat(fps=29.97, drop_frame=True)
    for frame in (0, 1, 2, 1798, 17982, 100000):
        assert parse_timecode(format_timecode(frame, fmt), fmt) == frame


def test_drop_frame_uses_semicolon() -> None:
    fmt = TimecodeFormat(fps=29.97, drop_frame=True)
    assert ";" in format_timecode(1800, fmt)


def test_parse_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_timecode("not a timecode", TimecodeFormat())


def test_parse_ignores_subframes() -> None:
    fmt = TimecodeFormat(fps=24.0)
    assert parse_timecode("00:00:01:00:00", fmt) == 24


def test_infer_fps_from_edl_header() -> None:
    assert infer_fps_from_edl_header("FCM: DROP FRAME", 24.0) == TimecodeFormat(29.97, True)
    assert infer_fps_from_edl_header("FCM: NON-DROP FRAME 29.97", 24.0).drop_frame is False
    assert infer_fps_from_edl_header("25 fps", 24.0).fps == 25.0
    assert infer_fps_from_edl_header("nothing useful", 23.976).fps == 23.976
