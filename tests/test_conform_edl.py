"""CMX 3600 EDL parsing."""

from __future__ import annotations

from matchref.conform_edl import parse_edl_text
from matchref.timecode import TimecodeFormat, parse_timecode

_EDL = """TITLE: MY SEQUENCE
FCM: NON-DROP FRAME

001  AX       V     C        01:00:00:00 01:00:04:00 00:00:00:00 00:00:04:00
* FROM CLIP NAME: SHOT_01.mov
* SOURCE FILE: /media/SHOT_01.mov

002  TAPE02   V     C        02:00:00:00 02:00:05:00 00:00:04:00 00:00:09:00
* FROM CLIP NAME: SHOT_02.mov
* TAPE NAME: TAPE02
"""


def test_parses_title_and_event_count() -> None:
    res = parse_edl_text(_EDL, default_fps=24.0)
    assert res.title == "MY SEQUENCE"
    assert len(res.events) == 2


def test_event_fields_and_metadata() -> None:
    res = parse_edl_text(_EDL, default_fps=24.0)
    e1, e2 = res.events
    assert e1.event_number == 1
    assert e1.reel == "AX"
    assert e1.clip_name == "SHOT_01.mov"
    assert e1.source_file == "/media/SHOT_01.mov"
    assert e2.reel == "TAPE02"
    assert e2.tape_name == "TAPE02"
    assert e2.clip_name == "SHOT_02.mov"


def test_record_timecodes_in_frames() -> None:
    fmt = TimecodeFormat(fps=24.0, drop_frame=False)
    res = parse_edl_text(_EDL, default_fps=24.0)
    e1, e2 = res.events
    assert e1.rec_in == parse_timecode("00:00:00:00", fmt)  # 0
    assert e1.rec_out == parse_timecode("00:00:04:00", fmt)  # 96
    assert e1.duration == 96  # src 01:00:00:00 -> 01:00:04:00
    assert e2.rec_in == 96  # back-to-back with event 1


def test_drop_frame_header_sets_format() -> None:
    res = parse_edl_text("FCM: DROP FRAME\n", default_fps=24.0)
    assert res.timecode_format.drop_frame is True


def test_garbage_and_blank_lines_ignored() -> None:
    text = "\n\nrandom noise line\n* FROM CLIP NAME: orphan (no event)\n"
    res = parse_edl_text(text)
    assert res.events == []
