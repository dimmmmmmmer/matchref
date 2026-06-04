"""Clip analysis pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
    timeline_end_frame,
)
from matchref.logging_report import log_sample
from matchref.match_quality import (
    assess_clip_match_quality,
    ecc_consensus_passes,
    max_scale_spread,
    min_ecc_for_refine_attempt,
    refine_early_exit_enabled,
    sample_refine_accepted,
    sample_score_passes,
    scale_spread,
    score_from_refine_ncc,
)
from matchref.models import AnalysisReport, ClipAnalysisResult, SamplePoint, TransformSample
from matchref.precision_align import (
    initial_edit_from_warp,
    is_maximum_precision,
    refine_resolve_edit,
)
from matchref.timeline_context import TimelineContext


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
        self.frames = FrameProvider(config, offline_resolver=resolver)
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
        self.frames.offline_resolver.configure_lock_cut(
            lock_cut_hub_origin=origin,
            lock_cut_frame_count=offline_frames,
            timeline_end_frame=hub_end,
        )

        self._debug_dir = resolve_debug_dir(self.config)
        if self._debug_dir:
            self.logger.info("Debug frames → %s", self._debug_dir)

        self._batch_ready = True
        self.logger.info("Batch: %d clips (%d skipped)", len(clips), len(skipped))
        return clips, skipped

    def analyze_clips(
        self,
        timeline_items: list[Any],
        *,
        on_clip_status: Callable[[int, int, str, str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> AnalysisReport:
        clips, skipped = self.prepare_batch(timeline_items)
        report = AnalysisReport()
        report.skipped.extend(skipped)
        total = len(clips)

        for index, item in enumerate(clips, start=1):
            if should_cancel and should_cancel():
                self.logger.info("Cancelled after %d/%d clips", index - 1, total)
                break
            name = _clip_name(item)
            if on_clip_status:
                on_clip_status(index, total, "analyze", name)
            self.logger.info("--- Clip %d/%d: %s ---", index, total, name)
            info = self._analyze_single(item)
            if info is not None:
                report.results.append(info)
            if on_clip_status:
                on_clip_status(index, total, "done", name)

        return report

    def analyze_one(self, timeline_item: Any) -> ClipAnalysisResult | None:
        if not self._batch_ready:
            raise RuntimeError("Call prepare_batch() before analyze_one().")
        return self._analyze_single(timeline_item)

    def _analyze_single(self, timeline_item: Any) -> ClipAnalysisResult | None:
        variance_threshold = self._variance_threshold
        sample_n = self._sample_n
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

        from matchref.resolve_api import timeline_item_info

        meta = timeline_item_info(timeline_item)
        baseline = read_clip_edit_transform(timeline_item)
        result = ClipAnalysisResult(
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
        self.logger.info(
            "Clip baseline Edit: Zoom=%.3f Pan=%.1f Tilt=%.1f Rot=%.1f",
            baseline.zoom_x,
            baseline.pan,
            baseline.tilt,
            baseline.rotation_deg,
        )
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
        canvas_size = self._timeline_canvas_size()

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

        control_points = sample_points_for_clip(meta["duration"], sample_n, config=self.config)
        # When we only keep the single best sample (apply_transform_select="best"),
        # refining every sample is wasted work: try the mid frame first (most
        # representative of a shot) so a confident match lets us stop early.
        stop_on_confident = (
            str(self.config.get("apply_transform_select", "best")).lower() == "best"
            and bool(self.config.get("refine_stop_on_confident_sample", True))
        )
        confident_score = float(self.config.get("confident_sample_score", 0.96))
        min_scale_apply = float(self.config.get("min_alignment_scale", 0.4))
        max_scale_apply = float(self.config.get("max_alignment_scale", 2.5))
        if stop_on_confident and len(control_points) > 1:
            control_points = sorted(
                control_points, key=lambda cp: 0 if cp[0] == SamplePoint.MID else 1
            )
        base_scale = float(
            self.config.get("transform_base_scale", self.config.get("fusion_base_size", 1.0))
        )
        base_center = tuple(
            self.config.get("transform_base_center", self.config.get("fusion_base_center", [0.5, 0.5]))
        )
        base_angle = float(
            self.config.get("transform_base_angle", self.config.get("fusion_base_angle", 0.0))
        )

        # Match base values are loop-invariant (config only); hoist so both the
        # accept path and the ECC-consensus rescue derive transforms identically.
        if is_absolute_edit_match(self.config):
            match_base_scale = 1.0
            match_base_center = (0.0, 0.0)
            match_base_angle = 0.0
        else:
            match_base_scale = base_scale
            match_base_center = (float(base_center[0]), float(base_center[1]))
            match_base_angle = base_angle

        transform_vectors: list[tuple[float, float, float, float]] = []
        # Samples whose refine was rejected but whose direct ECC alignment is
        # plausible. Used to rescue low-structure clips (smoke/sky) where the ECC
        # zoom is reliable and corroborated across frames but the refine search and
        # the structure gate are not.
        ecc_candidates: list[TransformSample] = []

        for point, local_frame in control_points:
            src = build_clip_source_info(
                timeline_item, local_frame, result.clip_name, config=self.config
            )
            mapping = self.frames.offline_resolver.resolve(
                timeline_frame=src.timeline_frame,
                reel_name=src.reel_name,
                source_frame=src.source_frame,
                clip_name=src.clip_name,
                media_basename=src.media_basename,
            )

            clamped_src = self.frames.clamp_source_frame(media_path, src.source_frame)
            if clamped_src != src.source_frame:
                result.warnings.append(
                    f"{point.value}: source frame {src.source_frame} clamped to {clamped_src}"
                )
            online_raw = self.frames.get_online_frame(media_path, clamped_src)
            absolute = is_absolute_edit_match(self.config)
            compensate = (
                not absolute
                and use_baseline_compensation_for_match(self.config)
                and should_simulate_edit_for_match(baseline, canvas_size, self.config)
            )
            if online_raw is not None and absolute:
                online = _fit_frame_to_canvas(
                    online_raw,
                    canvas_size[0],
                    canvas_size[1],
                    str(self.config.get("input_scaling", "fit")),
                )
            else:
                online = online_raw
                if online is not None and compensate:
                    online = apply_clip_edit_to_frame(online, baseline, canvas_size, self.config)
                elif online is not None and bool(self.config.get("compensate_clip_transform", False)):
                    self.logger.debug(
                        "Skip Edit simulation (pan/tilt/zoom out of range); matching raw source"
                    )
            offline, offline_reported = self.frames.get_offline_frame_at_index_with_meta(
                mapping.offline_frame
            )
            if offline_reported >= 0 and offline_reported != mapping.offline_frame:
                result.warnings.append(
                    f"{point.value}: offline decode index {offline_reported} "
                    f"(requested {mapping.offline_frame})"
                )

            sample = TransformSample(
                sample_point=point,
                clip_local_frame=local_frame,
                timeline_frame=src.timeline_frame,
                offline_frame=mapping.offline_frame,
                offline_mapping=mapping.source.value,
                reel_name=src.reel_name,
                source_frame=src.source_frame,
            )

            if online is None:
                sample.message = "online frame unavailable"
                result.samples.append(sample)
                result.problem = True
                result.warnings.append(f"{point.value}: online frame missing")
                continue
            if offline is None:
                sample.message = "offline frame unavailable"
                result.samples.append(sample)
                result.problem = True
                result.warnings.append(
                    f"{point.value}: offline frame missing (mapping={mapping.source.value})"
                )
                continue

            compare_line = format_sample_compare_line(
                media_path=media_path,
                source_frame=clamped_src,
                timeline_frame=src.timeline_frame,
                offline_frame=mapping.offline_frame,
                offline_mapping=mapping.source.value,
                mapping_detail=mapping.detail or "",
                reel=src.reel_name,
                baseline_zoom=baseline.zoom_x,
                compensated=compensate and online_raw is not None,
                offline_decoded_index=offline_reported,
            )
            self.logger.info("[%s %s] %s", result.clip_name, point.value, compare_line.replace("\n", " | "))

            if (
                self.conform.is_available
                and mapping.source.value == "timeline"
                and str(self.config.get("offline_mapping_mode", "auto")) == "conform"
            ):
                sample.message = "conform match not found"
                result.samples.append(sample)
                result.problem = True
                result.warnings.append(f"{point.value}: no EDL/XML match for reel={src.reel_name!r}")
                continue

            alignment = self._align_with_neighbors(
                online,
                offline,
                media_path,
                clamped_src,
                mapping,
                online_raw=online_raw,
                offline_full=offline,
                baseline=baseline,
            )
            sample.ecc_score = alignment.ecc_score
            admit = alignment_admission_threshold(alignment, self.config)
            apply_floor = float(self.config.get("min_match_score", 0.95))
            min_refine_ecc = min_ecc_for_refine_attempt(self.config)
            canvas = self._timeline_canvas_size()
            can_refine = (
                bool(self.config.get("resolve_edit_refine", True))
                and is_maximum_precision(self.config)
                and online_raw is not None
            )

            if not alignment.ok:
                sample.message = alignment.message
                result.samples.append(sample)
                result.warnings.append(f"{point.value}: match failed ({alignment.message})")
                continue

            if alignment.ecc_score < admit:
                if can_refine and alignment.ecc_score >= min_refine_ecc:
                    self.logger.info(
                        "[%s %s] ECC %.4f < admit %.2f — trying Resolve Edit refine",
                        result.clip_name,
                        point.value,
                        alignment.ecc_score,
                        admit,
                    )
                else:
                    sample.message = (
                        f"score below admission ({alignment.ecc_score:.4f} < {admit:.2f})"
                    )
                    result.samples.append(sample)
                    result.warnings.append(
                        f"{point.value}: ECC {alignment.ecc_score:.4f} < {admit:.2f} "
                        f"(refine needs >= {min_refine_ecc:.2f}, apply {apply_floor:.2f})"
                    )
                    self._save_match_debug(
                        result,
                        point,
                        sample,
                        online,
                        offline,
                        online_raw,
                        compensate,
                        mapping,
                        compare_line,
                        alignment,
                        admit,
                        apply_floor,
                    )
                    continue

            warp = alignment.warp_matrix
            if warp is not None and alignment.analysis_width > 0:
                warp = warp_analysis_to_timeline(
                    warp,
                    (alignment.analysis_height, alignment.analysis_width),
                    self._timeline_canvas_size(),
                )
            sample.warp_matrix = warp

            skip_refine = (
                refine_early_exit_enabled(self.config)
                and sample_score_passes(alignment.ecc_score, self.config)
            )
            if skip_refine:
                self.logger.info(
                    "[%s %s] ECC %.4f >= threshold — skip Resolve Edit refine",
                    result.clip_name,
                    point.value,
                    alignment.ecc_score,
                )
            refine_outcome = None
            if can_refine and warp is not None and not skip_refine:
                bl = None if absolute else baseline
                guess = initial_edit_from_warp(warp, canvas, self.config, bl)
                refine_outcome = refine_resolve_edit(
                    online_raw,
                    offline,
                    canvas_size=canvas,
                    config=self.config,
                    initial=guess,
                    warp=warp,
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
                    result.clip_name,
                    point.value,
                    refine_outcome.strategy,
                    pos_note,
                    refine_outcome.edit.zoom_x,
                    refine_outcome.edit.pan,
                    refine_outcome.edit.tilt,
                    rot_note,
                    refine_outcome.ncc,
                )
                refine_ok, reasons, accept_via = sample_refine_accepted(
                    ncc=refine_outcome.ncc,
                    gradient_ncc=refine_outcome.gradient_ncc,
                    edit=refine_outcome.edit,
                    ecc_scale=alignment.scale,
                    timeline_size=canvas,
                    config=self.config,
                )
                if refine_ok:
                    sample.accept_via = accept_via
                if not refine_ok:
                    sample.message = "refine rejected: " + "; ".join(reasons)
                    # The refine wandered, but the direct ECC alignment may still be
                    # right (common on low-structure smoke/sky). Stash its geometry so
                    # cross-sample consensus can rescue the clip; drop the bad refined
                    # edit so apply falls back to the ECC warp, not the wandered zoom.
                    ecc_scale, ecc_px, ecc_py, ecc_ang = apply_match_flags(
                        alignment,
                        self.config,
                        base_scale=match_base_scale,
                        base_center=match_base_center,
                        base_angle=match_base_angle,
                    )
                    if min_scale_apply <= float(ecc_scale) <= max_scale_apply:
                        sample.refined_edit = None
                        sample.scale = ecc_scale
                        sample.position_x = ecc_px
                        sample.position_y = ecc_py
                        sample.rotation_deg = ecc_ang
                        ecc_candidates.append(sample)
                    result.samples.append(sample)
                    result.warnings.append(f"{point.value}: {sample.message}")
                    self._save_match_debug(
                        result,
                        point,
                        sample,
                        online,
                        offline,
                        online_raw,
                        compensate,
                        mapping,
                        compare_line,
                        alignment,
                        admit,
                        apply_floor,
                    )
                    continue

            scale, pos_x, pos_y, angle = apply_match_flags(
                alignment,
                self.config,
                base_scale=match_base_scale,
                base_center=match_base_center,
                base_angle=match_base_angle,
            )
            sample.scale = scale
            sample.position_x = pos_x
            sample.position_y = pos_y
            sample.rotation_deg = angle
            sample.ok = True
            if not sample.accept_via:
                sample.accept_via = "score"  # ECC cleared threshold; no refine needed
            transform_vectors.append((scale, pos_x, pos_y, angle))
            result.samples.append(sample)
            log_sample(self.logger, result.clip_name, sample)

            self._save_match_debug(
                result,
                point,
                sample,
                online,
                offline,
                online_raw,
                compensate,
                mapping,
                compare_line,
                alignment,
                admit,
                apply_floor,
            )

            if (
                stop_on_confident
                and float(sample.ecc_score) >= confident_score
                and min_scale_apply <= float(sample.scale) <= max_scale_apply
            ):
                self.logger.info(
                    "[%s %s] confident match (score %.4f >= %.2f) — skipping remaining samples",
                    result.clip_name,
                    point.value,
                    sample.ecc_score,
                    confident_score,
                )
                break

        ok_samples = [s for s in result.samples if s.ok]

        # ECC-consensus rescue: low-structure shots (smoke, fire, flat sky) can fail
        # every per-sample refine (the search wanders, the structure gate trips) yet
        # have a correct, stable ECC zoom across independent frames. When no sample
        # passed but ≥2 rejected samples agree on a plausible ECC zoom — and the best
        # of them clears the geometry-trust floor — trust that geometry and apply it.
        if (
            not ok_samples
            and ecc_candidates
            and bool(self.config.get("ecc_consensus_rescue", True))
        ):
            scales = [float(s.scale) for s in ecc_candidates]
            rep = max(ecc_candidates, key=lambda s: float(s.ecc_score))
            if ecc_consensus_passes(scales, float(rep.ecc_score), self.config):
                lo, hi = min(scales), max(scales)
                rep.ok = True
                rep.accept_via = "consensus"
                rep.message = (
                    f"ECC-consensus rescue: {len(ecc_candidates)} samples agree on "
                    f"zoom {lo:.3f}…{hi:.3f} (refine/structure unreliable on low-structure content)"
                )
                ok_samples = [rep]
                result.warnings.append(rep.message)
                self.logger.info("Quality check: %s — %s", result.clip_name, rep.message)
        min_ok = self._min_ok_samples_required()
        select_best = str(self.config.get("apply_transform_select", "best")).lower() == "best"
        if ok_samples and select_best:
            # Keep the single best-matching sample; validate only its scale (the
            # others may be weaker frames — we just don't use them). No keyframing.
            best = max(ok_samples, key=lambda s: s.ecc_score)
            min_scale = float(self.config.get("min_alignment_scale", 0.4))
            max_scale = float(self.config.get("max_alignment_scale", 2.5))
            reasons: list[str] = []
            if not (min_scale <= best.scale <= max_scale):
                reasons.append(
                    f"best sample scale {best.scale:.3f} outside [{min_scale}, {max_scale}]"
                )
            # Cross-sample corroboration: if several samples survived, their zoom must
            # agree — independent frames landing on the same zoom is strong evidence the
            # geometry is right (and rules out a single-frame fluke).
            if len(ok_samples) >= 2:
                lo, hi, spread = scale_spread([s.scale for s in ok_samples])
                if spread > max_scale_spread(self.config):
                    reasons.append(
                        f"samples disagree on zoom {lo:.3f}…{hi:.3f} (×{spread:.2f})"
                    )
            result.problem = bool(reasons)
            for msg in reasons:
                result.warnings.append(msg)
                self.logger.warning("Quality check: %s — %s", result.clip_name, msg)
            if not result.problem:
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
            result.use_mid_key = False
        elif len(ok_samples) >= min_ok:
            passed, fail_reasons = assess_clip_match_quality(ok_samples, self.config)
            result.problem = not passed
            for reason in fail_reasons:
                result.warnings.append(reason)
                self.logger.warning("Quality check: %s — %s", result.clip_name, reason)
            stable = transforms_are_stable(
                [(s.scale, s.position_x, s.position_y, s.rotation_deg) for s in ok_samples],
                variance_threshold,
            )
            use_mid = bool(self.config.get("use_midpoint_keyframe", True)) and not stable
            result.use_mid_key = use_mid and any(
                s.sample_point == SamplePoint.MID for s in ok_samples
            )
        else:
            result.problem = True
            stable = transforms_are_stable(transform_vectors, variance_threshold)
            result.use_mid_key = bool(self.config.get("use_midpoint_keyframe", True)) and not stable

        if result.problem:
            self.logger.warning(
                "Clip marked as problem: %s (%d/%d samples ok, min %d)",
                result.clip_name,
                len(ok_samples),
                len(result.samples),
                min_ok,
            )
        return result

    def _save_match_debug(
        self,
        result: ClipAnalysisResult,
        point: Any,
        sample: Any,
        online: np.ndarray,
        offline: np.ndarray,
        online_raw: np.ndarray | None,
        compensate: bool,
        mapping: Any,
        compare_line: str,
        alignment: Any,
        admit: float,
        apply_floor: float,
    ) -> None:
        if self._debug_dir is None:
            return
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
        extra = [compare_line, match_line]
        if sample.ok and online_raw is not None:
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
            except Exception:  # noqa: BLE001
                result_render = None

        image_path = save_match_debug(
            self._debug_dir,
            clip_name=result.clip_name,
            sample_point=point.value,
            online_for_match=online,
            offline_ref=offline,
            online_raw=online_raw,
            result_render=result_render,
            mapping_detail=mapping.detail or mapping.source.value,
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
            for src_off, off_off in ((delta, delta), (-delta, -delta), (delta, 0), (0, delta)):
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
