"""Clip selection modes and the auto cascade (selection → color → flags → all)."""

from __future__ import annotations

from typing import Any

from matchref.config import AppConfig
from matchref.selection import get_target_clips_with_source


class _FakeClip:
    def __init__(self, name: str, color: str = "", flags: list[str] | None = None) -> None:
        self._name = name
        self._color = color
        self._flags = flags or []

    def GetName(self) -> str:
        return self._name

    def GetClipColor(self) -> str:
        return self._color

    def GetFlagList(self) -> list[str]:
        return self._flags


class _FakeTimeline:
    def __init__(
        self,
        tracks: dict[int, list[_FakeClip]],
        selected: list[_FakeClip] | None = None,
        current: _FakeClip | None = None,
    ) -> None:
        self._tracks = tracks
        self._selected = selected or []
        self._current = current

    def GetTrackCount(self, kind: str) -> int:
        return max(self._tracks) if self._tracks else 0

    def GetItemListInTrack(self, kind: str, index: int) -> list[_FakeClip]:
        return self._tracks.get(index, [])

    def GetSelectedTimelineItems(self) -> list[_FakeClip]:
        return self._selected

    def GetCurrentVideoItem(self) -> Any:
        return self._current


def _config(mode: str, **overrides: Any) -> AppConfig:
    cfg = AppConfig()
    cfg.set("clip_selection_mode", mode)
    for key, value in overrides.items():
        cfg.set(key, value)
    return cfg


def test_all_filtered_returns_every_video_clip() -> None:
    a, b = _FakeClip("a"), _FakeClip("b")
    timeline = _FakeTimeline({1: [a], 2: [b]})
    clips, source = get_target_clips_with_source(timeline, _config("all_filtered"))
    assert clips == [a, b]
    assert "all video clips (2)" in source


def test_track_mode_uses_configured_track() -> None:
    a, b = _FakeClip("a"), _FakeClip("b")
    timeline = _FakeTimeline({1: [a], 2: [b]})
    clips, source = get_target_clips_with_source(timeline, _config("track", video_track_index=2))
    assert clips == [b]
    assert "track V2" in source


def test_clip_color_mode_filters_by_color() -> None:
    purple = _FakeClip("p", color="Purple")
    plain = _FakeClip("x")
    timeline = _FakeTimeline({1: [purple, plain]})
    clips, source = get_target_clips_with_source(
        timeline, _config("clip_color", selection_clip_color="Purple")
    )
    assert clips == [purple]
    assert "clip color" in source


def test_flagged_mode_filters_by_flag() -> None:
    flagged = _FakeClip("f", flags=["Purple"])
    plain = _FakeClip("x")
    timeline = _FakeTimeline({1: [flagged, plain]})
    clips, _ = get_target_clips_with_source(
        timeline, _config("flagged", selection_flag_color="Purple")
    )
    assert clips == [flagged]


def test_auto_prefers_api_selection() -> None:
    selected = _FakeClip("sel")
    other = _FakeClip("x", color="Purple")
    timeline = _FakeTimeline({1: [selected, other]}, selected=[selected])
    clips, source = get_target_clips_with_source(timeline, _config("auto"))
    assert clips == [selected]
    assert "selected API" in source


def test_auto_falls_back_to_clip_color_then_flags() -> None:
    colored = _FakeClip("c", color="Purple")
    plain = _FakeClip("x")
    timeline = _FakeTimeline({1: [colored, plain]})
    clips, source = get_target_clips_with_source(timeline, _config("auto"))
    assert clips == [colored]
    assert "clip color" in source

    flagged = _FakeClip("f", flags=["Purple"])
    timeline = _FakeTimeline({1: [flagged, plain]})
    clips, source = get_target_clips_with_source(timeline, _config("auto"))
    assert clips == [flagged]
    assert "flags" in source


def test_auto_all_clips_fallback() -> None:
    a, b = _FakeClip("a"), _FakeClip("b")
    timeline = _FakeTimeline({1: [a, b]})
    clips, source = get_target_clips_with_source(timeline, _config("auto"))
    assert clips == [a, b]
    assert "all_filtered fallback" in source


def test_auto_playhead_when_fallback_disabled() -> None:
    current = _FakeClip("cur")
    timeline = _FakeTimeline({1: [_FakeClip("a")]}, current=current)
    clips, source = get_target_clips_with_source(
        timeline, _config("auto", auto_all_clips_fallback=False)
    )
    assert clips == [current]
    assert source == "playhead fallback"


def test_auto_none_when_nothing_matches() -> None:
    timeline = _FakeTimeline({1: [_FakeClip("a")]})
    clips, source = get_target_clips_with_source(
        timeline, _config("auto", auto_all_clips_fallback=False)
    )
    assert clips == []
    assert source == "none"
