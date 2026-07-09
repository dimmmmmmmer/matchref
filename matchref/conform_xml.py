"""FCPXML / XMEML conform parsers for offline record mapping."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from matchref.conform_edl import EdlEvent
from matchref.fps import effective_fps_from_xml_rate
from matchref.timecode import TimecodeFormat, parse_timecode

_FCPXML_TIME_RE = re.compile(r"^(?:(?P<h>\d+)s)?(?:(?P<num>-?\d+)/(?P<den>\d+)s)?$")


@dataclass
class XmlParseResult:
    events: list[EdlEvent] = field(default_factory=list)
    timecode_format: TimecodeFormat = field(default_factory=TimecodeFormat)
    sequence_start_frames: int = 0
    title: str = ""


def parse_conform_xml(path: str | Path, default_fps: float = 24.0) -> XmlParseResult:
    root = ET.parse(path).getroot()
    tag = root.tag.lower()
    if (
        "fcpxml" in tag
        or root.find(".//sequence") is not None
        and root.find(".//spine") is not None
    ):
        return _parse_fcpxml(root, default_fps)
    return _parse_xmeml(root, default_fps)


def _parse_rational_time(value: str | None, fps: float) -> int:
    if not value:
        return 0
    text = str(value).strip()
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return int(text)
    match = _FCPXML_TIME_RE.match(text)
    if not match:
        try:
            return int(round(float(text) * fps))
        except ValueError:
            return 0
    if match.group("num") and match.group("den"):
        num = int(match.group("num"))
        den = int(match.group("den"))
        if den == 0:
            return 0
        return int(round((num / den) * fps))
    if match.group("h"):
        return int(round(float(match.group("h")) * fps))
    return 0


def _parse_fcpxml_header(sequence: ET.Element, result: XmlParseResult) -> None:
    """Read the sequence format (fps, title) and start timecode into ``result``."""
    fmt_node = sequence.find("format")
    if fmt_node is not None:
        frame_duration = fmt_node.get("frameDuration", "")
        if "/" in frame_duration:
            num, den = frame_duration.replace("s", "").split("/")
            if float(den) != 0:
                result.timecode_format = TimecodeFormat(fps=float(num) / float(den))
        result.title = sequence.get("name", "") or result.title

    tc_start = sequence.find("timecode")
    if tc_start is not None and tc_start.get("value"):
        try:
            result.sequence_start_frames = parse_timecode(
                tc_start.get("value", ""), result.timecode_format
            )
        except ValueError:
            result.sequence_start_frames = 0


def _fcpxml_clip_event(
    root: ET.Element, child: ET.Element, rec_in: int, duration: int, fps: float, number: int
) -> EdlEvent:
    """Build the conform event for one spine clip element."""
    start = _parse_rational_time(child.get("start"), fps)
    name = child.get("name", "") or child.get("ref", "UNKNOWN")
    reel = name
    asset = root.find(f".//asset[@id='{child.get('ref', '')}']")
    if asset is not None:
        reel = asset.get("name", reel) or reel
    return EdlEvent(
        event_number=number,
        reel=reel,
        track="V",
        transition="C",
        src_in=start,
        src_out=start + duration,
        rec_in=rec_in,
        rec_out=rec_in + duration,
        clip_name=name,
    )


def _parse_fcpxml(root: ET.Element, default_fps: float) -> XmlParseResult:
    result = XmlParseResult(timecode_format=TimecodeFormat(fps=default_fps))
    sequence = root.find(".//sequence")
    if sequence is None:
        return result

    _parse_fcpxml_header(sequence, result)
    fps = result.timecode_format.fps

    spine = sequence.find(".//spine")
    if spine is None:
        return result

    record_cursor = result.sequence_start_frames
    for child in list(spine):
        tag = child.tag.split("}")[-1].lower()
        offset = _parse_rational_time(child.get("offset"), fps)
        duration = _parse_rational_time(child.get("duration"), fps)
        if tag in ("gap", "spine"):
            record_cursor += offset + duration
            continue
        if tag not in ("clip", "asset-clip", "video", "ref-clip", "sync-clip"):
            continue

        event = _fcpxml_clip_event(
            root, child, record_cursor + offset, duration, fps, len(result.events) + 1
        )
        result.events.append(event)
        record_cursor = max(record_cursor, event.rec_out)

    return result


def _timecode_format_from_rate_element(
    rate_el: ET.Element | None, default: TimecodeFormat
) -> TimecodeFormat:
    if rate_el is None:
        return default
    timebase = 0.0
    tb = rate_el.find("timebase")
    if tb is not None and tb.text:
        try:
            timebase = float(tb.text)
        except ValueError:
            pass
    ntsc = False
    ntsc_node = rate_el.find("ntsc")
    if ntsc_node is not None and ntsc_node.text:
        ntsc = ntsc_node.text.strip().upper() in ("TRUE", "1", "YES")
    if timebase > 0:
        return TimecodeFormat(
            fps=effective_fps_from_xml_rate(timebase, ntsc),
            drop_frame=default.drop_frame,
        )
    return default


def _sequence_start_from_node(
    sequence: ET.Element, fmt: TimecodeFormat
) -> tuple[int, TimecodeFormat]:
    """Read sequence origin timecode / frame from XMEML / DaVinci XML."""
    tc = sequence.find("timecode")
    if tc is None:
        return 0, fmt

    rate_el = tc.find("rate") or sequence.find("rate")
    fmt = _timecode_format_from_rate_element(rate_el, fmt)

    frame_node = tc.find("frame")
    if frame_node is not None and frame_node.text:
        text = frame_node.text.strip()
        if text.lstrip("-").isdigit():
            return int(text), fmt

    for tag in ("string", "displaystring"):
        node = tc.find(tag)
        if node is not None and node.text:
            try:
                return parse_timecode(node.text.strip(), fmt), fmt
            except ValueError:
                continue
    return 0, fmt


def _parse_xmeml(root: ET.Element, default_fps: float) -> XmlParseResult:
    result = XmlParseResult(timecode_format=TimecodeFormat(fps=default_fps))
    sequence = root.find(".//sequence")
    if sequence is None:
        return result

    result.timecode_format = _timecode_format_from_rate_element(
        sequence.find("rate"),
        result.timecode_format,
    )

    seq_start, result.timecode_format = _sequence_start_from_node(sequence, result.timecode_format)
    result.sequence_start_frames = seq_start

    fps = result.timecode_format.fps

    for clipitem in sequence.findall(".//clipitem"):
        name_node = clipitem.find("name")
        name = name_node.text.strip() if name_node is not None and name_node.text else "UNKNOWN"
        reel = name
        reel_node = clipitem.find(".//reel/name")
        if reel_node is not None and reel_node.text:
            reel = reel_node.text.strip()

        def _frame(tag: str, clipitem=clipitem) -> int:  # noqa: B008 — bind loop var
            node = clipitem.find(tag)
            if node is None or node.text is None:
                return 0
            try:
                return int(node.text)
            except ValueError:
                return _parse_rational_time(node.text, fps)

        src_in = _frame("in")
        src_out = _frame("out")
        rec_in = _frame("start")
        rec_out = rec_in + max(0, src_out - src_in)

        file_node = clipitem.find(".//file/pathurl")
        source_file = ""
        if file_node is not None and file_node.text:
            source_file = file_node.text.strip()

        result.events.append(
            EdlEvent(
                event_number=len(result.events) + 1,
                reel=reel,
                track="V",
                transition="C",
                src_in=src_in,
                src_out=src_out,
                rec_in=rec_in,
                rec_out=rec_out,
                clip_name=name,
                source_file=source_file,
            )
        )
    return result
