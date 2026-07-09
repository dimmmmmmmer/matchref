"""Grade-invariant reference metrics (intensity/gradient NCC) for the refine search."""

from __future__ import annotations

import cv2
import numpy as np

from matchref.config import AppConfig
from matchref.overlay_crop import content_crop_for_shape, overlay_margins_fraction


def _content_gray(image: np.ndarray, config: AppConfig) -> np.ndarray:
    margins = overlay_margins_fraction(config)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    crop = content_crop_for_shape(gray.shape[0], gray.shape[1], margins)
    return gray[crop.y0 : crop.y1, crop.x0 : crop.x1]


def _auto_highlight_clip(reference_gray: np.ndarray, config: AppConfig) -> float:
    """
    Clip level for highlight-dominated dark shots (fire/specular flicker).

    Such shots have a small, very bright, high-variance region on a dark base that
    hijacks the correlation and pulls position off the static structure. Clipping
    highlights lets the rigid dark structure drive the match. Returns 0 (no clip)
    for normal/bright scenes. ``match_highlight_clip``: "auto" | 0/off | <number>.
    """
    setting = config.get("match_highlight_clip", "auto")
    if isinstance(setting, (int, float)):
        return float(setting)
    if str(setting).lower() in ("off", "none", "0", ""):
        return 0.0
    # auto: only dark-dominant scenes, clip relative to the median brightness.
    nonzero = reference_gray[reference_gray > 1.0]
    if nonzero.size == 0:
        return 0.0
    median = float(np.median(nonzero))
    dark_threshold = float(config.get("highlight_clip_dark_median", 48.0))
    if median >= dark_threshold:
        return 0.0
    clip = median * float(config.get("highlight_clip_median_mult", 5.0))
    return clip if clip < 255.0 else 0.0


def _gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude — invariant to grade/exposure, sharp at edges."""
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def _zero_mean_norm(arr: np.ndarray) -> tuple[np.ndarray, float]:
    centered = arr - float(arr.mean())
    return centered, float(np.linalg.norm(centered))


def _ncc_against(feature: np.ndarray, ref_centered: np.ndarray, ref_norm: float) -> float:
    a, a_norm = _zero_mean_norm(feature)
    denom = a_norm * ref_norm
    if denom < 1e-6:
        return 0.0
    return float(np.clip(float(np.sum(a * ref_centered)) / denom, -1.0, 1.0))


class _Reference:
    """
    Pre-computed offline features so each cost evaluation only processes the render.

    Holds both an intensity and a gradient-domain view of the (fixed) offline frame.
    Gradient NCC is robust to the grade difference between raw online media and the
    colour-graded lock-cut, and falls off sharply with misalignment (precise fit);
    intensity NCC keeps ungraded clips scoring high. ``score`` blends them per
    ``refine_score_metric``; ``cost`` drives the search per ``refine_cost_metric``.
    """

    def __init__(self, reference: np.ndarray, config: AppConfig) -> None:
        self._config = config
        gray = _content_gray(reference, config)
        # Clip derived once from the (stable, full-frame) offline reference and
        # applied identically to every rendered online frame, so both sides drop
        # the same flicker highlights.
        self._clip = _auto_highlight_clip(gray, config)
        gray = self._clipped(gray)
        self._intensity = _zero_mean_norm(gray)
        self._gradient = _zero_mean_norm(_gradient_magnitude(gray))

    def _clipped(self, gray: np.ndarray) -> np.ndarray:
        if self._clip > 0:
            return np.minimum(gray, self._clip)
        return gray

    def intensity_ncc(self, rendered: np.ndarray) -> float:
        return _ncc_against(self._clipped(_content_gray(rendered, self._config)), *self._intensity)

    def gradient_ncc(self, rendered: np.ndarray) -> float:
        feat = _gradient_magnitude(self._clipped(_content_gray(rendered, self._config)))
        return _ncc_against(feat, *self._gradient)

    def score(self, rendered: np.ndarray) -> float:
        metric = str(self._config.get("refine_score_metric", "max")).lower()
        if metric == "intensity":
            return self.intensity_ncc(rendered)
        if metric == "gradient":
            return self.gradient_ncc(rendered)
        return max(self.intensity_ncc(rendered), self.gradient_ncc(rendered))

    def cost(self, rendered: np.ndarray) -> float:
        metric = str(self._config.get("refine_cost_metric", "blend")).lower()
        if metric == "intensity":
            return 1.0 - self.intensity_ncc(rendered)
        if metric == "blend":
            # Weight toward the gradient term: its sharp peak fixes zoom precisely,
            # while a little intensity keeps pan/tilt smooth and survives non-rigid
            # content (fire/smoke) where gradient alone is unreliable.
            wg = float(self._config.get("refine_blend_gradient_weight", 0.5))
            wg = min(1.0, max(0.0, wg))
            return 1.0 - (
                wg * self.gradient_ncc(rendered) + (1.0 - wg) * self.intensity_ncc(rendered)
            )
        return 1.0 - self.gradient_ncc(rendered)
