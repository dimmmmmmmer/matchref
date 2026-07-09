"""Apply transforms via Edit page Inspector (TimelineItem SetProperty)."""

from __future__ import annotations

import logging
import statistics
from typing import Any

from matchref.config import AppConfig
from matchref.edit_match_mode import compose_result_with_baseline
from matchref.models import ClipAnalysisResult, SamplePoint, TransformSample
from matchref.timeline_context import TimelineContext
from matchref.transform_convert import baseline_from_result, resolve_transform


def _callable(obj: Any, name: str) -> Any | None:
    fn = getattr(obj, name, None)
    return fn if callable(fn) else None


class EditTransformApplier:
    """
    Write Pan / Tilt / ZoomX / ZoomY / RotationAngle on timeline clips.

    Note: Resolve API does not document per-frame keyframes for these properties.
    We set values at each sample frame by moving the playhead (works on many builds).
    """

    def __init__(
        self,
        resolve: Any,
        config: AppConfig,
        timeline_ctx: TimelineContext,
        logger: logging.Logger | None = None,
    ) -> None:
        self.resolve = resolve
        self.config = config
        self.timeline_ctx = timeline_ctx
        self.logger = logger or logging.getLogger("matchref")
        self._timeline = None
        self._size = timeline_ctx.resolution

    def _timeline_obj(self) -> Any:
        if self._timeline is None:
            pm = _callable(self.resolve, "GetProjectManager")
            if pm is None:
                raise RuntimeError("GetProjectManager unavailable.")
            project = pm().GetCurrentProject()
            self._timeline = project.GetCurrentTimeline()
        return self._timeline

    def apply_clip(self, result: ClipAnalysisResult) -> None:
        item = result.timeline_item
        if item is None:
            raise RuntimeError("Timeline item reference missing.")

        ok_samples = [s for s in result.samples if s.ok]
        if not ok_samples:
            raise RuntimeError("No successful samples.")
        ok_samples = self._prefer_mid_samples(result, ok_samples)

        self._open_edit_page()

        use_playhead = bool(self.config.get("edit_keyframe_via_playhead", True))
        clip_start = self._clip_start(item)

        if self._apply_via_alternate_backend(item, result):
            return

        kf_enabled = self._enable_keyframe_mode()

        baseline = baseline_from_result(result)
        compose_bl = baseline if compose_result_with_baseline(self.config) else None
        sorted_samples = sorted(ok_samples, key=lambda s: s.clip_local_frame)

        # Keyframed reframe: write each sample's own transform at its frame so the
        # animation is reproduced, instead of one static value.
        if result.animated and use_playhead and len(sorted_samples) >= 2:
            self._apply_keyframes(
                item, sorted_samples, clip_start, compose_bl, result.clip_name, kf_enabled
            )
            return

        last_resolved, write_samples = self._select_static_transform(
            ok_samples, sorted_samples, compose_bl
        )
        if not self._resolved_values_sane(last_resolved):
            raise RuntimeError(
                f"Refusing to apply insane transform (zoom={last_resolved.edit_zoom_x:.3f}, "
                f"pan={last_resolved.edit_pan:.1f}, tilt={last_resolved.edit_tilt:.1f})"
            )

        for sample in write_samples:
            if use_playhead:
                self._seek(clip_start + sample.clip_local_frame)
            self._set_edit_properties(item, last_resolved)

        self.logger.info(
            "Edit Inspector: %s — %d point(s) Zoom=%.4f Pan=%.1f Tilt=%.1f Rot=%.1f",
            result.clip_name,
            len(ok_samples),
            last_resolved.edit_zoom_x,
            last_resolved.edit_pan,
            last_resolved.edit_tilt,
            last_resolved.edit_rotation,
        )

    @staticmethod
    def _prefer_mid_samples(
        result: ClipAnalysisResult, ok_samples: list[TransformSample]
    ) -> list[TransformSample]:
        """MID-only samples when the clip decision asked for the midpoint keyframe."""
        if not result.use_mid_key:
            return ok_samples
        mid_only = [s for s in ok_samples if s.sample_point == SamplePoint.MID]
        return mid_only or ok_samples

    @staticmethod
    def _clip_start(item: Any) -> int:
        try:
            return int(item.GetStart())
        except Exception:
            return 0

    def _open_edit_page(self) -> None:
        open_page = _callable(self.resolve, "OpenPage")
        if open_page is not None:
            try:
                open_page("edit")
            except Exception:
                pass

    def _apply_via_alternate_backend(self, item: Any, result: ClipAnalysisResult) -> bool:
        """Perspective and Fusion backends; True when one of them handled the clip."""
        # Perspective match is applied as a Fusion CornerPin (Edit can't do it) when
        # perspective_match_enabled and a homography was solved.
        from matchref.cornerpin_apply import CornerPinApplier, should_use_cornerpin

        if should_use_cornerpin(result, self.config):
            CornerPinApplier(self.config, self._size, self.logger).apply(item, result)
            return True

        # Animated clips can be keyframed via a Fusion Transform node (cleaner than
        # the Edit-page playhead keyframes) when apply_via_fusion is enabled.
        from matchref.fusion_apply import FusionTransformApplier, should_use_fusion

        if should_use_fusion(result, self.config):
            FusionTransformApplier(self.config, self._size, self.logger).apply_animated(
                item, result
            )
            return True
        return False

    def _select_static_transform(
        self,
        ok_samples: list[TransformSample],
        sorted_samples: list[TransformSample],
        compose_bl: Any | None,
    ) -> tuple[Any, list[TransformSample]]:
        """Pick the transform to write and the samples to write it at."""
        select = str(self.config.get("apply_transform_select", "best")).lower()
        if bool(self.config.get("apply_median_transform", False)) and len(sorted_samples) >= 2:
            select = "median"  # back-compat

        if select == "best":
            # Reframe is static across the clip, so apply the single sample that
            # matched best (highest score) rather than averaging — the median would
            # be dragged toward a weak frame (motion blur, flame flicker, occlusion).
            best = max(ok_samples, key=lambda s: s.ecc_score)
            resolved = resolve_transform(best, self.config, self._size, baseline=compose_bl)
            self.logger.info(
                "Apply select: best of %d sample(s) — %s (score %.4f)",
                len(ok_samples),
                best.sample_point.value,
                best.ecc_score,
            )
            return resolved, [best]
        if select == "median" and len(sorted_samples) >= 2:
            return self._median_resolved(sorted_samples, compose_bl), sorted_samples
        return (
            resolve_transform(sorted_samples[-1], self.config, self._size, baseline=compose_bl),
            sorted_samples,
        )

    def _apply_keyframes(
        self,
        item: Any,
        sorted_samples: list[TransformSample],
        clip_start: int,
        compose_bl: Any | None,
        clip_name: str,
        kf_enabled: bool,
    ) -> None:
        """Set each sample's own resolved transform at its frame (keyframe ramp)."""
        # Without keyframe mode the per-frame writes would collapse to a single
        # static value (last write wins) — a silently wrong ramp. Refuse instead so
        # the clip is flagged for manual keyframing.
        if not kf_enabled:
            raise RuntimeError(
                "Animated reframe needs keyframe mode, which Resolve did not accept on "
                "this build. Set apply_keyframes_on_variance=false to apply a single "
                "static transform, or keyframe this clip manually."
            )
        resolved = [
            (s, resolve_transform(s, self.config, self._size, baseline=compose_bl))
            for s in sorted_samples
        ]
        for _s, rt in resolved:
            if not self._resolved_values_sane(rt):
                raise RuntimeError(
                    f"Refusing to keyframe insane transform (zoom={rt.edit_zoom_x:.3f}, "
                    f"pan={rt.edit_pan:.1f}, tilt={rt.edit_tilt:.1f})"
                )
        for sample, rt in resolved:
            target = clip_start + sample.clip_local_frame
            if not self._seek(target):
                raise RuntimeError(
                    f"Playhead seek to frame {target} failed — refusing to write a "
                    "collapsed keyframe ramp."
                )
            self._set_edit_properties(item, rt)
        self.logger.info(
            "Edit Inspector (keyframed): %s — %d keyframes [%s]",
            clip_name,
            len(resolved),
            ", ".join(
                f"{s.sample_point.value}@{s.clip_local_frame} z={rt.edit_zoom_x:.3f} "
                f"pan={rt.edit_pan:.0f} tilt={rt.edit_tilt:.0f}"
                for s, rt in resolved
            ),
        )

    def _median_resolved(self, samples: list[TransformSample], baseline: Any | None) -> Any:
        resolved = [
            resolve_transform(sample, self.config, self._size, baseline=baseline)
            for sample in samples
        ]
        from matchref.transform_convert import ResolvedTransform

        return ResolvedTransform(
            scale=statistics.median([r.scale for r in resolved]),
            rotation_deg=statistics.median([r.rotation_deg for r in resolved]),
            edit_zoom_x=statistics.median([r.edit_zoom_x for r in resolved]),
            edit_zoom_y=statistics.median([r.edit_zoom_y for r in resolved]),
            edit_pan=statistics.median([r.edit_pan for r in resolved]),
            edit_tilt=statistics.median([r.edit_tilt for r in resolved]),
            edit_rotation=statistics.median([r.edit_rotation for r in resolved]),
        )

    def _resolved_values_sane(self, resolved: Any) -> bool:
        width, height = self._size
        limit = max(width, height) * 2.0
        if resolved.edit_zoom_x > 16.0 or resolved.edit_zoom_x < 0.05:
            return False
        if abs(resolved.edit_pan) > limit or abs(resolved.edit_tilt) > limit:
            return False
        return True

    def _enable_keyframe_mode(self) -> bool:
        """Enable keyframe mode; return whether Resolve accepted it on this build."""
        setter = _callable(self.resolve, "SetKeyframeMode")
        if setter is None:
            return False
        for mode in (2, 0):
            try:
                setter(mode)
                return True
            except Exception:
                continue
        return False

    def _seek(self, timeline_frame: int) -> bool:
        """Move the playhead; return whether the move was confirmed (best-effort).

        Returns False when the seek call is unavailable/raises, or when the
        timeline timecode can be read back and clearly did not reach the target.
        When it cannot be verified (no GetCurrentTimecode) it trusts the setter.
        """
        timeline = self._timeline_obj()
        setter = _callable(timeline, "SetCurrentTimecode")
        if setter is None:
            return False
        from matchref.timecode import TimecodeFormat, format_timecode, parse_timecode

        fmt = TimecodeFormat(
            fps=self.timeline_ctx.fps,
            drop_frame=self.timeline_ctx.drop_frame,
        )
        target = max(0, int(timeline_frame))
        tc = format_timecode(target, fmt)
        try:
            setter(tc)
        except Exception:
            return False
        getter = _callable(timeline, "GetCurrentTimecode")
        if getter is None:
            return True  # can't verify on this build — trust the setter
        try:
            actual = parse_timecode(str(getter()), fmt)
        except (ValueError, TypeError):
            return True
        return abs(actual - target) <= 1

    # Tolerance (in the property's own units) when verifying a written value was
    # accepted by Resolve. Generous enough to absorb internal re-quantisation.
    _READBACK_TOL = {
        "ZoomX": 0.01,
        "ZoomY": 0.01,
        "Pan": 1.0,
        "Tilt": 1.0,
        "RotationAngle": 0.1,
    }

    def _set_edit_properties(self, item: Any, resolved: Any) -> None:
        setter = _callable(item, "SetProperty")
        if setter is None:
            raise RuntimeError("TimelineItem.SetProperty is not available.")

        props: dict[str, float | bool] = {}
        if self.config.get("match_scale", True):
            props["ZoomX"] = float(resolved.edit_zoom_x)
            props["ZoomY"] = float(resolved.edit_zoom_y)
            props["ZoomGang"] = True
        if self.config.get("match_position", True):
            props["Pan"] = float(resolved.edit_pan)
            props["Tilt"] = float(resolved.edit_tilt)
        if self.config.get("match_rotation", True):
            props["RotationAngle"] = float(resolved.edit_rotation)

        # A half-written transform (e.g. Zoom set, Pan rejected) is worse than no
        # write at all — it silently mis-places the clip while reporting success.
        # Treat any SetProperty failure as a hard error so the clip is flagged.
        failed = [key for key, value in props.items() if not setter(key, value)]
        if failed:
            raise RuntimeError(
                f"SetProperty rejected: {', '.join(failed)} (clip left partially transformed)"
            )

        if bool(self.config.get("verify_apply_readback", True)):
            self._verify_written(item, props)

    def _verify_written(self, item: Any, props: dict[str, float | bool]) -> None:
        """Read back numeric properties and confirm Resolve accepted the values."""
        getter = _callable(item, "GetProperty")
        if getter is None:
            self.logger.debug("GetProperty unavailable — skipping apply read-back.")
            return
        mismatches: list[str] = []
        for key, value in props.items():
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            tol = self._READBACK_TOL.get(key, 0.01)
            try:
                actual = float(getter(key))
            except (TypeError, ValueError):
                # Property not readable as a number on this build — don't block.
                continue
            if abs(actual - float(value)) > tol:
                mismatches.append(f"{key}: wrote {float(value):.4f}, read {actual:.4f}")
        if mismatches:
            raise RuntimeError(
                "Apply not confirmed (Resolve value differs after write): " + "; ".join(mismatches)
            )
