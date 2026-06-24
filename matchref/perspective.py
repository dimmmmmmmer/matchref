"""Perspective match geometry (estimation foundation).

The Edit page cannot represent perspective; it is applied via a Fusion CornerPin
node (see docs/fusion-apply-plan.md). This module holds the pure, testable math:
solve a homography (reusing the ECC homography path) and turn it into the four
corner positions a CornerPin needs. The cv2 solve wraps existing alignment; the
geometry below is unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from matchref.config import AppConfig


@dataclass(frozen=True)
class CornerPin:
    """Fusion CornerPin inputs — normalized (0..1, Y up), one point per corner."""

    top_left: tuple[float, float]
    top_right: tuple[float, float]
    bottom_right: tuple[float, float]
    bottom_left: tuple[float, float]


def homography_corners(
    warp: np.ndarray, width: int, height: int
) -> list[tuple[float, float]]:
    """Map the four frame corners (TL, TR, BR, BL) through a 3x3 homography.

    Returns pixel coordinates. A 2x3 affine is accepted and treated as the top two
    rows of a homography (perspective row [0, 0, 1]).
    """
    matrix = np.asarray(warp, dtype=np.float64)
    if matrix.shape == (2, 3):
        matrix = np.vstack([matrix, [0.0, 0.0, 1.0]])
    if matrix.shape != (3, 3):
        raise ValueError("warp must be 2x3 or 3x3")

    w = float(width)
    h = float(height)
    corners = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    out: list[tuple[float, float]] = []
    for x, y in corners:
        vec = matrix @ np.array([x, y, 1.0])
        denom = vec[2] if abs(vec[2]) > 1e-12 else 1e-12
        out.append((float(vec[0] / denom), float(vec[1] / denom)))
    return out


def corners_to_cornerpin(
    corners: list[tuple[float, float]], width: int, height: int
) -> CornerPin:
    """Pixel corners (TL, TR, BR, BL) → normalized Fusion CornerPin (Y up)."""
    if len(corners) != 4:
        raise ValueError("expected 4 corners")
    w = max(1.0, float(width))
    h = max(1.0, float(height))

    def norm(p: tuple[float, float]) -> tuple[float, float]:
        return (p[0] / w, 1.0 - p[1] / h)

    return CornerPin(
        top_left=norm(corners[0]),
        top_right=norm(corners[1]),
        bottom_right=norm(corners[2]),
        bottom_left=norm(corners[3]),
    )


def rescale_homography(warp: np.ndarray, scale: float) -> np.ndarray:
    """Convert a homography solved at a downscaled size to full-size coordinates.

    If the solve ran on frames downscaled by ``scale`` (reduced = scale · full),
    the full-resolution homography is S⁻¹ · H · S with S = diag(scale, scale, 1).
    Unlike an affine, a homography's perspective row also needs this conjugation —
    rescaling only the translation column would be wrong.
    """
    matrix = np.asarray(warp, dtype=np.float64)
    if matrix.shape != (3, 3):
        raise ValueError("homography must be 3x3")
    s = float(scale) if scale else 1.0
    big = np.diag([s, s, 1.0])
    big_inv = np.diag([1.0 / s, 1.0 / s, 1.0])
    return big_inv @ matrix @ big


def cornerpin_from_warp(warp: np.ndarray, timeline_size: tuple[int, int]) -> CornerPin:
    """Homography (timeline pixels) → normalized Fusion CornerPin inputs."""
    width, height = int(timeline_size[0]), int(timeline_size[1])
    return corners_to_cornerpin(homography_corners(warp, width, height), width, height)


def solve_homography(
    online_bgr: Any,
    offline_bgr: Any,
    config: AppConfig,
    *,
    timeline_size: tuple[int, int],
) -> np.ndarray | None:
    """Best-effort ECC homography (online→offline), returned in timeline pixels.

    Solves on a downscaled pair for a smooth landscape, then rescales the
    homography back to the timeline raster. Returns None if ECC does not converge.
    cv2-bound — only the surrounding geometry (rescale/corners) is unit-tested.
    """
    import cv2

    from matchref.alignment import _match_size, prepare_gray_uint8

    tw = max(1, int(timeline_size[0]))
    cap = int(config.get("refine_max_width", 1920))
    max_w = min(cap, tw) if cap > 0 else tw
    use_clahe = bool(config.get("match_use_clahe", True))
    on = prepare_gray_uint8(online_bgr, max_w, use_clahe=use_clahe)
    off = prepare_gray_uint8(offline_bgr, max_w, use_clahe=use_clahe)
    on, off = _match_size(on, off)

    scale = on.shape[1] / float(tw)
    warp = np.eye(3, 3, dtype=np.float32)
    criteria = (
        cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
        int(config.get("ecc_iterations", 8000)),
        float(config.get("ecc_epsilon", 1e-8)),
    )
    try:
        _, warp = cv2.findTransformECC(  # type: ignore[call-overload]
            off, on, warp, cv2.MOTION_HOMOGRAPHY, criteria, None,
            int(config.get("ecc_gauss_filt_size", 5)),
        )
    except cv2.error:
        return None
    return rescale_homography(warp, scale)
