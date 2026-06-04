"""Timeline clip selection for batch conform."""

from __future__ import annotations

import logging
from typing import Any

from matchref.config import AppConfig
from matchref.resolve_api import (
    _items_with_clip_color,
    _items_with_flag,
    _try_get_selected_items,
    iter_video_timeline_items,
)


def _try_alternate_selection(timeline: Any) -> list[Any]:
    """Probe undocumented / version-specific selection APIs."""
    items: list[Any] = []
    for method_name in (
        "GetSelectedTimelineItems",
        "GetSelectedItems",
        "GetTimelineItemSelection",
        "GetCurrentTimelineSelection",
    ):
        method = getattr(timeline, method_name, None)
        if not callable(method):
            continue
        try:
            raw = method()
        except Exception:
            continue
        if not raw:
            continue
        if isinstance(raw, dict):
            items.extend(v for v in raw.values() if v is not None)
        elif isinstance(raw, list):
            items.extend(i for i in raw if i is not None)
        if items:
            return items
    return []


def _selection_color(config: AppConfig) -> str:
    """Clip color / flag color name (e.g. Purple, Green, Orange)."""
    return str(
        config.get("selection_clip_color")
        or config.get("selection_flag_color")
        or "Purple"
    ).strip()


def get_target_clips_with_source(
    timeline: Any,
    config: AppConfig,
    logger: logging.Logger | None = None,
) -> tuple[list[Any], str]:
    """
    Return (clips, source_description).

    Resolve UI multi-select is usually invisible to the API.
    Use Clip Color (правый клик → Clip Color) or clip_selection_mode: all_filtered.
    """
    log = logger or logging.getLogger("matchref")
    mode = str(config.get("clip_selection_mode", "auto")).lower()
    all_items = iter_video_timeline_items(timeline)
    color = _selection_color(config)

    if mode == "all" or mode == "all_filtered":
        return all_items, f"all video clips ({len(all_items)})"

    if mode == "track":
        track_index = int(config.get("video_track_index", 1))
        try:
            track_items = timeline.GetItemListInTrack("video", track_index)
        except Exception:
            return [], "track (empty)"
        if isinstance(track_items, dict):
            clips = [v for v in track_items.values() if v is not None]
        else:
            clips = list(track_items or [])
        return clips, f"track V{track_index} ({len(clips)})"

    if mode == "playhead":
        try:
            item = timeline.GetCurrentVideoItem()
            return ([item] if item else []), "playhead"
        except Exception:
            return [], "playhead (empty)"

    if mode == "clip_color" or mode == "colored":
        clips = _items_with_clip_color(all_items, color)
        return clips, f"clip color {color!r} ({len(clips)})"

    if mode == "flagged":
        clips = _items_with_flag(all_items, color)
        return clips, f"flags {color!r} ({len(clips)})"

    if mode == "selected":
        clips = _try_get_selected_items(timeline) or _try_alternate_selection(timeline)
        return clips, f"timeline selection API ({len(clips)})"

    # auto: API selection → clip color → flags → all clips
    selected = _try_get_selected_items(timeline) or _try_alternate_selection(timeline)
    if selected:
        log.info("Using %d clip(s) from Resolve selection API.", len(selected))
        return selected, f"selected API ({len(selected)})"

    if color:
        colored = _items_with_clip_color(all_items, color)
        if colored:
            log.info("Using %d clip(s) with Clip Color %r.", len(colored), color)
            return colored, f"clip color {color!r} ({len(colored)})"

        flagged = _items_with_flag(all_items, color)
        if flagged:
            return flagged, f"flags {color!r} ({len(flagged)})"

    if bool(config.get("auto_all_clips_fallback", True)):
        log.info(
            "No clip color %r — processing all %d video clips (minus reference).",
            color,
            len(all_items),
        )
        return all_items, f"all_filtered fallback ({len(all_items)})"

    try:
        item = timeline.GetCurrentVideoItem()
        if item:
            return [item], "playhead fallback"
    except Exception:
        pass
    return [], "none"


def _clip_name(item: Any) -> str:
    try:
        return item.GetName() or "?"
    except Exception:
        return "?"
