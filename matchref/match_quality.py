"""Reject weak or inconsistent alignment before applying Edit transforms."""

from __future__ import annotations

from matchref.alignment import transforms_are_stable
from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.models import TransformSample


def quality_threshold(config: AppConfig) -> float:
    return float(config.get("min_match_score", 0.95))


def refine_ncc_threshold(config: AppConfig) -> float:
    return float(config.get("auto_reframe_ncc_threshold", 0.95))


def refine_early_exit_enabled(config: AppConfig) -> bool:
    return bool(config.get("refine_early_exit_on_quality", True))


def sample_score_passes(score: float, config: AppConfig) -> bool:
    return float(score) >= quality_threshold(config)


def refine_ncc_passes(ncc: float, config: AppConfig) -> bool:
    return float(ncc) >= refine_ncc_threshold(config)


def refine_gradient_floor(config: AppConfig) -> float:
    """Min structural (gradient-domain) agreement to accept a refine result."""
    return float(config.get("refine_min_gradient_ncc", 0.4))


def refine_structure_passes(gradient_ncc: float, config: AppConfig) -> bool:
    """Reject intensity-only false matches (grade-similar but geometrically wrong)."""
    return float(gradient_ncc) >= refine_gradient_floor(config)


def min_ecc_for_refine_attempt(config: AppConfig) -> float:
    return float(config.get("min_ecc_for_refine_attempt", 0.70))


def score_from_refine_ncc(ncc: float) -> float:
    return float((float(ncc) + 1.0) * 0.5)


def max_refine_pan_tilt_pixels(timeline_size: tuple[int, int], config: AppConfig) -> float:
    explicit = float(config.get("max_refine_pan_tilt_pixels", 0.0))
    if explicit > 0:
        return explicit
    frac = float(config.get("max_refine_pan_tilt_fraction", 0.15))
    return max(timeline_size) * frac


def refine_edit_plausible(
    edit: ClipEditTransform,
    timeline_size: tuple[int, int],
    config: AppConfig,
) -> bool:
    limit = max_refine_pan_tilt_pixels(timeline_size, config)
    return abs(float(edit.pan)) <= limit and abs(float(edit.tilt)) <= limit


def assess_clip_match_quality(
    ok_samples: list[TransformSample],
    config: AppConfig,
) -> tuple[bool, list[str]]:
    """Return (passed, reasons). Failed clips must not be applied."""
    if not ok_samples:
        return False, ["no successful samples"]

    reasons: list[str] = []
    min_score = quality_threshold(config)
    scores = [float(s.ecc_score) for s in ok_samples]
    if min(scores) < min_score:
        reasons.append(f"min match score {min(scores):.4f} < {min_score:.2f}")

    scales = [float(s.scale) for s in ok_samples]
    min_scale = float(config.get("min_alignment_scale", 0.4))
    max_scale = float(config.get("max_alignment_scale", 2.5))
    for sample in ok_samples:
        if sample.scale < min_scale or sample.scale > max_scale:
            reasons.append(
                f"{sample.sample_point.value}: scale {sample.scale:.3f} outside "
                f"[{min_scale}, {max_scale}]"
            )
            break

    if len(ok_samples) < 2:
        return len(reasons) == 0, reasons

    if len(scales) >= 2:
        lo, hi = min(scales), max(scales)
        if lo > 1e-6:
            spread = hi / lo
            max_spread = float(config.get("max_scale_spread_ratio", 1.35))
            if spread > max_spread:
                reasons.append(f"scale varies {lo:.3f}…{hi:.3f} (×{spread:.2f})")

    if len(ok_samples) >= 2:
        pos_threshold = float(config.get("transform_variance_threshold", 0.02))
        pos_vectors = [
            (s.position_x, s.position_y, s.rotation_deg, 0.0) for s in ok_samples
        ]
        if not transforms_are_stable(pos_vectors, pos_threshold):
            reasons.append(
                f"pan/tilt/rot unstable across samples (threshold {pos_threshold})"
            )

    return len(reasons) == 0, reasons
