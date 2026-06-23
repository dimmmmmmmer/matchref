"""Start-timecode alignment between the timeline and the offline reference.

If the timeline and the offline reference file start at different timecodes, the
hub-frame → reference-frame mapping is off by their difference. These pure helpers
compute that difference; integration is opt-in via ``auto_align_start_timecode``
(see ConformIndex) so it can never silently change a working project's mapping.
"""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.timecode import TimecodeFormat, parse_timecode


def parse_tc_or_none(value: str, fmt: TimecodeFormat) -> int | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        return parse_timecode(text, fmt)
    except ValueError:
        return None


def start_offset_frames(
    timeline_start_tc: str, reference_start_tc: str, fmt: TimecodeFormat
) -> int:
    """Frames the timeline clock leads the reference clock (timeline − reference).

    Subtract this from a hub frame so equal wall-clock timecodes index the same
    reference-file frame. Returns 0 when either timecode is missing or unparseable.
    """
    t = parse_tc_or_none(timeline_start_tc, fmt)
    r = parse_tc_or_none(reference_start_tc, fmt)
    if t is None or r is None:
        return 0
    return t - r


def auto_align_enabled(config: AppConfig) -> bool:
    return bool(config.get("auto_align_start_timecode", False))
