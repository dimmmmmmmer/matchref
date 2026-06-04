"""Retime-aware mapping of clip-local frames to source media frames."""

from matchref.config import AppConfig
from matchref.frame_provider import source_frame_for_local


class FakeItem:
    def __init__(self, source_start: int, source_end: int, duration: int) -> None:
        self._source_start = source_start
        self._source_end = source_end
        self._duration = duration

    def GetSourceStartFrame(self) -> int:
        return self._source_start

    def GetSourceEndFrame(self) -> int:
        return self._source_end

    def GetDuration(self) -> int:
        return self._duration


def test_no_retime_is_one_to_one() -> None:
    cfg = AppConfig()
    item = FakeItem(source_start=100, source_end=199, duration=100)
    assert source_frame_for_local(item, 0, config=cfg) == 100
    assert source_frame_for_local(item, 50, config=cfg) == 150
    assert source_frame_for_local(item, 99, config=cfg) == 199


def test_slow_motion_half_speed() -> None:
    # 100 timeline frames consume 50 source frames -> 0.5x speed.
    cfg = AppConfig()
    item = FakeItem(source_start=200, source_end=249, duration=100)
    assert source_frame_for_local(item, 0, config=cfg) == 200
    assert source_frame_for_local(item, 99, config=cfg) == 249
    assert source_frame_for_local(item, 50, config=cfg) == 225


def test_reverse_clip() -> None:
    cfg = AppConfig()
    item = FakeItem(source_start=300, source_end=201, duration=100)
    assert source_frame_for_local(item, 0, config=cfg) == 300
    assert source_frame_for_local(item, 99, config=cfg) == 201


def test_disabled_falls_back_to_one_to_one() -> None:
    cfg = AppConfig()
    cfg.set("retime_aware_source_mapping", False)
    item = FakeItem(source_start=200, source_end=249, duration=100)
    assert source_frame_for_local(item, 50, config=cfg) == 250


def test_missing_end_frame_falls_back() -> None:
    class NoEnd:
        def GetSourceStartFrame(self) -> int:
            return 10

        def GetDuration(self) -> int:
            return 100

    cfg = AppConfig()
    assert source_frame_for_local(NoEnd(), 5, config=cfg) == 15
