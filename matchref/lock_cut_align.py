"""Map Resolve hub frames → lock-cut file frames (without conform XML)."""

from __future__ import annotations

import logging
from typing import Any

from matchref.config import AppConfig


def clip_hub_start(timeline_item: Any) -> int:
    try:
        return int(timeline_item.GetStart())
    except Exception:
        return 0


def timeline_end_frame(timeline: object, clip_starts: list[int], clip_ends: list[int]) -> int:
    """Best-effort last frame index on the open timeline."""
    end = 0
    if clip_ends:
        end = max(clip_ends)
    for getter_name in ("GetEndFrame", "GetDuration"):
        try:
            getter = getattr(timeline, getter_name, None)
            if callable(getter):
                value = int(getter())
                if value > end:
                    end = value
        except Exception:
            continue
    if end <= 0 and clip_starts:
        end = max(clip_starts) + 1
    return max(0, end)


def detect_lock_cut_hub_origin(
    *,
    timeline_end_frame: int,
    lock_cut_frame_count: int,
    clip_starts: list[int],
    config: AppConfig,
    logger: logging.Logger | None = None,
) -> int:
    """
    Resolve hub frame index of lock-cut frame 0.

    - Full timeline export: origin 0 (hub frame == lock-cut frame).
    - Trimmed lock cut (picture starts later on hub): origin ≈ first clip on timeline.
    """
    log = logger or logging.getLogger("matchref")
    override = int(config.get("lock_cut_hub_origin_frame", -1))
    if override >= 0:
        log.info("Lock-cut hub origin from config: %d", override)
        return override

    if lock_cut_frame_count <= 0 or not clip_starts:
        return 0

    min_start = min(clip_starts)
    hub_end = max(timeline_end_frame, min_start + 1)
    span_from_first = max(1, hub_end - min_start)
    tol = max(48, int(hub_end * 0.02))

    if abs(lock_cut_frame_count - span_from_first) <= tol:
        log.info(
            "Lock-cut matches hub span from first clip: %d frames, origin hub %d",
            lock_cut_frame_count,
            min_start,
        )
        return min_start

    if abs(lock_cut_frame_count - hub_end) <= tol:
        log.info(
            "Lock-cut matches full hub duration: %d frames — origin hub 0",
            lock_cut_frame_count,
        )
        return 0

    if lock_cut_frame_count < hub_end:
        log.warning(
            "Lock-cut (%d frames) is much shorter than hub span (%d). "
            "Using hub frame = lock-cut frame (origin 0). "
            "If the reference starts later on the timeline, set lock_cut_hub_origin_frame "
            "or load Conform XML.",
            lock_cut_frame_count,
            hub_end,
        )

    log.info(
        "Lock-cut %d frames vs hub end %d — default origin hub 0",
        lock_cut_frame_count,
        hub_end,
    )
    return 0


def hub_to_lock_cut_frame(
    resolve_hub_frame: int,
    *,
    lock_cut_hub_origin: int,
    reference_origin_frames: int = 0,
    extra_offset_frames: int = 0,
) -> int:
    return max(
        0,
        int(resolve_hub_frame)
        - int(lock_cut_hub_origin)
        - int(reference_origin_frames)
        + int(extra_offset_frames),
    )
