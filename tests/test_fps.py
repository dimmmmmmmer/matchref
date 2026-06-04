"""FPS and XML rate tests."""

from matchref.fps import FPS_23976, effective_fps_from_xml_rate, fps_near_equal
from matchref.timecode import TimecodeFormat, parse_timecode


def test_xml_ntsc_24_is_23976() -> None:
    assert abs(effective_fps_from_xml_rate(24.0, True) - FPS_23976) < 0.001


def test_fps_near_equal_24_vs_23976() -> None:
    assert not fps_near_equal(24.0, FPS_23976)
    assert fps_near_equal(FPS_23976, 23.976)


def test_timecode_parsing_uses_format_fps() -> None:
    fmt = TimecodeFormat(fps=FPS_23976)
    at_24 = parse_timecode("01:00:00:00", TimecodeFormat(fps=24.0))
    at_23976 = parse_timecode("01:00:00:00", fmt)
    assert at_24 == 86400
    assert at_23976 < at_24
