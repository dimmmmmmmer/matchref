"""Unified conform index: EDL/XML lookup with timeline fallback."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from matchref.config import AppConfig
from matchref.conform_edl import EdlEvent, parse_edl
from matchref.conform_xml import parse_conform_xml
from matchref.lock_cut_align import hub_to_lock_cut_frame
from matchref.models import OfflineMappingSource
from matchref.timebase import (
    describe_timebase_log,
    detect_conform_origin_frames,
    resolve_timeline_to_offline_frame,
)
from matchref.timecode import TimecodeFormat, format_timecode, parse_timecode
from matchref.timeline_context import TimelineContext


@dataclass
class OfflineFrameMapping:
    offline_frame: int
    source: OfflineMappingSource
    reel: str = ""
    matched_event: int | None = None
    detail: str = ""


def _normalize_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


class ConformIndex:
    """Maps Resolve timeline frames → offline reference via conform EDL/XML."""

    def __init__(
        self,
        config: AppConfig,
        logger: logging.Logger | None = None,
        timeline: TimelineContext | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("matchref")
        self.timeline = timeline
        self.events: list[EdlEvent] = []
        hub_fps = timeline.fps if timeline is not None else 0.0
        self.timecode_format = TimecodeFormat(
            fps=hub_fps,
            drop_frame=timeline.drop_frame if timeline else False,
        )
        self._sequence_start_frames = 0
        self._conform_origin_frames = 0
        self._reference_origin_frames = 0
        self._timeline_offset_frames = 0
        self._loaded = False
        self._sources: list[str] = []
        self._has_edl = False
        self._has_xml = False

    @property
    def is_available(self) -> bool:
        return bool(self.events)

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    def load(self) -> bool:
        """Load EDL/XML from config paths. Returns True if any conform data loaded."""
        self.events.clear()
        self._sources.clear()
        self._loaded = False
        self._conform_origin_frames = 0
        self._sequence_start_frames = 0

        if self.timeline is None:
            raise RuntimeError("ConformIndex requires TimelineContext from Resolve.")
        default_fps = self.timeline.fps
        edl_path = str(self.config.get("conform_edl_path", "")).strip()
        xml_path = str(self.config.get("conform_xml_path", "")).strip()

        if xml_path and not Path(xml_path).is_file():
            raise FileNotFoundError(f"Conform XML not found: {xml_path}")
        if edl_path and not Path(edl_path).is_file():
            raise FileNotFoundError(f"Conform EDL not found: {edl_path}")

        if edl_path and Path(edl_path).is_file():
            edl = parse_edl(edl_path, default_fps=default_fps)
            self.events.extend(edl.events)
            self.timecode_format = edl.timecode_format
            self._sources.append(f"EDL:{edl_path}")
            self._has_edl = True
            self.logger.info("Loaded %d EDL events from %s", len(edl.events), edl_path)

        if xml_path and Path(xml_path).is_file():
            xml = parse_conform_xml(xml_path, default_fps=default_fps)
            if xml.events:
                self.events.extend(xml.events)
                self._has_xml = True
            if xml.timecode_format.fps:
                self.timecode_format = xml.timecode_format
            self._sequence_start_frames = xml.sequence_start_frames
            self._sources.append(f"XML:{xml_path}")
            self.logger.info("Loaded %d XML events from %s", len(xml.events), xml_path)

        self._normalize_conform_to_resolve_hub()
        self._load_reference_origin()

        self._timeline_offset_frames = int(self.config.get("offline_timeline_offset_frames", 0))

        for line in describe_timebase_log(
            self._conform_origin_frames,
            self._reference_origin_frames,
            self.timecode_format,
        ):
            self.logger.info(line)
        if self._timeline_offset_frames:
            self.logger.info("  Hub → lock cut: extra offset %d frames", self._timeline_offset_frames)

        self._loaded = bool(self.events)
        return self._loaded

    def _normalize_conform_to_resolve_hub(self) -> None:
        """Shift conform record TC into Resolve timeline frame space (hub @ 0)."""
        if not self.events:
            return

        if not bool(self.config.get("normalize_conform_to_resolve", True)):
            self.logger.info("normalize_conform_to_resolve=false — conform record TC unchanged")
            return

        origin = detect_conform_origin_frames(
            self.events,
            self._sequence_start_frames,
            self.timecode_format.fps,
        )
        self._conform_origin_frames = origin

        if origin <= 0:
            min_rec = min(event.rec_in for event in self.events)
            self.logger.info(
                "Conform record TC already in Resolve hub space (min rec_in=%d)",
                min_rec,
            )
            return

        for event in self.events:
            event.rec_in -= origin
            event.rec_out -= origin

        if self._sequence_start_frames > 0 and origin > self._sequence_start_frames:
            leader = origin - self._sequence_start_frames
            self.logger.info(
                "Conform → Resolve hub: origin %s (%d f); %s (%d f) after sequence TC (leader)",
                format_timecode(origin, self.timecode_format),
                origin,
                format_timecode(leader, self.timecode_format),
                leader,
            )
        else:
            self.logger.info(
                "Conform → Resolve hub: shifted record TC by %s (%d frames)",
                format_timecode(origin, self.timecode_format),
                origin,
            )

    def _load_reference_origin(self) -> None:
        """Lock-cut file origin: manual reference TC, or auto start-TC alignment."""
        from matchref.timecode_align import auto_align_enabled, start_offset_frames

        fmt = self.timecode_format
        start_tc = str(self.config.get("reference_start_timecode", "")).strip()
        timeline_tc = self.timeline.start_timecode if self.timeline is not None else ""

        # Auto: neutralize a timeline-vs-reference start-timecode difference so the
        # hub→file mapping lines up. Opt-in (default off) — never changes a working
        # project silently. Needs the reference's start TC (manual or, in future,
        # read from the Resolve media pool).
        if auto_align_enabled(self.config) and timeline_tc and start_tc:
            self._reference_origin_frames = max(0, start_offset_frames(timeline_tc, start_tc, fmt))
            self.logger.info(
                "Auto start-TC align: timeline %s vs reference %s → reference origin %d frames",
                timeline_tc,
                start_tc,
                self._reference_origin_frames,
            )
            return

        if start_tc:
            try:
                self._reference_origin_frames = parse_timecode(start_tc, fmt)
            except ValueError:
                self.logger.warning("Invalid reference_start_timecode: %s", start_tc)

        # Diagnostic: surface a mismatch even when not auto-aligning, so it does not
        # silently throw the mapping off (the user asked for this).
        if not auto_align_enabled(self.config) and timeline_tc and start_tc:
            diff = start_offset_frames(timeline_tc, start_tc, fmt)
            if diff != 0:
                self.logger.warning(
                    "Start-TC mismatch: timeline %s vs reference %s (%d frames). "
                    "Enable auto_align_start_timecode to neutralize it.",
                    timeline_tc,
                    start_tc,
                    diff,
                )

    def _offline_from_resolve(self, resolve_frame: int) -> int:
        return resolve_timeline_to_offline_frame(
            resolve_frame,
            reference_origin_frames=self._reference_origin_frames,
            timeline_offset_frames=self._timeline_offset_frames,
        )

    def lookup_by_record_frame(self, timeline_frame: int) -> OfflineFrameMapping | None:
        """
        Map Resolve hub frame → offline lock-cut frame via conform record in/out.

        `timeline_frame` is clip start + local offset from Resolve (the hub).
        """
        if not self.events:
            return None
        resolve_t = int(timeline_frame)
        for event in self.events:
            if event.rec_in <= resolve_t < event.rec_out:
                offset = resolve_t - event.rec_in
                offline_frame = self._offline_from_resolve(resolve_t)
                source = OfflineMappingSource.XML if self._has_xml else OfflineMappingSource.EDL
                return OfflineFrameMapping(
                    offline_frame=offline_frame,
                    source=source,
                    reel=event.reel,
                    matched_event=event.event_number,
                    detail=(
                        f"resolve hub {resolve_t} → offline {offline_frame} "
                        f"(evt {event.event_number}, conform rec {event.rec_in}+{offset})"
                    ),
                )
        return None

    def lookup(
        self,
        reel_name: str,
        source_frame: int,
        clip_name: str = "",
        media_basename: str = "",
    ) -> OfflineFrameMapping | None:
        """Find offline frame via reel/source; maps through conform record in hub space."""
        if not self.events:
            return None

        event = self._find_event(reel_name, source_frame, clip_name, media_basename)
        if event is None:
            return None

        if source_frame < event.src_in or source_frame >= event.src_out:
            return None

        offset = source_frame - event.src_in
        resolve_t = event.rec_in + offset
        offline_frame = self._offline_from_resolve(resolve_t)
        if self._has_edl and not self._has_xml:
            source = OfflineMappingSource.EDL
        elif self._has_xml and not self._has_edl:
            source = OfflineMappingSource.XML
        else:
            source = OfflineMappingSource.EDL

        return OfflineFrameMapping(
            offline_frame=offline_frame,
            source=source,
            reel=event.reel,
            matched_event=event.event_number,
            detail=f"resolve hub {resolve_t} → offline {offline_frame} (src+{offset})",
        )

    def _find_event(
        self,
        reel_name: str,
        source_frame: int,
        clip_name: str,
        media_basename: str,
    ) -> EdlEvent | None:
        keys = [_normalize_key(reel_name), _normalize_key(clip_name), _normalize_key(media_basename)]
        keys = [k for k in keys if k]

        for event in self.events:
            if event.reel == "AX" or event.reel.upper() == "AX":
                continue
            if _normalize_key(event.reel) not in keys and _normalize_key(event.clip_name) not in keys:
                continue
            if event.src_in <= source_frame < event.src_out:
                return event

        for event in self.events:
            candidates = [
                _normalize_key(event.clip_name),
                _normalize_key(Path(event.source_file).stem if event.source_file else ""),
                _normalize_key(event.tape_name),
                _normalize_key(event.reel),
            ]
            if not any(k in candidates for k in keys if k):
                continue
            if event.src_in <= source_frame < event.src_out:
                return event

        best: EdlEvent | None = None
        best_overlap = -1
        for event in self.events:
            if event.src_in <= source_frame < event.src_out:
                overlap = min(event.src_out, source_frame + 1) - max(event.src_in, source_frame)
                if overlap > best_overlap:
                    best = event
                    best_overlap = overlap
        return best


class OfflineFrameResolver:
    """Resolve offline frame: conform match in Resolve hub space, else hub = offline."""

    def __init__(self, config: AppConfig, conform: ConformIndex | None = None) -> None:
        self.config = config
        self.conform = conform
        self.lock_cut_hub_origin = 0

    def configure_lock_cut(
        self,
        *,
        lock_cut_hub_origin: int,
        lock_cut_frame_count: int = 0,
        timeline_end_frame: int = 0,
    ) -> None:
        self.lock_cut_hub_origin = max(0, int(lock_cut_hub_origin))
        self._lock_cut_frames = lock_cut_frame_count
        self._timeline_end = timeline_end_frame

    def _offline_from_resolve(self, resolve_frame: int) -> int:
        ref_origin = 0
        # offline_timeline_offset_frames must be applied exactly once. When a conform
        # is loaded it already folded the config value into _timeline_offset_frames
        # (see ConformIndex.load), so use that — otherwise read the config directly.
        # Adding both double-counts the offset for clips that fall through to the
        # timeline mapping while a conform is present (e.g. stock clips not in the XML).
        if self.conform is not None:
            ref_origin = self.conform._reference_origin_frames
            extra = self.conform._timeline_offset_frames
        else:
            extra = int(self.config.get("offline_timeline_offset_frames", 0))
        return hub_to_lock_cut_frame(
            resolve_frame,
            lock_cut_hub_origin=self.lock_cut_hub_origin,
            reference_origin_frames=ref_origin,
            extra_offset_frames=extra,
        )

    def resolve(
        self,
        *,
        timeline_frame: int,
        reel_name: str,
        source_frame: int,
        clip_name: str = "",
        media_basename: str = "",
    ) -> OfflineFrameMapping:
        mode = str(self.config.get("offline_mapping_mode", "auto")).lower()
        resolve_t = int(timeline_frame)

        if self.conform and self.conform.is_available and mode in ("auto", "conform", "edl", "xml"):
            if bool(self.config.get("prefer_record_timecode_mapping", True)):
                mapping = self.conform.lookup_by_record_frame(resolve_t)
                if mapping is not None:
                    return mapping
            mapping = self.conform.lookup(reel_name, source_frame, clip_name, media_basename)
            if mapping is not None:
                return mapping

        if mode == "conform":
            offline = self._offline_from_resolve(resolve_t)
            return OfflineFrameMapping(
                offline_frame=offline,
                source=OfflineMappingSource.NONE,
                detail="conform required but no match",
            )

        offline = self._offline_from_resolve(resolve_t)
        origin = self.lock_cut_hub_origin
        return OfflineFrameMapping(
            offline_frame=offline,
            source=OfflineMappingSource.TIMELINE,
            reel=reel_name,
            detail=(
                f"resolve hub {resolve_t} → lock-cut {offline} "
                f"(origin hub {origin}, no conform)"
            ),
        )
