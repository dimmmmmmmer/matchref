"""Perspective apply via a Fusion CornerPin node (prototype).

The Edit page is affine-only, so a perspective match is applied as a Fusion
CornerPin. The homography → corner math lives in perspective.py (unit-tested);
the node creation here is PROTOTYPE — validate it in DaVinci Resolve. Gated by
``perspective_match_enabled`` (default off).
"""

from __future__ import annotations

import logging
from typing import Any

from matchref.config import AppConfig
from matchref.models import ClipAnalysisResult, TransformSample
from matchref.perspective import CornerPin, cornerpin_from_warp


def _best_with_homography(result: ClipAnalysisResult) -> TransformSample | None:
    ok = [s for s in result.samples if s.ok and s.homography is not None]
    if not ok:
        return None
    return max(ok, key=lambda s: s.ecc_score)


def should_use_cornerpin(result: ClipAnalysisResult, config: AppConfig) -> bool:
    """Apply via CornerPin when perspective is enabled and a homography was solved."""
    if not bool(config.get("perspective_match_enabled", False)):
        return False
    return _best_with_homography(result) is not None


def cornerpin_for_result(
    result: ClipAnalysisResult, timeline_size: tuple[int, int]
) -> CornerPin | None:
    """Normalized CornerPin inputs for the best sample that has a homography."""
    sample = _best_with_homography(result)
    if sample is None:
        return None
    return cornerpin_from_warp(sample.homography, timeline_size)


class CornerPinApplier:
    """PROTOTYPE — runs only inside DaVinci Resolve.

    Adds a Fusion CornerPin to the clip and sets its four corners from the solved
    homography. The corner math is the tested perspective.py code; the node
    plumbing must be validated in Resolve.
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

    def apply(self, item: Any, result: ClipAnalysisResult) -> None:
        cp = cornerpin_for_result(result, self.timeline_size)
        if cp is None:
            raise RuntimeError("No homography to build a CornerPin from.")

        comp = self._fusion_comp(item)
        if comp is None:
            raise RuntimeError("Could not obtain a Fusion comp for the clip.")
        node = self._add_cornerpin(comp)
        if node is None:
            raise RuntimeError("Could not create a Fusion CornerPin node.")

        # CornerPin node inputs are normalized {x, y} points.
        for attr, point in (
            ("TopLeft", cp.top_left),
            ("TopRight", cp.top_right),
            ("BottomRight", cp.bottom_right),
            ("BottomLeft", cp.bottom_left),
        ):
            try:
                node.SetInput(attr, list(point))
            except Exception as exc:  # noqa: BLE001
                self.logger.debug("SetInput(%s) failed: %s", attr, exc)
        self.logger.info("Fusion CornerPin applied: %s", result.clip_name)

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

    def _add_cornerpin(self, comp: Any) -> Any:
        try:
            return comp.AddTool("CornerPositioner")
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("Fusion AddTool(CornerPositioner) failed: %s", exc)
            return None
