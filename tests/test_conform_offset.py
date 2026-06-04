"""Tests for conform → Resolve hub alignment (abstract frame numbers)."""

from matchref.config import AppConfig
from matchref.conform_edl import EdlEvent
from matchref.conform_index import ConformIndex
from matchref.timebase import detect_conform_origin_frames, resolve_timeline_to_offline_frame
from matchref.timecode import TimecodeFormat, parse_timecode
from tests.test_timeline_context import make_timeline

# Abstract editorial values — not tied to any show reel.
_SEQ_ORIGIN = 10_000
_LEADER_GAP = 50
_FPS = 24.0


def test_parse_timecode_at_fps() -> None:
    fmt = TimecodeFormat(fps=_FPS)
    assert parse_timecode("01:00:00:00", fmt) == 86400


def test_detect_origin_at_declared_sequence_start() -> None:
    events = [EdlEvent(1, "A", "V", "C", 0, 100, _SEQ_ORIGIN, _SEQ_ORIGIN + 100)]
    assert detect_conform_origin_frames(events, _SEQ_ORIGIN, _FPS) == _SEQ_ORIGIN


def test_detect_origin_uses_first_cut_not_only_sequence_tag() -> None:
    first_cut = _SEQ_ORIGIN + _LEADER_GAP
    events = [EdlEvent(1, "A", "V", "C", 0, 100, first_cut, first_cut + 100)]
    assert detect_conform_origin_frames(events, _SEQ_ORIGIN, _FPS) == first_cut


def test_detect_origin_already_hub_aligned() -> None:
    events = [EdlEvent(1, "A", "V", "C", 0, 100, 0, 100)]
    assert detect_conform_origin_frames(events, _SEQ_ORIGIN, _FPS) == 0


def test_normalize_to_resolve_hub() -> None:
    cfg = AppConfig()
    cfg.set("normalize_conform_to_resolve", True)

    index = ConformIndex(cfg, timeline=make_timeline())
    index.events = [
        EdlEvent(
            event_number=1,
            reel="A001",
            track="V",
            transition="C",
            src_in=0,
            src_out=100,
            rec_in=_SEQ_ORIGIN,
            rec_out=_SEQ_ORIGIN + 100,
            clip_name="shot_a",
        )
    ]
    index._sequence_start_frames = _SEQ_ORIGIN
    index._has_xml = True
    index._normalize_conform_to_resolve_hub()

    assert index.events[0].rec_in == 0
    mapping = index.lookup_by_record_frame(50)
    assert mapping is not None
    assert mapping.offline_frame == 50


def test_normalize_leader_gap_after_sequence_tag() -> None:
    first_cut = _SEQ_ORIGIN + _LEADER_GAP
    cfg = AppConfig()
    index = ConformIndex(cfg, timeline=make_timeline())
    index.events = [
        EdlEvent(1, "A", "V", "C", 0, 100, first_cut, first_cut + 100, clip_name="shot"),
    ]
    index._sequence_start_frames = _SEQ_ORIGIN
    index._normalize_conform_to_resolve_hub()
    assert index.events[0].rec_in == 0
    assert index.lookup_by_record_frame(0).offline_frame == 0


def test_hub_to_offline_with_reference_origin() -> None:
    assert resolve_timeline_to_offline_frame(100, reference_origin_frames=0) == 100
    assert resolve_timeline_to_offline_frame(100, reference_origin_frames=86400) == 0
