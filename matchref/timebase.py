"""Unified timebase: DaVinci Resolve timeline frames are the hub."""

from __future__ import annotations

from matchref.conform_edl import EdlEvent
from matchref.timecode import TimecodeFormat, format_timecode


def detect_conform_origin_frames(
    events: list[EdlEvent],
    sequence_start_frames: int,
    fps: float,
) -> int:
    """
    Pick conform origin so the first conform cut lands on Resolve hub frame 0.

    Conform may declare a sequence start TC while the first clip record sits later
    (leader, black, bars). Subtracting only the sequence tag leaves a constant gap —
    align using the first cut's record frame so hub frame 0 matches Resolve.
    """
    if not events:
        return 0

    min_rec = min(event.rec_in for event in events)
    tolerance = max(2, int(round(fps)))

    if min_rec <= tolerance:
        return 0

    # First cut (absolute or relative) → Resolve hub 0.
    return min_rec


def resolve_timeline_to_offline_frame(
    resolve_timeline_frame: int,
    *,
    reference_origin_frames: int = 0,
    timeline_offset_frames: int = 0,
) -> int:
    """Resolve hub frame → offline / lock-cut file frame index."""
    return max(
        0,
        int(resolve_timeline_frame) - int(reference_origin_frames) + int(timeline_offset_frames),
    )


def describe_timebase_log(
    conform_origin: int,
    reference_origin: int,
    fmt: TimecodeFormat,
) -> list[str]:
    lines = [
        "Timebase hub: DaVinci Resolve timeline (clip GetStart + local frame = hub frame 0…N)",
    ]
    if conform_origin > 0:
        lines.append(
            f"  Conform → hub: subtract {format_timecode(conform_origin, fmt)} ({conform_origin} frames)"
        )
    else:
        lines.append("  Conform → hub: already aligned (no origin shift)")
    if reference_origin > 0:
        lines.append(
            f"  Hub → lock cut: subtract {format_timecode(reference_origin, fmt)} ({reference_origin} frames)"
        )
    else:
        lines.append("  Hub → lock cut: same frame index (reference starts @ hub 0)")
    return lines
