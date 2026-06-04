"""ECC-consensus rescue: accept low-structure clips on stable, corroborated zoom."""

from matchref.config import AppConfig
from matchref.match_quality import ecc_consensus_passes


def _cfg() -> AppConfig:
    cfg = AppConfig()
    cfg.set("ecc_consensus_min_samples", 2)
    cfg.set("max_scale_spread_ratio", 1.35)
    cfg.set("geometry_trust_min_score", 0.70)
    return cfg


def test_accepts_consistent_zoom_above_trust() -> None:
    # A036R018-like: two samples agree on ~1.124, best score above floor.
    assert ecc_consensus_passes([1.124, 1.125], rep_score=0.84, config=_cfg())


def test_accepts_nasa_tail_case() -> None:
    # A059_A005-like: mid/end agree ~1.11, best score 0.746.
    assert ecc_consensus_passes([1.118, 1.112], rep_score=0.746, config=_cfg())


def test_rejects_single_sample() -> None:
    assert not ecc_consensus_passes([1.124], rep_score=0.95, config=_cfg())


def test_rejects_disagreeing_zoom() -> None:
    # Samples disagree wildly -> not corroborated.
    assert not ecc_consensus_passes([1.12, 1.9], rep_score=0.9, config=_cfg())


def test_rejects_below_trust_floor() -> None:
    # Consistent zoom but the best match itself is garbage.
    assert not ecc_consensus_passes([1.12, 1.13], rep_score=0.55, config=_cfg())


def test_respects_min_samples_config() -> None:
    cfg = _cfg()
    cfg.set("ecc_consensus_min_samples", 3)
    assert not ecc_consensus_passes([1.12, 1.13], rep_score=0.9, config=cfg)
    assert ecc_consensus_passes([1.12, 1.13, 1.125], rep_score=0.9, config=cfg)
