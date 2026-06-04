"""Post-refine quality gates."""

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.match_quality import (
    geometry_corroborated,
    refine_edit_plausible,
    refine_ncc_passes,
    sample_refine_accepted,
    score_from_refine_ncc,
)

CANVAS = (3840, 1920)


def test_score_from_refine_ncc() -> None:
    assert abs(score_from_refine_ncc(0.9889) - 0.99445) < 1e-4
    assert score_from_refine_ncc(0.4471) < 0.75


def test_rejects_low_ncc_refine() -> None:
    cfg = AppConfig()
    assert not refine_ncc_passes(0.4471, cfg)


def test_rejects_insane_pan_tilt() -> None:
    cfg = AppConfig()
    edit = ClipEditTransform(pan=-599.0, tilt=699.0)
    assert not refine_edit_plausible(edit, (3840, 1920), cfg)


def test_accepts_small_adjustment() -> None:
    cfg = AppConfig()
    edit = ClipEditTransform(pan=9.7, tilt=-6.3)
    assert refine_edit_plausible(edit, (3840, 1920), cfg)


def test_geometry_corroborated() -> None:
    cfg = AppConfig()
    assert geometry_corroborated(1.125, 1.125, cfg)
    assert geometry_corroborated(1.133, 1.133, cfg)
    assert not geometry_corroborated(1.125, 1.40, cfg)


def test_agreement_accepts_corroborated_low_ncc() -> None:
    """Graded/smoke clip: NCC 0.79 but ECC and refine zoom agree -> accept."""
    cfg = AppConfig()  # default accept_mode == agreement
    edit = ClipEditTransform(zoom_x=1.125, zoom_y=1.125, pan=0.0, tilt=0.0)
    ok, reasons, via = sample_refine_accepted(
        ncc=0.788, gradient_ncc=0.6, edit=edit, ecc_scale=1.125,
        timeline_size=CANVAS, config=cfg,
    )
    assert ok, reasons
    assert via == "agreement"  # accepted on corroboration, not the strict score


def test_agreement_rejects_below_trust_floor() -> None:
    cfg = AppConfig()
    edit = ClipEditTransform(zoom_x=1.041, zoom_y=1.041, pan=0.0, tilt=0.0)
    ok, _, _ = sample_refine_accepted(
        ncc=0.474, gradient_ncc=0.6, edit=edit, ecc_scale=1.041,
        timeline_size=CANVAS, config=cfg,
    )
    assert not ok


def test_agreement_rejects_zoom_disagreement() -> None:
    """Geometry not corroborated (ecc != refine zoom) at low NCC -> reject."""
    cfg = AppConfig()
    edit = ClipEditTransform(zoom_x=1.40, zoom_y=1.40, pan=0.0, tilt=0.0)
    ok, _, _ = sample_refine_accepted(
        ncc=0.80, gradient_ncc=0.6, edit=edit, ecc_scale=1.10,
        timeline_size=CANVAS, config=cfg,
    )
    assert not ok


def test_score_mode_stays_strict() -> None:
    cfg = AppConfig()
    cfg.set("accept_mode", "score")
    edit = ClipEditTransform(zoom_x=1.125, zoom_y=1.125, pan=0.0, tilt=0.0)
    ok, _, _ = sample_refine_accepted(
        ncc=0.788, gradient_ncc=0.6, edit=edit, ecc_scale=1.125,
        timeline_size=CANVAS, config=cfg,
    )
    assert not ok  # strict NCC gate (0.90) rejects 0.788
