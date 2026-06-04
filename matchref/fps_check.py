"""Validate conform / reference against Resolve timeline FPS and resolution."""

from __future__ import annotations

import logging

from matchref.config import AppConfig
from matchref.conform_index import ConformIndex
from matchref.fps import format_fps, fps_near_equal
from matchref.media_probe import probe_video
from matchref.timeline_context import TimelineContext


class FpsMismatchError(RuntimeError):
    """Conform or reference does not match Resolve timeline."""


def validate_against_timeline(
    timeline: TimelineContext,
    config: AppConfig,
    *,
    conform: ConformIndex | None = None,
    offline_reference_path: str = "",
    logger: logging.Logger | None = None,
) -> None:
    """
    Resolve timeline is the hub. Conform XML/EDL and lock-cut file must match.
    """
    log = logger or logging.getLogger("matchref")
    tolerance = float(config.get("fps_match_tolerance", 0.01))
    strict = bool(config.get("require_fps_match", True))
    hub = timeline.fps

    log.info("Hub FPS (Resolve timeline): %s", format_fps(hub))
    log.info("Hub resolution (Resolve timeline): %dx%d", timeline.width, timeline.height)

    path = offline_reference_path.strip() or str(config.get("offline_reference_path", "")).strip()
    ref_fps = 0.0
    ref_w = ref_h = 0
    if path:
        probe = probe_video(path)
        ref_fps = probe.fps
        ref_w, ref_h = probe.width, probe.height
        if ref_fps > 0:
            log.info("Lock-cut reference file FPS: %s", format_fps(ref_fps))
        if ref_w > 0 and ref_h > 0:
            log.info("Lock-cut reference file: %dx%d", ref_w, ref_h)

    conform_fps = 0.0
    if conform is not None and conform.is_available:
        conform_fps = float(conform.timecode_format.fps)
        log.info("Conform EDL/XML FPS: %s", format_fps(conform_fps))

    if ref_w > 0 and ref_h > 0 and (ref_w != timeline.width or ref_h != timeline.height):
        msg = (
            f"Reference {ref_w}x{ref_h} vs timeline {timeline.width}x{timeline.height} — "
            "hub frame indices follow Resolve; images are scaled for matching."
        )
        if bool(config.get("require_resolution_match", False)):
            raise FpsMismatchError(msg)
        if bool(config.get("warn_resolution_mismatch", True)):
            log.warning(msg)

    mismatches: list[str] = []
    if conform_fps > 0 and not fps_near_equal(conform_fps, hub, tolerance):
        mismatches.append(f"Conform: {format_fps(conform_fps)} ≠ timeline {format_fps(hub)}")
    if ref_fps > 0 and not fps_near_equal(ref_fps, hub, tolerance):
        mismatches.append(f"Reference file: {format_fps(ref_fps)} ≠ timeline {format_fps(hub)}")

    if mismatches:
        detail = (
            "Frame rate mismatch with the open Resolve timeline (hub).\n"
            + "\n".join(f"  • {m}" for m in mismatches)
        )
        if strict:
            raise FpsMismatchError(detail)
        log.warning("%s", detail)
