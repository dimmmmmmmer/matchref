"""Per-sample matching stage: load frames, align, refine, record (+ debug bundles)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

from matchref.alignment import (
    align_frames,
    alignment_admission_threshold,
    apply_match_flags,
    warp_analysis_to_timeline,
)
from matchref.analysis_context import _ClipContext, _SampleFrames
from matchref.analysis_debug import _SampleDebugMixin
from matchref.clip_edit_transform import (
    _fit_frame_to_canvas,
    apply_clip_edit_to_frame,
    should_simulate_edit_for_match,
)
from matchref.clip_metadata import build_clip_source_info
from matchref.debug_frames import (
    SampleCompareInfo,
    format_sample_compare_line,
)
from matchref.edit_match_mode import is_absolute_edit_match, use_baseline_compensation_for_match
from matchref.logging_report import log_sample
from matchref.match_quality import (
    min_ecc_for_refine_attempt,
    refine_early_exit_enabled,
    sample_refine_accepted,
    sample_score_passes,
    score_from_refine_ncc,
)
from matchref.models import TransformSample
from matchref.precision_align import (
    initial_edit_from_warp,
    is_maximum_precision,
    refine_resolve_edit,
)


class _SamplingMixin(_SampleDebugMixin):
    """The per-sample pipeline: resolve mapping → decode → align → refine → record."""

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
            SampleCompareInfo(
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
        message: str,
        warning: str,
    ) -> TransformSample:
        """Append a rejected sample with its warning and debug bundle."""
        sample = loaded.sample
        sample.message = message
        ctx.result.samples.append(sample)
        ctx.result.warnings.append(f"{point.value}: {warning}")
        self._save_match_debug(ctx, point, loaded, alignment)
        return sample

    def _passes_admission(
        self,
        ctx: _ClipContext,
        point: Any,
        loaded: _SampleFrames,
        alignment: Any,
        can_refine: bool,
    ) -> bool:
        """Gate on the ECC admission score; a refine-worthy near-miss may proceed."""
        admit = loaded.admit
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
            message=f"score below admission ({alignment.ecc_score:.4f} < {admit:.2f})",
            warning=(
                f"ECC {alignment.ecc_score:.4f} < {admit:.2f} "
                f"(refine needs >= {min_refine_ecc:.2f}, apply {loaded.apply_floor:.2f})"
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
        loaded.first_refine = refine_outcome
        loaded.first_reasons = reasons
        if self._escalated_refine(ctx, point, loaded, alignment, warp, should_cancel):
            return None
        self._stash_ecc_candidate(ctx, loaded.sample, alignment)
        message = "refine rejected: " + "; ".join(reasons)
        return self._record_sample_reject(
            ctx, point, loaded, alignment, message=message, warning=message
        )

    def _escalated_refine(
        self,
        ctx: _ClipContext,
        point: Any,
        loaded: _SampleFrames,
        alignment: Any,
        warp: Any,
        should_cancel: Callable[[], bool] | None,
    ) -> bool:
        """Second-chance refine with forced position + widened limits; True when accepted."""
        from matchref.escalation import escalated_config, should_escalate

        first = loaded.first_refine
        if first is None or not should_escalate(
            loaded.first_reasons, first.used_position, first.ncc, self.config
        ):
            return False
        esc = escalated_config(self.config)
        canvas = self._timeline_canvas_size()
        baseline = None if loaded.absolute else ctx.baseline
        guess = initial_edit_from_warp(warp, canvas, esc, baseline)
        outcome = refine_resolve_edit(
            loaded.online_raw,
            loaded.offline,
            canvas_size=canvas,
            config=esc,
            initial=guess,
            warp=warp,
            should_cancel=should_cancel,
        )
        ok, esc_reasons, _via = sample_refine_accepted(
            ncc=outcome.ncc,
            gradient_ncc=outcome.gradient_ncc,
            edit=outcome.edit,
            ecc_scale=alignment.scale,
            timeline_size=canvas,
            config=esc,
        )
        if not ok:
            self.logger.info(
                "[%s %s] escalated refine also rejected: %s",
                ctx.result.clip_name,
                point.value,
                "; ".join(esc_reasons),
            )
            return False
        sample = loaded.sample
        sample.refined_edit = outcome.edit
        sample.ecc_score = score_from_refine_ncc(outcome.ncc)
        sample.accept_via = "escalated"
        self.logger.info(
            "[%s %s] escalated refine accepted: zoom=%.4f pan=%.1f tilt=%.1f ncc=%.4f "
            "(first attempt: %s)",
            ctx.result.clip_name,
            point.value,
            outcome.edit.zoom_x,
            outcome.edit.pan,
            outcome.edit.tilt,
            outcome.ncc,
            "; ".join(loaded.first_reasons),
        )
        return True

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
        loaded.admit = alignment_admission_threshold(alignment, self.config)
        loaded.apply_floor = float(self.config.get("min_match_score", 0.95))
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

        if not self._passes_admission(ctx, point, loaded, alignment, can_refine):
            return sample

        warp = self._timeline_warp(alignment)
        sample.warp_matrix = warp

        skip_refine = self._refine_skipped_by_score(ctx, point, alignment)
        if can_refine and warp is not None and not skip_refine:
            # can_refine already implies loaded.online_raw is not None
            rejected = self._refine_sample(ctx, point, loaded, alignment, warp, should_cancel)
            if rejected is not None:
                return rejected

        self._accept_sample(ctx, point, loaded, alignment)
        self._save_match_debug(ctx, point, loaded, alignment)
        return sample

    def _align_with_neighbors(
        self,
        online: np.ndarray,
        offline: np.ndarray,
        media_path: str,
        source_frame: int,
        mapping: Any,
    ) -> Any:
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
