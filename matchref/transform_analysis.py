"""Clip analysis pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from matchref.alignment import (
    align_frames,
    alignment_admission_threshold,
    apply_match_flags,
    transforms_are_stable,
    warp_analysis_to_timeline,
)
from matchref.clip_edit_transform import (
    _fit_frame_to_canvas,
    apply_clip_edit_to_frame,
    read_clip_edit_transform,
    should_simulate_edit_for_match,
)
from matchref.clip_filter import filter_target_clips
from matchref.clip_metadata import build_clip_source_info
from matchref.config import AppConfig
from matchref.conform_index import ConformIndex, OfflineFrameResolver
from matchref.debug_frames import (
    format_sample_compare_line,
    resolve_debug_dir,
    save_match_debug,
)
from matchref.edit_match_mode import is_absolute_edit_match, use_baseline_compensation_for_match
from matchref.fps_check import validate_against_timeline
from matchref.frame_provider import FrameProvider, resolve_media_path, sample_points_for_clip
from matchref.lock_cut_align import (
    clip_hub_start,
    detect_lock_cut_hub_origin,
    reference_coverage_warning,
    timeline_end_frame,
)
from matchref.logging_report import log_sample
from matchref.match_quality import (
    assess_clip_match_quality,
    clip_motion_profile,
    ecc_consensus_passes,
    max_scale_spread,
    min_ecc_for_refine_attempt,
    refine_early_exit_enabled,
    sample_refine_accepted,
    sample_score_passes,
    scale_spread,
    score_from_refine_ncc,
)
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample
from matchref.precision_align import (
    initial_edit_from_warp,
    is_maximum_precision,
    refine_resolve_edit,
)
from matchref.timeline_context import TimelineContext


@dataclass
class _ClipContext:
    """Per-clip state shared across the analyze stages (setup → samples → finalize)."""

    timeline_item: Any
    result: ClipAnalysisResult
    baseline: Any
    media_path: str
    canvas_size: tuple[int, int]
    control_points: list[Any]
    stop_on_confident: bool
    confident_score: float
    min_scale_apply: float
    max_scale_apply: float
    match_base_scale: float
    match_base_center: tuple[float, float]
    match_base_angle: float
    transform_vectors: list[tuple[float, float, float, float]] = field(default_factory=list)
    # Samples whose refine was rejected but whose direct ECC alignment is plausible.
    # Used to rescue low-structure clips (smoke/sky) where the ECC zoom is reliable
    # and corroborated across frames but the refine search and structure gate are not.
    ecc_candidates: list[TransformSample] = field(default_factory=list)


@dataclass
class _SampleFrames:
    """Frames + bookkeeping for one sample, produced by `_load_sample`.

    ``terminal`` means the sample already failed (frame missing / no conform match)
    and was appended to the result — the caller should not attempt matching.
    """

    sample: TransformSample
    terminal: bool
    online: Any = None
    offline: Any = None
    online_raw: Any = None
    compensate: bool = False
    mapping: Any = None
    compare_line: str = ""
    clamped_src: int = 0
    absolute: bool = False


class TransformAnalyzer:
    def __init__(
        self,
        config: AppConfig,
        timeline_ctx: TimelineContext,
        logger: logging.Logger | None = None,
        resolve: Any | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("matchref")
        self.resolve = resolve
        self.timeline_ctx = timeline_ctx
        self.conform = ConformIndex(config, logger, timeline=timeline_ctx)
        self.conform.load()
        validate_against_timeline(timeline_ctx, config, conform=self.conform, logger=logger)
        resolver = OfflineFrameResolver(config, self.conform)
        self.frames = FrameProvider(config, offline_resolver=resolver, logger=self.logger)
        if self.conform.is_available:
            self.logger.info(
                "Conform map loaded (%d events) from %s",
                len(self.conform.events),
                ", ".join(self.conform.sources),
            )
        else:
            xml_path = str(self.config.get("conform_xml_path", "")).strip()
            edl_path = str(self.config.get("conform_edl_path", "")).strip()
            if xml_path or edl_path:
                self.logger.error(
                    "Conform path set but not loaded — check file path in GUI. "
                    "Without conform, lock-cut frame = hub frame minus auto origin."
                )
            else:
                self.logger.warning(
                    "No conform EDL/XML — lock-cut mapping uses hub frame and trim detect. "
                    "For reel/record mapping, set Conform XML in GUI."
                )

        self._variance_threshold = float(self.config.get("transform_variance_threshold", 0.02))
        self._sample_n = int(self.config.get("sample_every_n_frames", 0))
        self._debug_dir: Path | None = None
        self._batch_ready = False

    def prepare_batch(self, timeline_items: list[Any]) -> tuple[list[Any], list[str]]:
        """Load offline reference and return filtered clip list."""
        offline_path = str(self.config.get("offline_reference_path", "")).strip()
        if not offline_path:
            raise ValueError("Offline reference path is empty.")

        offline_frames = 0
        try:
            self.frames.set_offline_reference(
                offline_path,
                timeline_fps=self.timeline_ctx.fps,
            )
            offline_frames = self.frames.get_offline_frame_count()
        except (FileNotFoundError, RuntimeError):
            # A bad/unreadable reference path is fatal — surface it instead of
            # silently continuing with zero frames (which only yields confusing
            # "frame unavailable" errors per clip later).
            raise
        except Exception:
            pass

        clips, skipped = filter_target_clips(
            timeline_items,
            self.config,
            offline_reference=offline_path,
            offline_frame_count=offline_frames,
            logger=self.logger,
        )

        self._configure_lock_cut_origin(clips, offline_frames)

        self._debug_dir = resolve_debug_dir(self.config)
        if self._debug_dir:
            self.logger.info("Debug frames → %s", self._debug_dir)

        self._batch_ready = True
        self.logger.info("Batch: %d clips (%d skipped)", len(clips), len(skipped))
        return clips, skipped

    def _configure_lock_cut_origin(self, clips: list[Any], offline_frames: int) -> None:
        """Locate the lock-cut origin on the hub timeline; warn on short reference coverage."""
        timeline = None
        if self.resolve is not None:
            try:
                timeline = self.resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
            except Exception:
                timeline = None

        starts = [clip_hub_start(item) for item in clips]
        ends: list[int] = []
        for item in clips:
            try:
                ends.append(int(item.GetStart()) + int(item.GetDuration()))
            except Exception:
                ends.append(clip_hub_start(item))

        hub_end = timeline_end_frame(timeline, starts, ends) if timeline else max(ends or [0])
        origin = detect_lock_cut_hub_origin(
            timeline_end_frame=hub_end,
            lock_cut_frame_count=offline_frames,
            clip_starts=starts,
            config=self.config,
            logger=self.logger,
        )
        self.frames.offline_resolver.configure_lock_cut(lock_cut_hub_origin=origin)

        span = (hub_end - min(starts)) if starts else hub_end
        coverage_warning = reference_coverage_warning(offline_frames, span, self.timeline_ctx.fps)
        if coverage_warning:
            self.logger.warning(coverage_warning)

    def analyze_one(
        self,
        timeline_item: Any,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ClipAnalysisResult | None:
        if not self._batch_ready:
            raise RuntimeError("Call prepare_batch() before analyze_one().")
        return self._analyze_single(timeline_item, should_cancel)

    def _analyze_single(
        self,
        timeline_item: Any,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ClipAnalysisResult | None:
        """Analyze one clip: setup → per-sample matching → clip-level decision.

        ``should_cancel`` is checked between samples and threaded into the refine
        search, so Stop interrupts within a clip (not only at clip boundaries).
        A clip cancelled mid-analysis returns None and is not applied.
        """
        ctx = self._begin_clip(timeline_item)
        if ctx is None:
            return None
        for point, local_frame in ctx.control_points:
            if should_cancel is not None and should_cancel():
                return None
            sample = self._process_sample(ctx, point, local_frame, should_cancel)
            if should_cancel is not None and should_cancel():
                return None  # refine was interrupted — discard the partial clip
            if self._should_stop_after(ctx, sample):
                break
        self._finalize_clip(ctx)
        return ctx.result

    def _clip_media_path(self, timeline_item: Any) -> str | None:
        """Media path for a video timeline item; None means skip the clip."""
        try:
            track_type, _ = timeline_item.GetTrackTypeAndIndex()
            if track_type != "video":
                return None
        except Exception:
            pass
        media_item = None
        try:
            media_item = timeline_item.GetMediaPoolItem()
        except Exception:
            pass
        media_path = resolve_media_path(media_item)
        if not media_path:
            self.logger.warning("Skipping clip without media path.")
            return None
        return media_path

    @staticmethod
    def _make_clip_result(timeline_item: Any, meta: Any, baseline: Any) -> ClipAnalysisResult:
        """Seed the per-clip result record with identity and baseline Edit values."""
        return ClipAnalysisResult(
            clip_name=meta["name"],
            timeline_item_id=meta["unique_id"],
            track_type=meta["track_type"],
            track_index=meta["track_index"],
            duration_frames=meta["duration"],
            timeline_item=timeline_item,
            baseline_zoom_x=baseline.zoom_x,
            baseline_zoom_y=baseline.zoom_y,
            baseline_pan=baseline.pan,
            baseline_tilt=baseline.tilt,
            baseline_rotation=baseline.rotation_deg,
        )

    def _log_match_mode(self) -> None:
        """Log whether matching runs absolute (vs lock-cut) or delta (composed on baseline)."""
        if is_absolute_edit_match(self.config):
            zoom_note = (
                "zoom-first, pan/tilt≈0"
                if bool(self.config.get("edit_priority_zoom", True))
                else "full transform"
            )
            self.logger.info(
                "Match mode: absolute (%s) — source fit vs lock-cut → Inspector.",
                zoom_note,
            )
        else:
            self.logger.info(
                "Match mode: delta — compare includes current Edit; result composes on baseline."
            )

    def _warn_clip_durations(self, result: ClipAnalysisResult, meta: Any, media_path: str) -> None:
        """Warn on suspiciously long clips and timeline durations exceeding the media."""
        max_duration = int(self.config.get("max_clip_duration_frames", 0))
        if max_duration > 0 and meta["duration"] > max_duration:
            self.logger.warning(
                "Clip '%s' is %d frames (>%d) — unusually long for a single shot.",
                result.clip_name,
                meta["duration"],
                max_duration,
            )
            result.warnings.append(f"duration {meta['duration']}f > limit {max_duration}f")

        media_frames = self.frames.get_media_frame_count(media_path)
        if media_frames > 0 and meta["duration"] > media_frames:
            self.logger.warning(
                "Clip '%s' timeline duration %d > media length %d frames — clamping reads.",
                result.clip_name,
                meta["duration"],
                media_frames,
            )
            result.warnings.append(
                f"timeline {meta['duration']}f longer than media file ({media_frames}f)"
            )

    def _ordered_control_points(self, duration: int, stop_on_confident: bool) -> list[Any]:
        """Sample points for the clip; MID first when a confident hit can stop early."""
        control_points = sample_points_for_clip(duration, self._sample_n, config=self.config)
        # When we only keep the single best sample (apply_transform_select="best"),
        # refining every sample is wasted work: try the mid frame first (most
        # representative of a shot) so a confident match lets us stop early.
        if stop_on_confident and len(control_points) > 1:
            control_points = sorted(
                control_points, key=lambda cp: 0 if cp[0] == SamplePoint.MID else 1
            )
        return control_points

    def _match_base_values(self) -> tuple[float, tuple[float, float], float]:
        """Loop-invariant match base (scale, center, angle); identity in absolute mode."""
        # Match base values are loop-invariant (config only); hoist so both the
        # accept path and the ECC-consensus rescue derive transforms identically.
        if is_absolute_edit_match(self.config):
            return 1.0, (0.0, 0.0), 0.0
        base_scale = float(
            self.config.get("transform_base_scale", self.config.get("fusion_base_size", 1.0))
        )
        base_center = tuple(
            self.config.get(
                "transform_base_center", self.config.get("fusion_base_center", [0.5, 0.5])
            )
        )
        base_angle = float(
            self.config.get("transform_base_angle", self.config.get("fusion_base_angle", 0.0))
        )
        return base_scale, (float(base_center[0]), float(base_center[1])), base_angle

    def _begin_clip(self, timeline_item: Any) -> _ClipContext | None:
        """Validate the clip and build the loop-invariant analysis context."""
        media_path = self._clip_media_path(timeline_item)
        if media_path is None:
            return None

        from matchref.resolve_api import timeline_item_info

        meta = timeline_item_info(timeline_item)
        baseline = read_clip_edit_transform(timeline_item)
        result = self._make_clip_result(timeline_item, meta, baseline)
        self.logger.info(
            "Clip baseline Edit: Zoom=%.3f Pan=%.1f Tilt=%.1f Rot=%.1f",
            baseline.zoom_x,
            baseline.pan,
            baseline.tilt,
            baseline.rotation_deg,
        )
        self._log_match_mode()
        self._warn_clip_durations(result, meta, media_path)

        stop_on_confident = str(
            self.config.get("apply_transform_select", "best")
        ).lower() == "best" and bool(self.config.get("refine_stop_on_confident_sample", True))
        match_base_scale, match_base_center, match_base_angle = self._match_base_values()

        return _ClipContext(
            timeline_item=timeline_item,
            result=result,
            baseline=baseline,
            media_path=media_path,
            canvas_size=self._timeline_canvas_size(),
            control_points=self._ordered_control_points(meta["duration"], stop_on_confident),
            stop_on_confident=stop_on_confident,
            confident_score=float(self.config.get("confident_sample_score", 0.96)),
            min_scale_apply=float(self.config.get("min_alignment_scale", 0.4)),
            max_scale_apply=float(self.config.get("max_alignment_scale", 2.5)),
            match_base_scale=match_base_scale,
            match_base_center=match_base_center,
            match_base_angle=match_base_angle,
        )

    def _resolve_sample_mapping(
        self, ctx: _ClipContext, point: Any, local_frame: int
    ) -> tuple[Any, Any, int]:
        """Return (source_info, offline_mapping, clamped_source_frame) for a clip-local frame."""
        # Clamping is warned on the clip result: it means the conform points past the media.
        src = build_clip_source_info(
            ctx.timeline_item, local_frame, ctx.result.clip_name, config=self.config
        )
        mapping = self.frames.offline_resolver.resolve(
            timeline_frame=src.timeline_frame,
            reel_name=src.reel_name,
            source_frame=src.source_frame,
            clip_name=src.clip_name,
            media_basename=src.media_basename,
        )
        clamped_src = self.frames.clamp_source_frame(ctx.media_path, src.source_frame)
        if clamped_src != src.source_frame:
            ctx.result.warnings.append(
                f"{point.value}: source frame {src.source_frame} clamped to {clamped_src}"
            )
        return src, mapping, clamped_src

    def _decode_online_frame(
        self, ctx: _ClipContext, clamped_src: int
    ) -> tuple[Any, Any, bool, bool]:
        """Decode + pre-shape the online frame; returns (online, online_raw, compensate, absolute)."""
        # Absolute edit match fits the raw frame to the canvas; otherwise the clip's
        # baseline Edit transform is simulated onto it when in range.
        online_raw = self.frames.get_online_frame(ctx.media_path, clamped_src)
        absolute = is_absolute_edit_match(self.config)
        compensate = (
            not absolute
            and use_baseline_compensation_for_match(self.config)
            and should_simulate_edit_for_match(ctx.baseline, ctx.canvas_size, self.config)
        )
        if online_raw is not None and absolute:
            online = _fit_frame_to_canvas(
                online_raw,
                ctx.canvas_size[0],
                ctx.canvas_size[1],
                str(self.config.get("input_scaling", "fit")),
            )
        else:
            online = online_raw  # type: ignore[assignment]
            if online is not None and compensate:
                online = apply_clip_edit_to_frame(
                    online, ctx.baseline, ctx.canvas_size, self.config
                )
            elif online is not None and bool(self.config.get("compensate_clip_transform", False)):
                self.logger.debug(
                    "Skip Edit simulation (pan/tilt/zoom out of range); matching raw source"
                )
        return online, online_raw, compensate, absolute

    def _decode_offline_frame(
        self, ctx: _ClipContext, point: Any, offline_frame: int
    ) -> tuple[Any, int]:
        """Decode the offline reference frame, warning when the decoder lands off-index."""
        offline, offline_reported = self.frames.get_offline_frame_at_index_with_meta(offline_frame)
        if offline_reported >= 0 and offline_reported != offline_frame:
            ctx.result.warnings.append(
                f"{point.value}: offline decode index {offline_reported} "
                f"(requested {offline_frame})"
            )
        return offline, offline_reported

    def _terminal_sample(
        self, ctx: _ClipContext, sample: TransformSample, point: Any, message: str, warning: str
    ) -> _SampleFrames:
        """Record a failed sample on the clip result and mark the clip as a problem."""
        sample.message = message
        ctx.result.samples.append(sample)
        ctx.result.problem = True
        ctx.result.warnings.append(f"{point.value}: {warning}")
        return _SampleFrames(sample=sample, terminal=True)

    def _conform_match_missing(self, mapping: Any) -> bool:
        """True when strict conform mapping is requested but this frame fell back to timeline math."""
        return (
            self.conform.is_available
            and mapping.source.value == "timeline"
            and str(self.config.get("offline_mapping_mode", "auto")) == "conform"
        )

    def _log_sample_compare(
        self,
        ctx: _ClipContext,
        point: Any,
        src: Any,
        mapping: Any,
        clamped_src: int,
        compensated: bool,
        offline_reported: int,
    ) -> str:
        """Log the online↔offline mapping for one sample; returns the compare line."""
        compare_line = format_sample_compare_line(
            media_path=ctx.media_path,
            source_frame=clamped_src,
            timeline_frame=src.timeline_frame,
            offline_frame=mapping.offline_frame,
            offline_mapping=mapping.source.value,
            mapping_detail=mapping.detail or "",
            reel=src.reel_name,
            baseline_zoom=ctx.baseline.zoom_x,
            compensated=compensated,
            offline_decoded_index=offline_reported,
        )
        self.logger.info(
            "[%s %s] %s", ctx.result.clip_name, point.value, compare_line.replace("\n", " | ")
        )
        return compare_line

    @staticmethod
    def _make_sample(point: Any, local_frame: int, src: Any, mapping: Any) -> TransformSample:
        """Seed a sample record with the resolved mapping bookkeeping."""
        return TransformSample(
            sample_point=point,
            clip_local_frame=local_frame,
            timeline_frame=src.timeline_frame,
            offline_frame=mapping.offline_frame,
            offline_mapping=mapping.source.value,
            reel_name=src.reel_name,
            source_frame=src.source_frame,
        )

    def _load_sample(self, ctx: _ClipContext, point: Any, local_frame: int) -> _SampleFrames:
        """Resolve the offline mapping and decode the online/offline frame pair.

        Returns a ``_SampleFrames`` bundle; ``terminal`` is set (and the sample is
        already recorded on the result) when a frame is missing or no conform match
        exists, so the caller skips alignment.
        """
        src, mapping, clamped_src = self._resolve_sample_mapping(ctx, point, local_frame)
        online, online_raw, compensate, absolute = self._decode_online_frame(ctx, clamped_src)
        offline, offline_reported = self._decode_offline_frame(ctx, point, mapping.offline_frame)
        sample = self._make_sample(point, local_frame, src, mapping)

        if online is None:
            return self._terminal_sample(
                ctx, sample, point, "online frame unavailable", "online frame missing"
            )
        if offline is None:
            return self._terminal_sample(
                ctx,
                sample,
                point,
                "offline frame unavailable",
                f"offline frame missing (mapping={mapping.source.value})",
            )

        compare_line = self._log_sample_compare(
            ctx,
            point,
            src,
            mapping,
            clamped_src,
            compensated=compensate and online_raw is not None,
            offline_reported=offline_reported,
        )

        if self._conform_match_missing(mapping):
            return self._terminal_sample(
                ctx,
                sample,
                point,
                "conform match not found",
                f"no EDL/XML match for reel={src.reel_name!r}",
            )

        return _SampleFrames(
            sample=sample,
            terminal=False,
            online=online,
            offline=offline,
            online_raw=online_raw,
            compensate=compensate,
            mapping=mapping,
            compare_line=compare_line,
            clamped_src=clamped_src,
            absolute=absolute,
        )

    def _record_sample_reject(
        self,
        ctx: _ClipContext,
        point: Any,
        loaded: _SampleFrames,
        alignment: Any,
        admit: float,
        apply_floor: float,
        message: str,
        warning: str,
    ) -> TransformSample:
        """Append a rejected sample with its warning and debug bundle."""
        sample = loaded.sample
        sample.message = message
        ctx.result.samples.append(sample)
        ctx.result.warnings.append(f"{point.value}: {warning}")
        self._save_match_debug(ctx, point, loaded, alignment, admit, apply_floor)
        return sample

    def _passes_admission(
        self,
        ctx: _ClipContext,
        point: Any,
        loaded: _SampleFrames,
        alignment: Any,
        admit: float,
        apply_floor: float,
        can_refine: bool,
    ) -> bool:
        """Gate on the ECC admission score; a refine-worthy near-miss may proceed."""
        if alignment.ecc_score >= admit:
            return True
        min_refine_ecc = min_ecc_for_refine_attempt(self.config)
        if can_refine and alignment.ecc_score >= min_refine_ecc:
            self.logger.info(
                "[%s %s] ECC %.4f < admit %.2f — trying Resolve Edit refine",
                ctx.result.clip_name,
                point.value,
                alignment.ecc_score,
                admit,
            )
            return True
        self._record_sample_reject(
            ctx,
            point,
            loaded,
            alignment,
            admit,
            apply_floor,
            message=f"score below admission ({alignment.ecc_score:.4f} < {admit:.2f})",
            warning=(
                f"ECC {alignment.ecc_score:.4f} < {admit:.2f} "
                f"(refine needs >= {min_refine_ecc:.2f}, apply {apply_floor:.2f})"
            ),
        )
        return False

    def _timeline_warp(self, alignment: Any) -> Any:
        """Alignment warp rescaled from analysis resolution to the timeline canvas."""
        warp = alignment.warp_matrix
        if warp is not None and alignment.analysis_width > 0:
            warp = warp_analysis_to_timeline(
                warp,
                (alignment.analysis_height, alignment.analysis_width),
                self._timeline_canvas_size(),
            )
        return warp

    def _refine_skipped_by_score(self, ctx: _ClipContext, point: Any, alignment: Any) -> bool:
        """True when the ECC score alone clears the early-exit threshold (no refine needed)."""
        if not (
            refine_early_exit_enabled(self.config)
            and sample_score_passes(alignment.ecc_score, self.config)
        ):
            return False
        self.logger.info(
            "[%s %s] ECC %.4f >= threshold — skip Resolve Edit refine",
            ctx.result.clip_name,
            point.value,
            alignment.ecc_score,
        )
        return True

    def _run_refine(
        self,
        ctx: _ClipContext,
        point: Any,
        loaded: _SampleFrames,
        warp: Any,
        should_cancel: Callable[[], bool] | None,
    ) -> Any:
        """Run the Resolve Edit refine for a sample; sets its edit/score and logs the outcome."""
        sample = loaded.sample
        canvas = self._timeline_canvas_size()
        bl = None if loaded.absolute else ctx.baseline
        guess = initial_edit_from_warp(warp, canvas, self.config, bl)
        refine_outcome = refine_resolve_edit(
            loaded.online_raw,
            loaded.offline,
            canvas_size=canvas,
            config=self.config,
            initial=guess,
            warp=warp,
            should_cancel=should_cancel,
        )
        sample.refined_edit = refine_outcome.edit
        sample.ecc_score = score_from_refine_ncc(refine_outcome.ncc)
        pos_note = "+position" if refine_outcome.used_position else ""
        rot_note = (
            ""
            if abs(refine_outcome.edit.rotation_deg) < 0.05
            else f" rot={refine_outcome.edit.rotation_deg:.2f}"
        )
        self.logger.info(
            "[%s %s] Resolve Edit refine [%s]%s: zoom=%.4f pan=%.1f tilt=%.1f%s ncc=%.4f",
            ctx.result.clip_name,
            point.value,
            refine_outcome.strategy,
            pos_note,
            refine_outcome.edit.zoom_x,
            refine_outcome.edit.pan,
            refine_outcome.edit.tilt,
            rot_note,
            refine_outcome.ncc,
        )
        return refine_outcome

    def _stash_ecc_candidate(
        self, ctx: _ClipContext, sample: TransformSample, alignment: Any
    ) -> None:
        """Keep a refine-rejected sample's plausible direct-ECC geometry for consensus rescue."""
        # The refine wandered, but the direct ECC alignment may still be
        # right (common on low-structure smoke/sky). Stash its geometry so
        # cross-sample consensus can rescue the clip; drop the bad refined
        # edit so apply falls back to the ECC warp, not the wandered zoom.
        ecc_scale, ecc_px, ecc_py, ecc_ang = apply_match_flags(
            alignment,
            self.config,
            base_scale=ctx.match_base_scale,
            base_center=ctx.match_base_center,
            base_angle=ctx.match_base_angle,
        )
        if ctx.min_scale_apply <= float(ecc_scale) <= ctx.max_scale_apply:
            sample.refined_edit = None
            sample.scale = ecc_scale
            sample.position_x = ecc_px
            sample.position_y = ecc_py
            sample.rotation_deg = ecc_ang
            ctx.ecc_candidates.append(sample)

    def _refine_sample(
        self,
        ctx: _ClipContext,
        point: Any,
        loaded: _SampleFrames,
        alignment: Any,
        warp: Any,
        admit: float,
        apply_floor: float,
        should_cancel: Callable[[], bool] | None,
    ) -> TransformSample | None:
        """Refine to Edit parameters; returns the recorded sample on rejection, None when accepted."""
        refine_outcome = self._run_refine(ctx, point, loaded, warp, should_cancel)
        refine_ok, reasons, accept_via = sample_refine_accepted(
            ncc=refine_outcome.ncc,
            gradient_ncc=refine_outcome.gradient_ncc,
            edit=refine_outcome.edit,
            ecc_scale=alignment.scale,
            timeline_size=self._timeline_canvas_size(),
            config=self.config,
        )
        if refine_ok:
            loaded.sample.accept_via = accept_via
            return None
        self._stash_ecc_candidate(ctx, loaded.sample, alignment)
        message = "refine rejected: " + "; ".join(reasons)
        return self._record_sample_reject(
            ctx, point, loaded, alignment, admit, apply_floor, message=message, warning=message
        )

    def _accept_sample(
        self, ctx: _ClipContext, point: Any, loaded: _SampleFrames, alignment: Any
    ) -> None:
        """Mark the sample ok, derive its apply geometry, and record it on the clip."""
        sample = loaded.sample
        scale, pos_x, pos_y, angle = apply_match_flags(
            alignment,
            self.config,
            base_scale=ctx.match_base_scale,
            base_center=ctx.match_base_center,
            base_angle=ctx.match_base_angle,
        )
        sample.scale = scale
        sample.position_x = pos_x
        sample.position_y = pos_y
        sample.rotation_deg = angle
        sample.ok = True
        if not sample.accept_via:
            sample.accept_via = "score"  # ECC cleared threshold; no refine needed
        # Perspective (opt-in): solve a homography for this matched pair so apply can
        # use a Fusion CornerPin. Best-effort; the affine result above is unaffected.
        if bool(self.config.get("perspective_match_enabled", False)):
            from matchref.perspective import solve_homography

            sample.homography = solve_homography(
                loaded.online,
                loaded.offline,
                self.config,
                timeline_size=self._timeline_canvas_size(),
            )
        ctx.transform_vectors.append((scale, pos_x, pos_y, angle))
        ctx.result.samples.append(sample)
        log_sample(self.logger, ctx.result.clip_name, sample)

    def _align_sample(self, ctx: _ClipContext, loaded: _SampleFrames) -> Any:
        """Align the sample's online/offline pair (with neighbor-frame search)."""
        return self._align_with_neighbors(
            loaded.online,
            loaded.offline,
            ctx.media_path,
            loaded.clamped_src,
            loaded.mapping,
            online_raw=loaded.online_raw,
            offline_full=loaded.offline,
            baseline=ctx.baseline,
        )

    def _process_sample(
        self,
        ctx: _ClipContext,
        point: Any,
        local_frame: int,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TransformSample:
        """Match one sample frame; populate it and append to the clip context."""
        loaded = self._load_sample(ctx, point, local_frame)
        sample = loaded.sample
        if loaded.terminal:
            return sample

        alignment = self._align_sample(ctx, loaded)
        sample.ecc_score = alignment.ecc_score
        admit = alignment_admission_threshold(alignment, self.config)
        apply_floor = float(self.config.get("min_match_score", 0.95))
        can_refine = (
            bool(self.config.get("resolve_edit_refine", True))
            and is_maximum_precision(self.config)
            and loaded.online_raw is not None
        )

        if not alignment.ok:
            sample.message = alignment.message
            ctx.result.samples.append(sample)
            ctx.result.warnings.append(f"{point.value}: match failed ({alignment.message})")
            return sample

        if not self._passes_admission(
            ctx, point, loaded, alignment, admit, apply_floor, can_refine
        ):
            return sample

        warp = self._timeline_warp(alignment)
        sample.warp_matrix = warp

        skip_refine = self._refine_skipped_by_score(ctx, point, alignment)
        if can_refine and warp is not None and not skip_refine:
            assert loaded.online_raw is not None  # can_refine implies online_raw is not None
            rejected = self._refine_sample(
                ctx, point, loaded, alignment, warp, admit, apply_floor, should_cancel
            )
            if rejected is not None:
                return rejected

        self._accept_sample(ctx, point, loaded, alignment)
        self._save_match_debug(ctx, point, loaded, alignment, admit, apply_floor)
        return sample

    def _should_stop_after(self, ctx: _ClipContext, sample: TransformSample) -> bool:
        """Whether a confident, in-range sample lets us skip the remaining samples.

        Breaking after one confident sample is safe only if the clip is static.
        With keyframing on, a single (even very confident) MID match can't
        distinguish a static zoom from an animated ramp — detecting the ramp needs
        >=2 ok samples — so require >=2 ok samples that agree on zoom before
        short-circuiting; otherwise keep sampling.
        """
        if not (ctx.stop_on_confident and sample.ok):
            return False
        if not (
            float(sample.ecc_score) >= ctx.confident_score
            and ctx.min_scale_apply <= float(sample.scale) <= ctx.max_scale_apply
        ):
            return False
        ok_so_far = [s for s in ctx.result.samples if s.ok]
        keyframing = bool(self.config.get("apply_keyframes_on_variance", True))
        static_confirmed = confident_break_allowed(
            [float(s.scale) for s in ok_so_far],
            keyframing,
            float(self.config.get("keyframe_zoom_spread", 1.06)),
        )
        if static_confirmed:
            self.logger.info(
                "[%s %s] confident match (score %.4f >= %.2f) — skipping remaining samples",
                ctx.result.clip_name,
                sample.sample_point.value,
                sample.ecc_score,
                ctx.confident_score,
            )
            return True
        self.logger.info(
            "[%s %s] confident match (%.4f) but keyframing on and reframe not yet "
            "confirmed static — sampling more to rule out an animated reframe",
            ctx.result.clip_name,
            sample.sample_point.value,
            sample.ecc_score,
        )
        return False

    def _consensus_rescued(self, ctx: _ClipContext) -> list[TransformSample]:
        """ECC-consensus rescue for clips where no sample passed; returns [rep] or []."""
        # Low-structure shots (smoke, fire, flat sky) can fail every per-sample
        # refine (the search wanders, the structure gate trips) yet have a correct,
        # stable ECC zoom across independent frames. When ≥2 rejected samples agree
        # on a plausible ECC zoom — and the best of them clears the geometry-trust
        # floor — trust that geometry and apply it.
        ecc_candidates = ctx.ecc_candidates
        if not (ecc_candidates and bool(self.config.get("ecc_consensus_rescue", True))):
            return []
        scales = [float(s.scale) for s in ecc_candidates]
        rep = max(ecc_candidates, key=lambda s: float(s.ecc_score))
        if not ecc_consensus_passes(scales, float(rep.ecc_score), self.config):
            return []
        lo, hi = min(scales), max(scales)
        rep.ok = True
        rep.accept_via = "consensus"
        rep.message = (
            f"ECC-consensus rescue: {len(ecc_candidates)} samples agree on "
            f"zoom {lo:.3f}…{hi:.3f} (refine/structure unreliable on low-structure content)"
        )
        ctx.result.warnings.append(rep.message)
        self.logger.info("Quality check: %s — %s", ctx.result.clip_name, rep.message)
        return [rep]

    def _best_select_problems(
        self, ctx: _ClipContext, ok_samples: list[TransformSample], best: TransformSample
    ) -> tuple[list[str], bool]:
        """Validate the best sample's scale and classify motion; returns (reasons, animated)."""
        min_scale = float(self.config.get("min_alignment_scale", 0.4))
        max_scale = float(self.config.get("max_alignment_scale", 2.5))
        reasons: list[str] = []
        if not (min_scale <= best.scale <= max_scale):
            reasons.append(f"best sample scale {best.scale:.3f} outside [{min_scale}, {max_scale}]")
        # Samples may differ across the clip because the reframe is *keyframed*
        # (a smooth ramp) — that is a real animation to reproduce, not a fault.
        # Distinguish it from erratic disagreement (a bad/ambiguous match).
        animated = False
        if len(ok_samples) >= 2:
            varies, is_ramp = clip_motion_profile(ok_samples, self.config)
            keyframes_enabled = bool(self.config.get("apply_keyframes_on_variance", True))
            if varies and is_ramp and keyframes_enabled:
                animated = True
            else:
                # Reject only a *large* erratic disagreement (a bad/ambiguous
                # match). Small non-monotonic jitter is treated as static — the
                # best sample is applied (handled below).
                lo, hi, spread = scale_spread([s.scale for s in ok_samples])
                if spread > max_scale_spread(self.config):
                    reasons.append(
                        f"samples disagree {lo:.3f}…{hi:.3f} (×{spread:.2f}), not a smooth ramp"
                    )
        return reasons, animated

    def _log_best_outcome(
        self, ctx: _ClipContext, best: TransformSample, ok_samples: list[TransformSample]
    ) -> None:
        """Log the best-sample decision; non-score acceptances get an eyeball-check warning."""
        result = ctx.result
        if result.animated:
            self.logger.info(
                "Animated reframe: %s — %d keyframes (%s)",
                result.clip_name,
                len(ok_samples),
                ", ".join(
                    f"{s.sample_point.value} z={s.scale:.3f}"
                    for s in sorted(ok_samples, key=lambda s: s.clip_local_frame)
                ),
            )
            return
        via = best.accept_via or "score"
        if via == "score":
            self.logger.info(
                "Best sample: %s score %.4f (of %d ok)",
                best.sample_point.value,
                best.ecc_score,
                len(ok_samples),
            )
        else:
            # Not a strict-score match — geometry trusted via corroboration or
            # cross-sample consensus. Flag it so it gets an eyeball check.
            note = f"accepted via {via} (verify difference.jpg / near-black%)"
            result.warnings.append(note)
            self.logger.warning(
                "Best sample: %s score %.4f via %s — %s",
                best.sample_point.value,
                best.ecc_score,
                via,
                "geometry trusted, eyeball recommended",
            )

    def _finalize_best_select(self, ctx: _ClipContext, ok_samples: list[TransformSample]) -> None:
        """Keep the single best-matching sample; validate only its scale (the
        others may be weaker frames — we just don't use them). No keyframing."""
        result = ctx.result
        best = max(ok_samples, key=lambda s: s.ecc_score)
        select_reasons, animated = self._best_select_problems(ctx, ok_samples, best)
        result.problem = bool(select_reasons)
        result.animated = animated and not result.problem
        for msg in select_reasons:
            result.warnings.append(msg)
            self.logger.warning("Quality check: %s — %s", result.clip_name, msg)
        if not result.problem:
            self._log_best_outcome(ctx, best, ok_samples)
        result.use_mid_key = False

    def _finalize_multi_sample(self, ctx: _ClipContext, ok_samples: list[TransformSample]) -> None:
        """Gate the clip on aggregate sample quality and pick the keyframing mode."""
        result = ctx.result
        passed, fail_reasons = assess_clip_match_quality(ok_samples, self.config)
        result.problem = not passed
        for reason in fail_reasons:
            result.warnings.append(reason)
            self.logger.warning("Quality check: %s — %s", result.clip_name, reason)
        stable = transforms_are_stable(
            [(s.scale, s.position_x, s.position_y, s.rotation_deg) for s in ok_samples],
            self._variance_threshold,
        )
        use_mid = bool(self.config.get("use_midpoint_keyframe", True)) and not stable
        result.use_mid_key = use_mid and any(s.sample_point == SamplePoint.MID for s in ok_samples)

    def _finalize_clip(self, ctx: _ClipContext) -> None:
        """Apply the clip-level decision (consensus rescue, best/quality gate)."""
        result = ctx.result
        ok_samples = [s for s in result.samples if s.ok]
        if not ok_samples:
            ok_samples = self._consensus_rescued(ctx)
        min_ok = self._min_ok_samples_required()
        select_best = str(self.config.get("apply_transform_select", "best")).lower() == "best"
        if ok_samples and select_best:
            self._finalize_best_select(ctx, ok_samples)
        elif len(ok_samples) >= min_ok:
            self._finalize_multi_sample(ctx, ok_samples)
        else:
            result.problem = True
            stable = transforms_are_stable(ctx.transform_vectors, self._variance_threshold)
            result.use_mid_key = bool(self.config.get("use_midpoint_keyframe", True)) and not stable

        if result.problem:
            self.logger.warning(
                "Clip marked as problem: %s (%d/%d samples ok, min %d)",
                result.clip_name,
                len(ok_samples),
                len(result.samples),
                min_ok,
            )

    def _render_applied_result(
        self, sample: Any, online_raw: np.ndarray, offline: np.ndarray, extra: list[str]
    ) -> np.ndarray | None:
        """Render the Inspector-applied result for the debug stack; appends its stat lines."""
        try:
            from matchref.clip_edit_transform import (
                ClipEditTransform,
                apply_clip_edit_to_frame,
            )
            from matchref.transform_convert import resolve_transform

            canvas = self._timeline_canvas_size()
            rt = resolve_transform(sample, self.config, canvas)
            edit = ClipEditTransform(
                zoom_x=rt.edit_zoom_x,
                zoom_y=rt.edit_zoom_y,
                pan=rt.edit_pan,
                tilt=rt.edit_tilt,
                rotation_deg=rt.edit_rotation,
            )
            result_render = apply_clip_edit_to_frame(online_raw, edit, canvas, self.config)
            extra.append(
                f"applied Inspector: Zoom={rt.edit_zoom_x:.4f} Pan={rt.edit_pan:.1f} "
                f"Tilt={rt.edit_tilt:.1f} Rot={rt.edit_rotation:.2f} "
                f"(input_scaling={self.config.get('input_scaling', 'fit')})"
            )
            # Content-robust quality of the *applied* result. near-black is fooled
            # by overlays/bright/non-rigid content; structure (edge gradient) is
            # the reliable number — low here = the geometry is actually off.
            from matchref.precision_align import _Reference

            g = _Reference(offline, self.config).gradient_ncc(result_render)
            flag = "  <-- LOW, likely misaligned" if g < 0.4 else ""
            extra.append(f"applied structure (gradient-NCC of result vs offline): {g:.3f}{flag}")
            return result_render
        except Exception:  # noqa: BLE001
            return None

    def _save_match_debug(
        self,
        ctx: _ClipContext,
        point: Any,
        loaded: _SampleFrames,
        alignment: Any,
        admit: float,
        apply_floor: float,
    ) -> None:
        if self._debug_dir is None:
            return
        sample = loaded.sample
        if sample.ok:
            via = sample.accept_via or "score"
            match_line = (
                f"match: OK [{via}] scale={sample.scale:.4f} rot={sample.rotation_deg:.2f} "
                f"ecc={sample.ecc_score:.4f} (apply min {apply_floor:.2f}) — {sample.message}"
            )
        else:
            match_line = (
                f"match: FAIL ecc={alignment.ecc_score:.4f} "
                f"(admit {admit:.2f}, apply {apply_floor:.2f}) — {sample.message}"
            )

        # Render exactly what gets written to the Inspector and stack it against the
        # offline, so the debug shows the *result*, not just the pre-transform inputs.
        result_render: np.ndarray | None = None
        extra = [loaded.compare_line, match_line]
        if sample.ok and loaded.online_raw is not None:
            result_render = self._render_applied_result(
                sample, loaded.online_raw, loaded.offline, extra
            )

        image_path = save_match_debug(
            self._debug_dir,
            clip_name=ctx.result.clip_name,
            sample_point=point.value,
            online_for_match=loaded.online,
            offline_ref=loaded.offline,
            online_raw=loaded.online_raw,
            result_render=result_render,
            mapping_detail=loaded.mapping.detail or loaded.mapping.source.value,
            extra_lines=extra,
        )
        self.logger.info("  debug: %s", image_path)

    def _align_with_neighbors(
        self,
        online: np.ndarray,
        offline: np.ndarray,
        media_path: str,
        source_frame: int,
        mapping: Any,
        *,
        online_raw: np.ndarray | None = None,
        offline_full: np.ndarray | None = None,
        baseline: Any = None,
    ) -> Any:
        _ = online_raw, offline_full, baseline
        radius = int(self.config.get("alignment_frame_search_radius", 0))
        max_w = self.timeline_ctx.analysis_max_width(self.config)
        canvas = self._timeline_canvas_size()
        best = align_frames(
            online,
            offline,
            self.config,
            analysis_max_width=max_w,
            timeline_size=canvas,
        )
        if best.ok and best.ecc_score >= alignment_admission_threshold(best, self.config):
            return best

        for delta in range(1, radius + 1):
            offsets = (
                (delta, delta),
                (-delta, -delta),
                (delta, 0),
                (0, delta),
                (-delta, 0),
                (0, -delta),
            )
            for src_off, off_off in offsets:
                alt_src = self.frames.clamp_source_frame(media_path, source_frame + src_off)
                alt_on = self.frames.get_online_frame(media_path, alt_src)
                alt_off = self.frames.get_offline_frame_at_index(mapping.offline_frame + off_off)
                if alt_on is None or alt_off is None:
                    continue
                trial = align_frames(
                    alt_on,
                    alt_off,
                    self.config,
                    analysis_max_width=max_w,
                    timeline_size=canvas,
                )
                if trial.ok and (not best.ok or trial.ecc_score > best.ecc_score):
                    best = trial
        return best

    def _timeline_canvas_size(self) -> tuple[int, int]:
        return self.timeline_ctx.resolution

    def _min_ok_samples_required(self) -> int:
        if str(self.config.get("match_sample_mode", "single")).lower() == "single":
            return 1
        # "best" picks the single best-matching sample, so one good sample is enough.
        if str(self.config.get("apply_transform_select", "best")).lower() == "best":
            return 1
        return int(self.config.get("min_ok_samples_per_clip", 2))

    def close(self) -> None:
        self.frames.close()


def _clip_name(item: Any) -> str:
    try:
        return item.GetName() or "?"
    except Exception:
        return "?"


def confident_break_allowed(
    ok_scales: list[float],
    keyframing: bool,
    zoom_spread_limit: float,
) -> bool:
    """Whether a confident sample may short-circuit the remaining samples.

    Safe immediately when keyframing is off (we only ever apply one static value).
    With keyframing on, a single confident MID match cannot tell a static zoom from
    an animated ramp, so require >=2 ok samples that agree on zoom before stopping;
    otherwise keep sampling so an animated reframe can still be detected.
    """
    if not keyframing:
        return True
    if len(ok_scales) < 2:
        return False
    _, _, spread = scale_spread(ok_scales)
    return spread <= zoom_spread_limit
