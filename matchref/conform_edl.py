"""CMX 3600 EDL parser for offline record mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from matchref.timecode import TimecodeFormat, infer_fps_from_edl_header, parse_timecode

_EVENT_RE = re.compile(
    r"^\s*(\d{3,6})\s+(\S+)\s+(\S+)\s+(\S+)\s+"
    r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+"
    r"(\d{2}:\d{2}:\d{2}[:;]\d{2})\s+(\d{2}:\d{2}:\d{2}[:;]\d{2})"
)

_CLIP_NAME_RE = re.compile(r"^\*\s*FROM\s+CLIP\s+NAME:\s*(.+)$", re.IGNORECASE)
_SOURCE_FILE_RE = re.compile(r"^\*\s*SOURCE\s+FILE:\s*(.+)$", re.IGNORECASE)
_TAPE_NAME_RE = re.compile(r"^\*\s*TAPE\s+NAME:\s*(.+)$", re.IGNORECASE)


@dataclass
class EdlEvent:
    """Single EDL event with source/record timecodes (frames)."""

    event_number: int
    reel: str
    track: str
    transition: str
    src_in: int
    src_out: int
    rec_in: int
    rec_out: int
    clip_name: str = ""
    source_file: str = ""
    tape_name: str = ""

    @property
    def duration(self) -> int:
        return max(0, self.src_out - self.src_in)


@dataclass
class EdlParseResult:
    events: list[EdlEvent] = field(default_factory=list)
    timecode_format: TimecodeFormat = field(default_factory=TimecodeFormat)
    title: str = ""


def parse_edl(path: str | Path, default_fps: float = 24.0) -> EdlParseResult:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_edl_text(text, default_fps=default_fps)


def _event_from_match(match: re.Match[str], fmt: TimecodeFormat) -> EdlEvent:
    return EdlEvent(
        event_number=int(match.group(1)),
        reel=match.group(2).strip(),
        track=match.group(3).strip(),
        transition=match.group(4).strip(),
        src_in=parse_timecode(match.group(5), fmt),
        src_out=parse_timecode(match.group(6), fmt),
        rec_in=parse_timecode(match.group(7), fmt),
        rec_out=parse_timecode(match.group(8), fmt),
    )


def _apply_event_metadata(pending: EdlEvent, line: str) -> None:
    """FROM CLIP NAME / SOURCE FILE / TAPE comment lines attach to the last event."""
    clip_match = _CLIP_NAME_RE.match(line)
    if clip_match:
        pending.clip_name = clip_match.group(1).strip()
        return
    file_match = _SOURCE_FILE_RE.match(line)
    if file_match:
        pending.source_file = file_match.group(1).strip()
        return
    tape_match = _TAPE_NAME_RE.match(line)
    if tape_match:
        pending.tape_name = tape_match.group(1).strip()


def _apply_header_line(result: EdlParseResult, line: str, default_fps: float) -> bool:
    """Consume a TITLE:/FCM: header line into ``result``; False when not a header."""
    upper = line.upper()
    if upper.startswith("TITLE:"):
        result.title = line.split(":", 1)[-1].strip()
        return True
    if upper.startswith("FCM:"):
        result.timecode_format = infer_fps_from_edl_header(line, default_fps)
        return True
    return False


def parse_edl_text(text: str, default_fps: float = 24.0) -> EdlParseResult:
    result = EdlParseResult(timecode_format=TimecodeFormat(fps=default_fps))
    pending: EdlEvent | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _apply_header_line(result, line, default_fps):
            continue

        match = _EVENT_RE.match(line)
        if match:
            if pending is not None:
                result.events.append(pending)
            pending = _event_from_match(match, result.timecode_format)
            continue

        if pending is not None:
            _apply_event_metadata(pending, line)

    if pending is not None:
        result.events.append(pending)
    return result
