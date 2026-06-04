"""Admission vs apply quality thresholds."""

from matchref.alignment import AlignmentResult, alignment_admission_threshold
from matchref.config import AppConfig
from matchref.match_quality import quality_threshold


def test_admission_uses_ecc_threshold_not_min_match_score() -> None:
    cfg = AppConfig()
    cfg.set("ecc_threshold", 0.85)
    cfg.set("min_match_score", 0.95)
    align = AlignmentResult(ok=True, message="ok (ecc)", ecc_score=0.88)
    assert alignment_admission_threshold(align, cfg) == 0.85
    assert quality_threshold(cfg) == 0.95


def test_apply_floor_stays_strict() -> None:
    cfg = AppConfig()
    assert quality_threshold(cfg) >= 0.95
