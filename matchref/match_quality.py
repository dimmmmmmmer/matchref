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


def scale_spread(scales: list[float]) -> tuple[float, float, float]:
    """(min, max, max/min) of a set of zoom estimates; ratio 1.0 if fewer than two."""
    vals = [float(s) for s in scales]
    if len(vals) < 2:
        v = vals[0] if vals else 1.0
        return v, v, 1.0
    lo, hi = min(vals), max(vals)
    return lo, hi, (hi / lo if lo > 1e-6 else 999.0)


def max_scale_spread(config: AppConfig) -> float:
    return float(config.get("max_scale_spread_ratio", 1.35))


def _monotonic_ramp(values: list[float], tol: float) -> bool:
    """True if values progress consistently (non-decreasing or non-increasing).

    A genuine keyframed reframe ramps smoothly across the clip; a single-frame fluke
    makes the middle sample an outlier. ``tol`` absorbs sub-threshold jitter.
    """
    if len(values) < 2:
        return True
    pairs = list(zip(values, values[1:], strict=False))
    inc = all(b >= a - tol for a, b in pairs)
    dec = all(b <= a + tol for a, b in pairs)
    return inc or dec


def clip_motion_profile(samples: list[TransformSample], config: AppConfig) -> tuple[bool, bool]:
    """
    Inspect time-ordered accepted samples → (varies, animated).

    ``varies``   — zoom/pan/tilt change across the clip beyond the static thresholds.
    ``animated`` — that variation is a smooth, monotonic ramp on every changing axis
                   (i.e. a real keyframed reframe), as opposed to erratic disagreement
                   (a bad/ambiguous match). Animated clips get keyframed, not rejected.
    """
    if len(samples) < 2:
        return False, False
    ordered = sorted(samples, key=lambda s: s.clip_local_frame)
    zooms = [float(s.scale) for s in ordered]
    pans = [float(s.position_x) * 1.0 for s in ordered]
    tilts = [float(s.position_y) * 1.0 for s in ordered]
    _, _, zspread = scale_spread(zooms)

    # Animation thresholds are deliberately smaller than the static-disagreement
    # reject threshold (max_scale_spread): a modest push-in the editor keyframed
    # (e.g. ×1.1 zoom over the clip) must be reproduced as a ramp, not flattened to
    # one mid value.
    pan_tol = float(config.get("keyframe_pan_delta_norm", 0.01))  # fraction of frame
    zoom_spread_min = float(config.get("keyframe_zoom_spread", 1.06))
    zoom_varies = zspread > zoom_spread_min
    pan_varies = (max(pans) - min(pans)) > pan_tol
    tilt_varies = (max(tilts) - min(tilts)) > pan_tol
    varies = zoom_varies or pan_varies or tilt_varies
    if not varies:
        return False, False

    animated = True
    if zoom_varies and not _monotonic_ramp(zooms, max(zooms) * 0.01):
        animated = False
    if pan_varies and not _monotonic_ramp(pans, pan_tol):
        animated = False
    if tilt_varies and not _monotonic_ramp(tilts, pan_tol):
        animated = False
    return True, animated


def ecc_consensus_passes(scales: list[float], rep_score: float, config: AppConfig) -> bool:
    """Accept a low-structure clip on ECC geometry alone.

    Smoke/sky/fire shots can fail every per-sample refine yet have a correct,
    stable ECC zoom. When enough independent frames agree on a plausible zoom and
    the best of them clears the geometry-trust floor, the geometry is corroborated
    even though NCC/structure are weak.
    """
    need = int(config.get("ecc_consensus_min_samples", 2))
    if len(scales) < need:
        return False
    _, _, spread = scale_spread(scales)
    trust_floor = float(config.get("geometry_trust_min_score", 0.7))
    return spread <= max_scale_spread(config) and float(rep_score) >= trust_floor


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


def accept_mode(config: AppConfig) -> str:
    """'agreement' trusts corroborated geometry; 'score' is the strict NCC gate."""
    return str(config.get("accept_mode", "agreement")).lower()


def geometry_trust_min_score(config: AppConfig) -> float:
    """Lowest match score still accepted when the geometry is independently corroborated."""
    return float(config.get("geometry_trust_min_score", 0.70))


def geometry_agreement_zoom_tol(config: AppConfig) -> float:
    """Max relative disagreement between two independent zoom estimates to call it corroborated."""
    return float(config.get("geometry_agreement_zoom_tol", 0.04))


def geometry_corroborated(ecc_scale: float, refine_zoom: float, config: AppConfig) -> bool:
    """
    Whether the pyramid-ECC zoom and the Resolve-Edit refine zoom agree.

    Caveat: the refine is *seeded* from the ECC warp, so on low-structure content
    where the refine barely moves this largely re-confirms the seed rather than
    being a fully independent estimate. It is corroboration, not proof — samples
    accepted on this basis are tagged "agreement" so they can be eyeballed
    (see difference.jpg / near-black%).
    """
    a, b = float(ecc_scale), float(refine_zoom)
    if a <= 0.0 or b <= 0.0:
        return False
    return abs(a / b - 1.0) <= geometry_agreement_zoom_tol(config)


def sample_refine_accepted(
    *,
    ncc: float,
    gradient_ncc: float,
    edit: ClipEditTransform,
    ecc_scale: float,
    timeline_size: tuple[int, int],
    config: AppConfig,
) -> tuple[bool, list[str], str]:
    """
    Decide whether a single refined sample is good enough to keep.

    Returns (accepted, reasons, accept_via) where accept_via is "score" (strict NCC),
    "agreement" (lower trust floor + corroborated geometry), or "zoom-only".

    score mode:      strict — NCC >= refine threshold, structure floor, plausible.
    agreement mode:  plausible + structural + (high NCC OR corroborated geometry at a
                     lower trust floor). This keeps geometrically-correct matches that
                     a grade/smoke/fire legitimately caps below the strict NCC bar.
    zoom-only:       a low-structure clip with NO reframe (Pan/Tilt ≈ 0) whose zoom is
                     corroborated (ECC ≈ refine) is accepted on zoom alone at a lower
                     structure floor. Pan-runaways never qualify (their position ≠ 0),
                     so this can't pass a garbage shift.
    """
    plausible = refine_edit_plausible(edit, timeline_size, config)
    structural = refine_structure_passes(gradient_ncc, config)
    corroborated = geometry_corroborated(ecc_scale, edit.zoom_x, config)
    zoom_only = _zoom_only_rescue(structural, corroborated, edit, gradient_ncc, config)

    pan_reason = (
        f"pan/tilt {edit.pan:.0f},{edit.tilt:.0f} exceed "
        f"{max_refine_pan_tilt_pixels(timeline_size, config):.0f}px"
    )
    struct_reason = (
        f"structure {gradient_ncc:.3f} < {refine_gradient_floor(config):.2f} (intensity-only match)"
    )

    if accept_mode(config) == "score":
        reasons: list[str] = []
        if not plausible:
            reasons.append(pan_reason)
        if not structural:
            reasons.append(struct_reason)
        if not refine_ncc_passes(ncc, config):
            reasons.append(f"NCC {ncc:.4f} < {refine_ncc_threshold(config):.2f}")
        return (not reasons), reasons, "score"

    return _agreement_verdict(
        ncc=ncc,
        ecc_scale=ecc_scale,
        edit=edit,
        config=config,
        plausible=plausible,
        structural=structural,
        corroborated=corroborated,
        zoom_only=zoom_only,
        pan_reason=pan_reason,
        struct_reason=struct_reason,
    )


def _zoom_only_rescue(
    structural: bool,
    corroborated: bool,
    edit: ClipEditTransform,
    gradient_ncc: float,
    config: AppConfig,
) -> bool:
    """A no-reframe clip whose corroborated zoom may be trusted on zoom alone."""
    # Only rescues the low-structure band [zoom_only_floor, normal floor): a clip that
    # already clears the normal structure floor must go through the standard logic.
    snap = float(config.get("snap_pan_tilt_below_pixels", 2.0))
    position_zero = abs(float(edit.pan)) < snap and abs(float(edit.tilt)) < snap
    return (
        not structural
        and position_zero
        and corroborated
        and float(gradient_ncc) >= float(config.get("refine_zoom_only_min_gradient", 0.30))
    )


def _agreement_verdict(
    *,
    ncc: float,
    ecc_scale: float,
    edit: ClipEditTransform,
    config: AppConfig,
    plausible: bool,
    structural: bool,
    corroborated: bool,
    zoom_only: bool,
    pan_reason: str,
    struct_reason: str,
) -> tuple[bool, list[str], str]:
    """Agreement-mode decision: high NCC, corroborated geometry, or the zoom-only rescue."""
    high_conf = float(ncc) >= float(config.get("min_match_score", 0.95))
    trust_floor = geometry_trust_min_score(config)
    score_ok = high_conf or (corroborated and float(ncc) >= trust_floor) or zoom_only

    reasons: list[str] = []
    if not plausible:
        reasons.append(pan_reason)
    if not structural and not zoom_only:
        reasons.append(struct_reason)
    if not score_ok:
        if not corroborated:
            reasons.append(
                f"zoom disagree (ecc {ecc_scale:.3f} vs refine {edit.zoom_x:.3f}); NCC {ncc:.4f}"
            )
        else:
            reasons.append(f"NCC {ncc:.4f} < trust floor {trust_floor:.2f}")

    if reasons:
        return False, reasons, ""
    if high_conf:
        via = "score"
    elif not structural:  # rescued only by the zoom-only relaxation
        via = "zoom-only"
    else:
        via = "agreement"
    return True, [], via


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
        lo, hi, spread = scale_spread(scales)
        if spread > max_scale_spread(config):
            reasons.append(f"scale varies {lo:.3f}…{hi:.3f} (×{spread:.2f})")

    if len(ok_samples) >= 2:
        pos_threshold = float(config.get("transform_variance_threshold", 0.02))
        pos_vectors = [(s.position_x, s.position_y, s.rotation_deg, 0.0) for s in ok_samples]
        if not transforms_are_stable(pos_vectors, pos_threshold):
            reasons.append(f"pan/tilt/rot unstable across samples (threshold {pos_threshold})")

    return len(reasons) == 0, reasons
