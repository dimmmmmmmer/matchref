"""Extract reel / timecode metadata from Resolve timeline items."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from matchref.frame_provider import (
    resolve_media_path,
    source_frame_for_local,
    timeline_frame_for_local,
)


@dataclass
class ClipSourceInfo:
    clip_name: str
    reel_name: str
    source_frame: int
    timeline_frame: int
    media_path: str
    media_basename: str
    source_timecode: str | None = None


def _property_lookup(container: Any, keys: tuple[str, ...]) -> str:
    if container is None:
        return ""
    for key in keys:
        try:
            value = container.GetClipProperty(key) if hasattr(container, "GetClipProperty") else None
            if value:
                return str(value).strip()
        except Exception:
            continue
    try:
        props = container.GetClipProperty()
        if isinstance(props, dict):
            for key in keys:
                if props.get(key):
                    return str(props[key]).strip()
    except Exception:
        pass
    return ""


def reel_name_from_item(timeline_item: Any, media_pool_item: Any, clip_name: str, media_path: str) -> str:
    for source in (media_pool_item, timeline_item):
        value = _property_lookup(
            source,
            ("Reel Name", "Reel", "Tape Name", "Camera Reel", "Roll Card #"),
        )
        if value:
            return value

    if media_path:
        return Path(media_path).stem
    return clip_name


def source_timecode_from_item(
    timeline_item: Any,
    media_pool_item: Any,
    local_frame: int,
) -> str | None:
    keys = ("Start TC", "Start Timecode", "Source Start Timecode", "Timecode")
    for source in (timeline_item, media_pool_item):
        for key in keys:
            try:
                if hasattr(source, "GetProperty"):
                    value = source.GetProperty(key)
                    if value:
                        return str(value)
            except Exception:
                pass
        value = _property_lookup(source, keys)
        if value:
            return value
    return None


def build_clip_source_info(timeline_item: Any, local_frame: int, clip_name: str = "") -> ClipSourceInfo:
    media_item = None
    try:
        media_item = timeline_item.GetMediaPoolItem()
    except Exception:
        pass

    name = clip_name
    if not name:
        try:
            name = timeline_item.GetName() or "Unnamed"
        except Exception:
            name = "Unnamed"

    media_path = resolve_media_path(media_item)
    media_basename = Path(media_path).stem if media_path else ""
    reel = reel_name_from_item(timeline_item, media_item, name, media_path)
    source_frame = source_frame_for_local(timeline_item, local_frame)
    timeline_frame = timeline_frame_for_local(timeline_item, local_frame)
    tc = source_timecode_from_item(timeline_item, media_item, local_frame)

    return ClipSourceInfo(
        clip_name=name,
        reel_name=reel,
        source_frame=source_frame,
        timeline_frame=timeline_frame,
        media_path=media_path,
        media_basename=media_basename,
        source_timecode=tc,
    )
