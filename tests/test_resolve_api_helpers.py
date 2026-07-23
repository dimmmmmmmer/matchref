"""Resolve Scripting API helpers against duck-typed stand-ins (no live Resolve)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from matchref.resolve_api import (
    TimelineMediaInfo,
    _items_with_clip_color,
    _items_with_flag,
    _normalize_color_name,
    _read_setting,
    get_project_context,
    get_timeline_media_info,
    iter_video_timeline_items,
    timeline_item_info,
)


def _timeline(settings: dict[str, object], **extra: object) -> SimpleNamespace:
    return SimpleNamespace(GetSetting=lambda key: settings.get(key), **extra)


# ---- _read_setting ----------------------------------------------------------


def test_read_setting_handles_missing_and_blank() -> None:
    assert _read_setting(None, "x") is None
    assert _read_setting(SimpleNamespace(), "x") is None  # no GetSetting at all
    assert _read_setting(_timeline({}), "x") is None
    assert _read_setting(_timeline({"x": "  "}), "x") is None
    assert _read_setting(_timeline({"x": " 25 "}), "x") == "25"


def test_read_setting_swallows_api_errors() -> None:
    def _boom(key: str) -> str:
        raise RuntimeError("api down")

    assert _read_setting(SimpleNamespace(GetSetting=_boom), "x") is None


# ---- get_timeline_media_info ------------------------------------------------


def test_media_info_empty_without_timeline() -> None:
    info = get_timeline_media_info(None)
    assert info == TimelineMediaInfo()


def test_media_info_reads_fps_resolution_dropframe_start_tc() -> None:
    timeline = _timeline(
        {
            "timelineFrameRate": "25",
            "timelineResolutionWidth": "1920",
            "timelineResolutionHeight": "1080",
            "timelineDropFrameTimecode": "1",
        },
        GetStartTimecode=lambda: " 01:00:00:00 ",
    )
    info = get_timeline_media_info(timeline)
    assert info.fps == 25.0
    assert (info.width, info.height) == (1920, 1080)
    assert info.drop_frame
    assert info.start_timecode == "01:00:00:00"


def test_media_info_falls_back_to_project_settings() -> None:
    project = _timeline({"timelineFrameRate": "24", "timelineResolutionWidth": "3840"})
    timeline = _timeline({}, GetProject=lambda: project)
    info = get_timeline_media_info(timeline)
    assert info.fps == 24.0
    assert info.width == 3840


def test_media_info_skips_unparsable_fps() -> None:
    timeline = _timeline({"timelineFrameRate": "abc", "timelinePlaybackFrameRate": "30"})
    info = get_timeline_media_info(timeline)
    assert info.fps == 30.0


# ---- get_project_context ----------------------------------------------------


def test_project_context_returns_triple() -> None:
    timeline = object()
    project = SimpleNamespace(GetCurrentTimeline=lambda: timeline)
    manager = SimpleNamespace(GetCurrentProject=lambda: project)
    resolve = SimpleNamespace(GetProjectManager=lambda: manager)
    assert get_project_context(resolve) == (manager, project, timeline)


def test_project_context_raises_without_project_or_timeline() -> None:
    manager = SimpleNamespace(GetCurrentProject=lambda: None)
    resolve = SimpleNamespace(GetProjectManager=lambda: manager)
    with pytest.raises(RuntimeError, match="No project"):
        get_project_context(resolve)

    project = SimpleNamespace(GetCurrentTimeline=lambda: None)
    manager2 = SimpleNamespace(GetCurrentProject=lambda: project)
    resolve2 = SimpleNamespace(GetProjectManager=lambda: manager2)
    with pytest.raises(RuntimeError, match="No timeline"):
        get_project_context(resolve2)


# ---- iter_video_timeline_items ----------------------------------------------


def test_iter_video_items_handles_dict_list_and_none() -> None:
    a, b, c = object(), object(), object()
    tracks = {1: {"k1": a, "k2": None}, 2: [b, None, c], 3: None}
    timeline = SimpleNamespace(
        GetTrackCount=lambda kind: 3,
        GetItemListInTrack=lambda kind, index: tracks[index],
    )
    assert iter_video_timeline_items(timeline) == [a, b, c]


def test_iter_video_items_empty_on_api_errors() -> None:
    def _boom(kind: str) -> int:
        raise RuntimeError("api down")

    assert iter_video_timeline_items(SimpleNamespace(GetTrackCount=_boom)) == []


# ---- flag / clip-color selection --------------------------------------------


def _flagged_item(flags: list[str]) -> SimpleNamespace:
    return SimpleNamespace(GetFlagList=lambda: flags)


def test_normalize_color_name() -> None:
    assert _normalize_color_name("  Blue ") == "blue"


def test_items_with_flag_matches_case_insensitively() -> None:
    match = _flagged_item(["Blue", "Red"])
    other = _flagged_item(["Green"])
    broken = SimpleNamespace(GetFlagList=lambda: (_ for _ in ()).throw(RuntimeError()))
    assert _items_with_flag([match, other, broken], "blue") == [match]


def test_items_with_clip_color_matches() -> None:
    match = SimpleNamespace(GetClipColor=lambda: "Teal")
    empty = SimpleNamespace(GetClipColor=lambda: "")
    broken = SimpleNamespace(GetClipColor=lambda: (_ for _ in ()).throw(RuntimeError()))
    assert _items_with_clip_color([match, empty, broken], "teal") == [match]


# ---- timeline_item_info -----------------------------------------------------


def test_timeline_item_info_reads_duck_item() -> None:
    item = SimpleNamespace(
        GetName=lambda: "Shot A",
        GetTrackTypeAndIndex=lambda: ("video", 2),
        GetDuration=lambda: 48,
        GetUniqueId=lambda: "uid-1",
    )
    info = timeline_item_info(item)
    assert info == {
        "name": "Shot A",
        "track_type": "video",
        "track_index": 2,
        "duration": 48,
        "unique_id": "uid-1",
    }


def test_timeline_item_info_defaults_on_broken_item() -> None:
    info = timeline_item_info(object())
    assert info["name"] == "Unnamed"
    assert info["track_type"] == "video"
    assert info["duration"] == 1
    assert info["unique_id"] == ""
