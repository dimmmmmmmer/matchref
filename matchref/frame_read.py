"""Accurate video frame access (OpenCV frame index seek is unreliable on long-GOP files)."""

from __future__ import annotations

from typing import Literal

import cv2

SeekMode = Literal["refine", "msec", "frames"]


def index_after_read(cap: cv2.VideoCapture) -> int:
    """0-based index of the frame that was just decoded by the last read()."""
    return max(0, int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 1) - 1)


def seek_to_frame(
    cap: cv2.VideoCapture,
    frame_index: int,
    fps: float,
    *,
    mode: SeekMode = "refine",
) -> int:
    """
    Position `cap` so the next `read()` returns `frame_index` (0-based).

    Returns the frame index OpenCV reports before read (for logging).
    """
    target = max(0, int(frame_index))

    if mode == "frames" or fps <= 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        return int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)

    cap.set(cv2.CAP_PROP_POS_MSEC, (target / float(fps)) * 1000.0)
    reported = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)

    if mode == "msec":
        return reported

    if reported > target:
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        reported = target

    # Decode forward until the next read() should yield `target`.
    safety = 0
    while reported < target and safety < target + 16:
        if not cap.grab():
            break
        reported = int(cap.get(cv2.CAP_PROP_POS_FRAMES) or 0)
        safety += 1
    return reported


def read_frame_at_index(
    cap: cv2.VideoCapture,
    frame_index: int,
    fps: float,
    *,
    mode: SeekMode = "refine",
    index_offset: int = 0,
) -> tuple[bool, object | None, int]:
    """
    Read one BGR frame at `frame_index` (0-based).

    Third return value is the decoded frame index (verified after read).
    `index_offset` is added for codecs where OpenCV lags by one frame.
    """
    target = max(0, int(frame_index) + int(index_offset))
    seek_to_frame(cap, target, fps, mode=mode)
    ok, frame = cap.read()
    if not ok or frame is None:
        return False, None, -1

    decoded = index_after_read(cap)
    advance = 0
    while decoded < target and advance < 4:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        decoded = index_after_read(cap)
        advance += 1

    if decoded > target:
        cap.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, frame = cap.read()
        if ok and frame is not None:
            decoded = index_after_read(cap)

    return bool(frame is not None), frame, decoded
