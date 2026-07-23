"""Clip analysis pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from matchref.alignment import (
    transforms_are_stable,
)
from matchref.analysis_context import _ClipContext
from matchref.analysis_sampling import _SamplingMixin
from matchref.clip_edit_transform import (
    read_clip_edit_transform,
)
from matchref.clip_filter import filter_target_clips
from matchref.config import AppConfig
from matchref.conform_index import ConformIndex, OfflineFrameResolver
from matchref.debug_frames import (
    resolve_debug_dir,
)
from matchref.edit_match_mode import is_absolute_edit_match
from matchref.fps_check import validate_against_timeline
from matchref.frame_provider import FrameProvider, resolve_media_path, sample_points_for_clip
from matchref.lock_cut_align import (
    clip_hub_start,
    detect_lock_cut_hub_origin,
    reference_coverage_warning,
    timeline_end_frame,
)
from matchref.match_quality import (
    alignment_scale_bounds,
    assess_clip_match_quality,
    clip_motion_profile,
    ecc_consensus_passes,
    max_scale_spread,
    scale_spread,
)
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample
from matchref.timeline_context import TimelineContext


class TransformAnalyzer(_SamplingMixin):
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
        min_scale, max_scale = alignment_scale_bounds(float(best.ecc_score), self.config)
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
        """Keep the single best-matching sample; no keyframing unless it's a real ramp."""
        # Only the best sample's scale is validated — the others may be weaker
        # frames (motion blur, occlusion); we just don't use them.
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
