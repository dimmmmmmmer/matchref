"""Exclude offline burn-ins (timecode, slate text) from alignment."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from matchref.config import AppConfig


@dataclass(frozen=True)
class ContentCrop:
    """Pixel ROI inside a frame (exclusive of overlay margins)."""

    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return max(1, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(1, self.y1 - self.y0)


def overlay_margins_fraction(config: AppConfig) -> tuple[float, float, float, float]:
    """
    Fractional margins (top, right, bottom, left) to ignore for matching.

    Defaults bias slightly larger bottom margin for timecode overlays.
    """
    if not bool(config.get("match_ignore_overlay", True)):
        return 0.0, 0.0, 0.0, 0.0

    custom = config.get("match_overlay_margins")
    if isinstance(custom, (list, tuple)) and len(custom) == 4:
        return (float(custom[0]), float(custom[1]), float(custom[2]), float(custom[3]))

    uniform = float(config.get("match_crop_margin_ratio", 0.08))
    bottom = float(config.get("match_overlay_margin_bottom", uniform * 1.5))
    return uniform, uniform, min(bottom, 0.25), uniform


def content_crop_for_shape(
    height: int,
    width: int,
    margins: tuple[float, float, float, float],
) -> ContentCrop:
    top, right, bottom, left = margins
    x0 = int(width * left)
    x1 = int(width * (1.0 - right))
    y0 = int(height * top)
    y1 = int(height * (1.0 - bottom))
    if x1 - x0 < 32:
        x0, x1 = 0, width
    if y1 - y0 < 32:
        y0, y1 = 0, height
    return ContentCrop(x0=x0, y0=y0, x1=x1, y1=y1)


def ecc_match_mask(shape: tuple[int, ...], crop: ContentCrop) -> np.ndarray:
    """255 = use pixel in ECC template (offline), 0 = ignore overlays at edges."""
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    mask[crop.y0 : crop.y1, crop.x0 : crop.x1] = 255
    return mask
