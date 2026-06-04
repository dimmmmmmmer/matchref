"""Post-refine quality gates."""

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.match_quality import (
    refine_edit_plausible,
    refine_ncc_passes,
    score_from_refine_ncc,
)


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
