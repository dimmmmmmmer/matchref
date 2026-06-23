"""Offline frame mapping: timeline fallback and conform record/reel lookups."""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.conform_edl import EdlEvent
from matchref.conform_index import ConformIndex, OfflineFrameResolver
from matchref.models import OfflineMappingSource


def _event(
    n: int, reel: str, src_in: int, src_out: int, rec_in: int, rec_out: int, clip: str = ""
) -> EdlEvent:
    return EdlEvent(
        event_number=n,
        reel=reel,
        track="V",
        transition="C",
        src_in=src_in,
        src_out=src_out,
        rec_in=rec_in,
        rec_out=rec_out,
        clip_name=clip,
    )


def _index(events: list[EdlEvent], *, has_xml: bool = False) -> ConformIndex:
    idx = ConformIndex(AppConfig())
    idx.events = events
    idx._has_edl = not has_xml
    idx._has_xml = has_xml
    return idx


def _resolver(conform: ConformIndex | None = None, origin: int = 0) -> OfflineFrameResolver:
    r = OfflineFrameResolver(AppConfig(), conform)
    r.configure_lock_cut(lock_cut_hub_origin=origin)
    return r


# --- timeline fallback (no conform) -----------------------------------------


def test_timeline_fallback_subtracts_origin() -> None:
    r = _resolver(origin=100)
    m = r.resolve(timeline_frame=250, reel_name="x", source_frame=0)
    assert m.source is OfflineMappingSource.TIMELINE
    assert m.offline_frame == 150  # 250 - origin 100


def test_timeline_fallback_clamps_at_zero() -> None:
    r = _resolver(origin=100)
    assert r.resolve(timeline_frame=40, reel_name="x", source_frame=0).offline_frame == 0


def test_conform_required_but_missing_is_none_source() -> None:
    cfg = AppConfig()
    cfg.set("offline_mapping_mode", "conform")
    r = OfflineFrameResolver(cfg, None)
    r.configure_lock_cut(lock_cut_hub_origin=0)
    m = r.resolve(timeline_frame=10, reel_name="x", source_frame=0)
    assert m.source is OfflineMappingSource.NONE


# --- record-frame lookup ----------------------------------------------------


def test_lookup_by_record_frame_within_event() -> None:
    idx = _index([_event(1, "A", 1000, 1096, 0, 96), _event(2, "B", 2000, 2120, 96, 216)])
    m = idx.lookup_by_record_frame(100)
    assert m is not None
    assert m.matched_event == 2
    assert m.offline_frame == 100
    assert m.source is OfflineMappingSource.EDL


def test_lookup_by_record_frame_outside_returns_none() -> None:
    idx = _index([_event(1, "A", 1000, 1096, 0, 96)])
    assert idx.lookup_by_record_frame(500) is None


def test_empty_index_is_unavailable() -> None:
    idx = _index([])
    assert idx.is_available is False
    assert idx.lookup_by_record_frame(0) is None


# --- reel/source lookup -----------------------------------------------------


def test_lookup_by_reel_and_source_maps_through_record() -> None:
    idx = _index([_event(1, "SHOTA", 1000, 1096, 500, 596, clip="SHOTA")])
    m = idx.lookup("SHOTA", 1010)
    assert m is not None
    assert m.offline_frame == 510  # rec_in 500 + (1010 - src_in 1000)


def test_lookup_source_out_of_range_returns_none() -> None:
    idx = _index([_event(1, "SHOTA", 1000, 1096, 500, 596, clip="SHOTA")])
    assert idx.lookup("SHOTA", 5000) is None


def test_find_event_prefers_matching_reel() -> None:
    # Two events cover the same source range; the one whose reel matches wins.
    idx = _index(
        [
            _event(1, "OTHER", 1000, 1096, 0, 96, clip="OTHER"),
            _event(2, "SHOTA", 1000, 1096, 500, 596, clip="SHOTA"),
        ]
    )
    m = idx.lookup("SHOTA", 1010)
    assert m is not None
    assert m.matched_event == 2
    assert m.offline_frame == 510  # mapped through event 2's record in


# --- resolver prefers conform record mapping --------------------------------


def test_resolver_uses_conform_record_mapping_first() -> None:
    idx = _index([_event(1, "A", 1000, 1096, 0, 96)])
    r = _resolver(conform=idx, origin=0)
    m = r.resolve(timeline_frame=50, reel_name="A", source_frame=1050)
    assert m.source is OfflineMappingSource.EDL
    assert m.offline_frame == 50
