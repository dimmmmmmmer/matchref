"""Debug bundles for matched samples: comparison JPEGs + text manifest."""

from __future__ import annotations

from typing import Any

import numpy as np

from matchref.analysis_context import AnalyzerCore, _ClipContext, _SampleFrames
from matchref.clip_edit_transform import apply_clip_edit_to_frame
from matchref.debug_frames import DebugImages, save_match_debug


class _SampleDebugMixin(AnalyzerCore):
    """Comparison JPEGs + manifest for one matched sample (debug_save_frames)."""

    def _render_applied_result(
        self, sample: Any, online_raw: np.ndarray, offline: np.ndarray, extra: list[str]
    ) -> np.ndarray | None:
        """Render the Inspector-applied result for the debug stack; appends its stat lines."""
        try:
            # apply_clip_edit_to_frame is already imported at module level.
            from matchref.clip_edit_transform import ClipEditTransform
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
    ) -> None:
        if self._debug_dir is None:
            return
        sample = loaded.sample
        if sample.ok:
            via = sample.accept_via or "score"
            match_line = (
                f"match: OK [{via}] scale={sample.scale:.4f} rot={sample.rotation_deg:.2f} "
                f"ecc={sample.ecc_score:.4f} (apply min {loaded.apply_floor:.2f}) — {sample.message}"
            )
        else:
            match_line = (
                f"match: FAIL ecc={alignment.ecc_score:.4f} "
                f"(admit {loaded.admit:.2f}, apply {loaded.apply_floor:.2f}) — {sample.message}"
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
            images=DebugImages(
                online_for_match=loaded.online,
                offline_ref=loaded.offline,
                online_raw=loaded.online_raw,
                result_render=result_render,
            ),
            mapping_detail=loaded.mapping.detail or loaded.mapping.source.value,
            extra_lines=extra,
        )
        self.logger.info("  debug: %s", image_path)
