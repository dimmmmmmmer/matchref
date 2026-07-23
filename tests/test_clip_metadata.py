"""Reel/timecode metadata extraction from duck-typed timeline items."""

from __future__ import annotations

from types import SimpleNamespace

from matchref.clip_metadata import (
    _property_lookup,
    build_clip_source_info,
    reel_name_from_item,
    source_timecode_from_item,
)


def _props_item(props: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(
        GetClipProperty=lambda key=None: props if key is None else props.get(key)
    )


# ---- _property_lookup -------------------------------------------------------


def test_property_lookup_none_container() -> None:
    assert _property_lookup(None, ("Reel Name",)) == ""


def test_property_lookup_by_key_and_strip() -> None:
    item = _props_item({"Reel Name": " A001 "})
    assert _property_lookup(item, ("Tape Name", "Reel Name")) == "A001"


def test_property_lookup_falls_back_to_props_dict() -> None:
    # Per-key lookups return None; the argless dict call has the value.
    item = SimpleNamespace(
        GetClipProperty=lambda key=None: {"Tape Name": "T42"} if key is None else None
    )
    assert _property_lookup(item, ("Tape Name",)) == "T42"


def test_property_lookup_survives_raising_container() -> None:
    def _boom(key=None):
        raise RuntimeError("api down")

    assert _property_lookup(SimpleNamespace(GetClipProperty=_boom), ("Reel Name",)) == ""


# ---- reel_name_from_item ----------------------------------------------------


def test_reel_name_prefers_media_pool_property() -> None:
    media = _props_item({"Reel Name": "A001"})
    item = _props_item({"Reel Name": "B002"})
    assert reel_name_from_item(item, media, "clip", "/m/shot.mov") == "A001"


def test_reel_name_falls_back_to_media_stem_then_clip_name() -> None:
    empty = _props_item({})
    assert reel_name_from_item(empty, empty, "clip", "/m/shot_01.mov") == "shot_01"
    assert reel_name_from_item(empty, empty, "clip", "") == "clip"


# ---- source_timecode_from_item ----------------------------------------------


def test_source_timecode_via_get_property() -> None:
    item = SimpleNamespace(GetProperty=lambda key: "01:00:00:00" if key == "Start TC" else None)
    assert source_timecode_from_item(item, None, 0) == "01:00:00:00"


def test_source_timecode_via_clip_property_fallback() -> None:
    media = _props_item({"Start Timecode": "02:00:00:00"})
    assert source_timecode_from_item(SimpleNamespace(), media, 0) == "02:00:00:00"


def test_source_timecode_none_when_absent() -> None:
    assert source_timecode_from_item(SimpleNamespace(), None, 0) is None


# ---- build_clip_source_info -------------------------------------------------


def _timeline_item() -> SimpleNamespace:
    media = SimpleNamespace(
        GetClipProperty=lambda key=None: (
            {
                "File Path": "/m/shot_01.mov",
                "Reel Name": "A001",
            }.get(key)
            if key
            else None
        )
    )
    return SimpleNamespace(
        GetMediaPoolItem=lambda: media,
        GetName=lambda: "Shot A",
        GetSourceStartFrame=lambda: 100,
        GetSourceEndFrame=lambda: 147,
        GetStart=lambda: 1000,
        GetDuration=lambda: 48,
        GetProperty=lambda key: None,
    )


def test_build_clip_source_info_full_item() -> None:
    info = build_clip_source_info(_timeline_item(), 10)
    assert info.clip_name == "Shot A"
    assert info.reel_name == "A001"
    assert info.media_path == "/m/shot_01.mov"
    assert info.media_basename == "shot_01"
    assert info.timeline_frame == 1010
    assert info.source_frame >= 100  # mapped into the clip's source span


def test_build_clip_source_info_broken_item_defaults() -> None:
    info = build_clip_source_info(object(), 0)
    assert info.clip_name == "Unnamed"
    assert info.media_path == ""
    assert info.media_basename == ""
    assert info.reel_name == "Unnamed"  # falls back to the clip name


def test_build_clip_source_info_keeps_explicit_name() -> None:
    info = build_clip_source_info(object(), 0, clip_name="Named")
    assert info.clip_name == "Named"
    assert info.reel_name == "Named"
