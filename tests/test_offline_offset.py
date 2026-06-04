"""offline_timeline_offset_frames must shift only the offline mapping, exactly once."""

from matchref.config import AppConfig
from matchref.conform_edl import EdlEvent
from matchref.conform_index import ConformIndex, OfflineFrameResolver
from tests.test_timeline_context import make_timeline


def _conform_with_event(cfg: AppConfig) -> ConformIndex:
    index = ConformIndex(cfg, timeline=make_timeline())
    index.events = [EdlEvent(1, "A001", "V", "C", 0, 200, 0, 200, clip_name="shot_a")]
    index._has_xml = True
    index._timeline_offset_frames = int(cfg.get("offline_timeline_offset_frames", 0))
    return index


def test_offset_applied_once_on_conform_lookup() -> None:
    cfg = AppConfig()
    cfg.set("offline_timeline_offset_frames", 1)
    index = _conform_with_event(cfg)
    # hub 50 → offline 51 (shifted by exactly one)
    assert index.lookup_by_record_frame(50).offline_frame == 51


def test_offset_not_double_counted_on_timeline_fallback() -> None:
    """A clip not in the conform falls through to the timeline mapping; the offset
    must still be applied exactly once, not twice."""
    cfg = AppConfig()
    cfg.set("offline_timeline_offset_frames", 1)
    cfg.set("offline_mapping_mode", "auto")
    index = _conform_with_event(cfg)
    resolver = OfflineFrameResolver(cfg, conform=index)
    resolver.configure_lock_cut(lock_cut_hub_origin=0)

    # reel + source frame that are NOT in the conform → timeline fallback path
    mapping = resolver.resolve(
        timeline_frame=3295, reel_name="Stock Bonfire", source_frame=5000
    )
    assert mapping.offline_frame == 3296  # 3295 + 1, not + 2


def test_zero_offset_is_identity() -> None:
    cfg = AppConfig()
    cfg.set("offline_timeline_offset_frames", 0)
    index = _conform_with_event(cfg)
    resolver = OfflineFrameResolver(cfg, conform=index)
    resolver.configure_lock_cut(lock_cut_hub_origin=0)
    mapping = resolver.resolve(
        timeline_frame=3295, reel_name="Stock Bonfire", source_frame=5000
    )
    assert mapping.offline_frame == 3295
