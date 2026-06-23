"""Fusion-based transform apply (prototype).

The Edit Inspector is affine-only and its per-frame keyframes rely on nudging the
playhead, which is fragile. A Fusion Transform node keyframes cleanly and is the
foundation for perspective (a CornerPin/Perspective node) later.

This module is split deliberately:
- the value math (Resolve transform -> Fusion Transform inputs, and the per-frame
  keyframe spec) is pure and unit-tested here;
- the actual Fusion graph construction (`FusionTransformApplier`) only runs inside
  DaVinci Resolve and is marked PROTOTYPE — validate it there. Sign conventions
  for Center.Y / Angle are best-effort and may need flipping after a Resolve test.

See docs/fusion-apply-plan.md for the design and validation checklist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from matchref.config import AppConfig
from matchref.edit_match_mode import compose_result_with_baseline
from matchref.models import ClipAnalysisResult
from matchref.transform_convert import baseline_from_result, resolve_transform


@dataclass(frozen=True)
class FusionTransform:
    """Inputs for a Fusion Transform node (Center normalized, Size = scale)."""

    center_x: float
    center_y: float
    size: float
    angle: float


def fusion_transform_values(
    resolved: Any, timeline_size: tuple[int, int]
) -> FusionTransform:
    """Convert a resolved Edit transform (px Pan/Tilt, Zoom) to Fusion inputs.

    Fusion Center is normalized 0..1 with 0.5 = frame centre and Y pointing up;
    Size is the scale factor (1.0 = 100%); Angle is in degrees.
    """
    width = max(1, int(timeline_size[0]))
    height = max(1, int(timeline_size[1]))
    center_x = 0.5 + float(resolved.edit_pan) / width
    # Fusion Y is bottom-up; Edit Tilt is already in Resolve's convention. Positive
    # tilt raises the image, so add. (Confirm direction in Resolve — see plan doc.)
    center_y = 0.5 + float(resolved.edit_tilt) / height
    return FusionTransform(
        center_x=center_x,
        center_y=center_y,
        size=float(resolved.edit_zoom_x),
        angle=float(resolved.edit_rotation),
    )


def build_keyframe_spec(
    result: ClipAnalysisResult,
    config: AppConfig,
    timeline_size: tuple[int, int],
) -> list[tuple[int, FusionTransform]]:
    """(clip-local frame, Fusion inputs) per ok sample, ordered by frame.

    This is the animation MatchRef would write as a Fusion Transform keyframe ramp.
    """
    baseline = baseline_from_result(result)
    compose_bl = baseline if compose_result_with_baseline(config) else None
    ok = sorted(
        (s for s in result.samples if s.ok), key=lambda s: s.clip_local_frame
    )
    spec: list[tuple[int, FusionTransform]] = []
    for sample in ok:
        resolved = resolve_transform(sample, config, timeline_size, baseline=compose_bl)
        spec.append((sample.clip_local_frame, fusion_transform_values(resolved, timeline_size)))
    return spec


def should_use_fusion(result: ClipAnalysisResult, config: AppConfig) -> bool:
    """Use the Fusion backend for animated (keyframed) clips when enabled."""
    if not bool(config.get("apply_via_fusion", False)):
        return False
    return bool(result.animated) and sum(1 for s in result.samples if s.ok) >= 2


class FusionTransformApplier:
    """PROTOTYPE — runs only inside DaVinci Resolve.

    Builds a Fusion comp on the clip with a single Transform node and keyframes its
    Center/Size/Angle across the sample frames. The value math is delegated to the
    pure helpers above (which are tested); everything here touches the live Fusion
    API and must be validated in Resolve.
    """

    def __init__(
        self,
        config: AppConfig,
        timeline_size: tuple[int, int],
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.timeline_size = timeline_size
        self.logger = logger or logging.getLogger("matchref")

    def apply_animated(self, item: Any, result: ClipAnalysisResult) -> None:
        spec = build_keyframe_spec(result, self.config, self.timeline_size)
        if len(spec) < 2:
            raise RuntimeError("Fusion keyframe apply needs >= 2 ok samples.")

        comp = self._fusion_comp(item)
        if comp is None:
            raise RuntimeError("Could not obtain a Fusion comp for the clip.")
        node = self._add_transform(comp)
        if node is None:
            raise RuntimeError("Could not create a Fusion Transform node.")

        clip_start = 0
        try:
            clip_start = int(item.GetStart())
        except Exception:
            pass

        for local_frame, xf in spec:
            frame = clip_start + int(local_frame)
            self._key(node, "Center", frame, (xf.center_x, xf.center_y))
            self._key(node, "Size", frame, xf.size)
            self._key(node, "Angle", frame, xf.angle)
        self.logger.info(
            "Fusion Transform keyframed: %s — %d keys", result.clip_name, len(spec)
        )

    # --- live-Resolve plumbing (PROTOTYPE) ----------------------------------

    def _fusion_comp(self, item: Any) -> Any:
        try:
            count = int(item.GetFusionCompCount())
        except Exception:
            count = 0
        try:
            if count > 0:
                return item.GetFusionCompByIndex(1)
            return item.AddFusionComp()
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Fusion comp unavailable: %s", exc)
            return None

    def _add_transform(self, comp: Any) -> Any:
        try:
            return comp.AddTool("Transform")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Fusion AddTool(Transform) failed: %s", exc)
            return None

    def _key(self, node: Any, attr: str, frame: int, value: Any) -> None:
        # Resolve/Fusion keyframing varies by build; the prototype writes the value
        # at the playhead-equivalent frame. Real keyframe spline insertion is the
        # main thing to validate/iterate on in Resolve.
        try:
            node.SetInput(attr, value, frame)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("SetInput(%s) at %d failed: %s", attr, frame, exc)
