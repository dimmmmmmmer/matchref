"""Match quality gates."""

from matchref.config import AppConfig
from matchref.match_quality import assess_clip_match_quality
from matchref.models import SamplePoint, TransformSample


def _sample(scale: float, score: float, point: SamplePoint = SamplePoint.START) -> TransformSample:
    return TransformSample(
        sample_point=point,
        clip_local_frame=0,
        timeline_frame=0,
        scale=scale,
        ecc_score=score,
        ok=True,
    )


def test_rejects_unstable_scales() -> None:
    cfg = AppConfig()
    samples = [
        _sample(4.22, 0.25, SamplePoint.START),
        _sample(0.17, 0.16, SamplePoint.MID),
        _sample(0.18, 0.22, SamplePoint.END),
    ]
    passed, reasons = assess_clip_match_quality(samples, cfg)
    assert not passed
    assert any("scale" in r for r in reasons)


def test_rejects_low_score() -> None:
    cfg = AppConfig()
    samples = [
        _sample(1.0, 0.10, SamplePoint.START),
        _sample(1.05, 0.12, SamplePoint.MID),
    ]
    cfg.set("min_match_score", 0.25)
    passed, _ = assess_clip_match_quality(samples, cfg)
    assert not passed


def test_accepts_consistent_match() -> None:
    cfg = AppConfig()
    samples = [
        _sample(1.02, 0.96, SamplePoint.START),
        _sample(1.01, 0.95, SamplePoint.MID),
        _sample(1.00, 0.97, SamplePoint.END),
    ]
    passed, reasons = assess_clip_match_quality(samples, cfg)
    assert passed
    assert not reasons
