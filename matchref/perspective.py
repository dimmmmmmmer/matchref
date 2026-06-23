"""Perspective match geometry (estimation foundation).

The Edit page cannot represent perspective; it is applied via a Fusion CornerPin
node (see docs/fusion-apply-plan.md). This module holds the pure, testable math:
solve a homography (reusing the ECC homography path) and turn it into the four
corner positions a CornerPin needs. The cv2 solve wraps existing alignment; the
geometry below is unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


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
