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
    # NB: uses the fractional rate on purpose (conform offset math interprets a
    # timecode against true elapsed time), so parse/format do NOT round-trip for
    # non-integer fps like 23.976 — see test_fps.test_timecode_parsing_uses_format_fps.
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
    # 29.97 drop-frame: skip labels ;00 and ;01 at the top of every minute except
    # every tenth. Reconstruct the wall-clock label by adding the dropped labels
    # back to the real frame index (canonical algorithm), then split into HH:MM:SS;FF.
    drop = 2
    frames_per_10min = 17982
    frames_per_min = 1798
    f = int(frame)
    d = f // frames_per_10min
    m = f % frames_per_10min
    if m < drop:
        f += 18 * d
    else:
        f += 18 * d + drop * ((m - drop) // frames_per_min)
    ff = f % 30
    s = (f // 30) % 60
    mm = (f // (30 * 60)) % 60
    hh = (f // (30 * 3600)) % 24
    return f"{hh:02d}:{mm:02d}:{s:02d};{ff:02d}"


# Checked in order: the first marker found in the FCM/header line wins.
_EDL_HEADER_FPS: tuple[tuple[str, float], ...] = (
    ("29.97", 29.97),
    ("30", 30.0),
    ("25", 25.0),
    ("24", 24.0),
    ("23.976", 23.976),
    ("23.98", 23.976),
)


def infer_fps_from_edl_header(line: str, default: float) -> TimecodeFormat:
    upper = line.upper()
    if "DROP FRAME" in upper and "NON-DROP" not in upper:
        return TimecodeFormat(fps=29.97, drop_frame=True)
    for marker, fps in _EDL_HEADER_FPS:
        if marker in upper:
            return TimecodeFormat(fps=fps, drop_frame=False)
    return TimecodeFormat(fps=default, drop_frame=False)
