"""Apply transforms via Edit page Inspector (TimelineItem SetProperty)."""

from __future__ import annotations

import logging
import statistics
from typing import Any

from matchref.config import AppConfig
from matchref.edit_match_mode import compose_result_with_baseline
from matchref.models import AnalysisReport, ClipAnalysisResult, SamplePoint, TransformSample
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

    def apply_report(self, report: AnalysisReport) -> list[str]:
        if self.config.get("dry_run", False):
            self.logger.info("Dry-run: Edit Inspector transforms not written.")
            return ["dry-run"]

        messages: list[str] = []
        for result in report.ready_clips:
            try:
                self.apply_clip(result)
                messages.append(f"Applied (Edit): {result.clip_name}")
            except Exception as exc:  # noqa: BLE001
                msg = f"Failed {result.clip_name}: {exc}"
                self.logger.error(msg)
                messages.append(msg)
        return messages

    def apply_clip(self, result: ClipAnalysisResult) -> None:
        item = result.timeline_item
        if item is None:
            raise RuntimeError("Timeline item reference missing.")

        ok_samples = [s for s in result.samples if s.ok]
        if not ok_samples:
            raise RuntimeError("No successful samples.")

        if result.use_mid_key:
            mid_only = [s for s in ok_samples if s.sample_point == SamplePoint.MID]
            if mid_only:
                ok_samples = mid_only

        open_page = _callable(self.resolve, "OpenPage")
        if open_page is not None:
            try:
                open_page("edit")
            except Exception:
                pass

        use_playhead = bool(self.config.get("edit_keyframe_via_playhead", True))
        clip_start = 0
        try:
            clip_start = int(item.GetStart())
        except Exception:
            pass

        self._enable_keyframe_mode()

        baseline = baseline_from_result(result)
        compose_bl = baseline if compose_result_with_baseline(self.config) else None
        sorted_samples = sorted(ok_samples, key=lambda s: s.clip_local_frame)

        # Keyframed reframe: write each sample's own transform at its frame so the
        # animation is reproduced, instead of one static value.
        if result.animated and use_playhead and len(sorted_samples) >= 2:
            self._apply_keyframes(item, sorted_samples, clip_start, compose_bl, result.clip_name)
            return

        select = str(self.config.get("apply_transform_select", "best")).lower()
        if bool(self.config.get("apply_median_transform", False)) and len(sorted_samples) >= 2:
            select = "median"  # back-compat

        if select == "best":
            # Reframe is static across the clip, so apply the single sample that
            # matched best (highest score) rather than averaging — the median would
            # be dragged toward a weak frame (motion blur, flame flicker, occlusion).
            best = max(ok_samples, key=lambda s: s.ecc_score)
            last_resolved = resolve_transform(best, self.config, self._size, baseline=compose_bl)
            sorted_samples = [best]
            self.logger.info(
                "Apply select: best of %d sample(s) — %s (score %.4f)",
                len(ok_samples),
                best.sample_point.value,
                best.ecc_score,
            )
        elif select == "median" and len(sorted_samples) >= 2:
            last_resolved = self._median_resolved(sorted_samples, compose_bl)
        else:
            last_resolved = resolve_transform(
                sorted_samples[-1], self.config, self._size, baseline=compose_bl
            )

        if not self._resolved_values_sane(last_resolved):
            raise RuntimeError(
                f"Refusing to apply insane transform (zoom={last_resolved.edit_zoom_x:.3f}, "
                f"pan={last_resolved.edit_pan:.1f}, tilt={last_resolved.edit_tilt:.1f})"
            )

        for sample in sorted_samples:
            if use_playhead:
                self._seek(clip_start + sample.clip_local_frame)
            self._set_edit_properties(item, last_resolved)

        if last_resolved is None:
            raise RuntimeError("No resolved transform.")
        self.logger.info(
            "Edit Inspector: %s — %d point(s) Zoom=%.4f Pan=%.1f Tilt=%.1f Rot=%.1f",
            result.clip_name,
            len(ok_samples),
            last_resolved.edit_zoom_x,
            last_resolved.edit_pan,
            last_resolved.edit_tilt,
            last_resolved.edit_rotation,
        )

    def _apply_keyframes(
        self,
        item: Any,
        sorted_samples: list[TransformSample],
        clip_start: int,
        compose_bl: Any | None,
        clip_name: str,
    ) -> None:
        """Set each sample's own resolved transform at its frame (keyframe ramp)."""
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
            self._seek(clip_start + sample.clip_local_frame)
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

    def _enable_keyframe_mode(self) -> None:
        setter = _callable(self.resolve, "SetKeyframeMode")
        if setter is None:
            return
        try:
            setter(2)
        except Exception:
            try:
                setter(0)
            except Exception:
                pass

    def _seek(self, timeline_frame: int) -> None:
        timeline = self._timeline_obj()
        setter = _callable(timeline, "SetCurrentTimecode")
        if setter is None:
            return
        from matchref.timecode import TimecodeFormat, format_timecode

        fmt = TimecodeFormat(
            fps=self.timeline_ctx.fps,
            drop_frame=self.timeline_ctx.drop_frame,
        )
        tc = format_timecode(max(0, int(timeline_frame)), fmt)
        try:
            setter(tc)
        except Exception:
            pass

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

        for key, value in props.items():
            if not setter(key, value):
                self.logger.warning("SetProperty(%s) returned False", key)
