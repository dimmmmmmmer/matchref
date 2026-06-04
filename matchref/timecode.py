"""SMPTE timecode parsing and frame conversion."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TIMECODE_RE = re.compile(
    r"^(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[:;](?P<f>\d{2})(?:[:;](?P<ff>\d{2}))?$"
)


@dataclass(frozen=True)
class TimecodeFormat:
    fps: float = 24.0
    drop_frame: bool = False


def parse_timecode(value: str, fmt: TimecodeFormat) -> int:
    """
    Convert SMPTE timecode string to a zero-based frame index.

    Supports HH:MM:SS:FF and HH:MM:SS:FF:FF (subframes ignored).
    """
    text = str(value).strip()
    match = _TIMECODE_RE.match(text)
    if not match:
        raise ValueError(f"Invalid timecode: {value}")

    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    frames = int(match.group("f"))

    if fmt.drop_frame and abs(fmt.fps - 29.97) < 0.05:
        return _drop_frame_to_frames(hours, minutes, seconds, frames)
    return _non_drop_to_frames(hours, minutes, seconds, frames, fmt.fps)


def format_timecode(frame: int, fmt: TimecodeFormat) -> str:
    if fmt.drop_frame and abs(fmt.fps - 29.97) < 0.05:
        return _frames_to_drop_frame(frame)
    return _frames_to_non_drop(frame, fmt.fps)


def _non_drop_to_frames(h: int, m: int, s: int, f: int, fps: float) -> int:
    return int(round(((h * 3600 + m * 60 + s) * fps) + f))


def _frames_to_non_drop(frame: int, fps: float) -> str:
    total_seconds, ff = divmod(int(frame), int(round(fps)))
    if ff == int(round(fps)):
        total_seconds += 1
        ff = 0
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


def _drop_frame_to_frames(h: int, m: int, s: int, f: int) -> int:
    """29.97 DF timecode to frame count."""
    total_minutes = (h * 60) + m
    dropped = 2 * (total_minutes - (total_minutes // 10))
    return int(round(((h * 3600 + m * 60 + s) * 30) + f - dropped))


def _frames_to_drop_frame(frame: int) -> str:
    f = int(frame)
    d = f // 17982
    m = f % 17982
    if m < 2:
        f = d * 17982 + m
    else:
        f = d * 17982 + 2 + (((m - 2) // 1798) * 2) + ((m - 2) % 1798)
    ff = f % 30
    total_seconds = f // 30
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d};{ff:02d}"


def infer_fps_from_edl_header(line: str, default: float) -> TimecodeFormat:
    upper = line.upper()
    if "DROP FRAME" in upper and "NON-DROP" not in upper:
        return TimecodeFormat(fps=29.97, drop_frame=True)
    if "29.97" in upper:
        return TimecodeFormat(fps=29.97, drop_frame=False)
    if "30" in upper:
        return TimecodeFormat(fps=30.0, drop_frame=False)
    if "25" in upper:
        return TimecodeFormat(fps=25.0, drop_frame=False)
    if "24" in upper:
        return TimecodeFormat(fps=24.0, drop_frame=False)
    if "23.976" in upper or "23.98" in upper:
        return TimecodeFormat(fps=23.976, drop_frame=False)
    return TimecodeFormat(fps=default, drop_frame=False)
