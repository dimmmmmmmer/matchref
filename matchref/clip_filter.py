"""Filter timeline clips for batch film conform (skip lock cut / reference on timeline)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from matchref.config import AppConfig
from matchref.frame_provider import resolve_media_path


def _norm_path(path: str) -> str:
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(path))
    except OSError:
        return os.path.normcase(path)


def is_reference_media(media_path: str, offline_reference: str) -> bool:
    if not media_path or not offline_reference:
        return False
    a = _norm_path(media_path)
    b = _norm_path(offline_reference)
    if a == b:
        return True
    return Path(a).name == Path(b).name


def should_skip_clip(
    timeline_item: Any,
    config: AppConfig,
    *,
    offline_reference: str = "",
    offline_frame_count: int = 0,
) -> tuple[bool, str]:
    """
    Return (skip, reason) for clips that must not be analyzed.

    Skips the lock cut sitting on the timeline when the same file is used as reference.
    """
    if not bool(config.get("skip_reference_on_timeline", True)):
        return False, ""

    media_item = None
    try:
        media_item = timeline_item.GetMediaPoolItem()
    except Exception:
        pass
    media_path = resolve_media_path(media_item)

    if offline_reference and is_reference_media(media_path, offline_reference):
        return True, "same file as offline reference (lock cut on timeline)"

    exclude_tracks = config.get("exclude_track_indices") or []
    try:
        _, track_index = timeline_item.GetTrackTypeAndIndex()
        if track_index in exclude_tracks:
            return True, f"track V{track_index} excluded (reference track)"
    except Exception:
        pass

    try:
        duration = int(timeline_item.GetDuration())
    except Exception:
        duration = 0

    ref_max = int(config.get("reference_clip_max_duration_frames", 0))
    if ref_max <= 0 and offline_frame_count > 0:
        ref_max = int(offline_frame_count * 0.9)

    if ref_max > 0 and duration >= ref_max:
        if offline_reference and is_reference_media(media_path, offline_reference):
            return True, "full-length lock cut (matches reference file)"
        try:
            clip_name = (timeline_item.GetName() or "").lower()
        except Exception:
            clip_name = ""
        patterns = config.get("skip_clip_name_patterns") or [
            "lock cut",
            "offline ref",
            "offline reference",
        ]
        for pattern in patterns:
            if pattern and str(pattern).lower() in clip_name:
                return True, f"full-length reference clip ({pattern!r})"

    return False, ""


def filter_target_clips(
    clips: list[Any],
    config: AppConfig,
    *,
    offline_reference: str = "",
    offline_frame_count: int = 0,
    logger: Any = None,
) -> tuple[list[Any], list[str]]:
    kept: list[Any] = []
    skipped: list[str] = []
    for item in clips:
        skip, reason = should_skip_clip(
            item,
            config,
            offline_reference=offline_reference,
            offline_frame_count=offline_frame_count,
        )
        name = "?"
        try:
            name = item.GetName() or name
        except Exception:
            pass
        if skip:
            msg = f"{name}: {reason}"
            skipped.append(msg)
            if logger:
                logger.info("Skip: %s", msg)
        else:
            kept.append(item)
    return kept, skipped
