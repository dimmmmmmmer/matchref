"""Adaptive limits: scale escalation, rotation-cap escalation, parsimony fallback."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import matchref.precision_align as pa_mod
from matchref.config import AppConfig
from matchref.escalation import escalated_config, should_escalate
from matchref.match_quality import alignment_scale_bounds, assess_clip_match_quality
from matchref.models import SamplePoint, TransformSample
from matchref.precision_align import _drop_unjustified_position
from matchref.transform_analysis import TransformAnalyzer


def _sample(**overrides: object) -> TransformSample:
    fields: dict = {
        "sample_point": SamplePoint.MID,
        "clip_local_frame": 24,
        "timeline_frame": 100,
        "ok": True,
        "scale": 1.2,
        "ecc_score": 0.97,
    }
    fields.update(overrides)
    return TransformSample(**fields)


# ---- alignment_scale_bounds -------------------------------------------------


def test_scale_bounds_strict_for_ordinary_scores() -> None:
    config = AppConfig()
    assert alignment_scale_bounds(0.90, config) == (0.4, 2.5)


def test_scale_bounds_widen_for_high_confidence() -> None:
    config = AppConfig()
    assert alignment_scale_bounds(0.97, config) == (0.25, 4.0)


def test_scale_bounds_escalation_can_be_disabled() -> None:
    config = AppConfig()
    config.set("alignment_scale_escalation", False)
    assert alignment_scale_bounds(0.99, config) == (0.4, 2.5)


def test_clip_quality_accepts_confident_blowup() -> None:
    config = AppConfig()
    passed, reasons = assess_clip_match_quality([_sample(scale=3.2, ecc_score=0.98)], config)
    assert passed
    assert reasons == []


def test_clip_quality_still_rejects_uncertain_blowup() -> None:
    config = AppConfig()
    passed, reasons = assess_clip_match_quality([_sample(scale=3.2, ecc_score=0.95)], config)
    assert not passed
    assert any("outside" in r for r in reasons)


def test_best_select_uses_escalated_bounds() -> None:
    analyzer = TransformAnalyzer.__new__(TransformAnalyzer)
    analyzer.config = AppConfig()
    analyzer.logger = logging.getLogger("matchref-test")
    ctx = SimpleNamespace(result=SimpleNamespace(clip_name="clip"))
    confident = _sample(scale=3.2, ecc_score=0.98)
    reasons, _ = analyzer._best_select_problems(ctx, [confident], confident)
    assert reasons == []
    uncertain = _sample(scale=3.2, ecc_score=0.90)
    reasons2, _ = analyzer._best_select_problems(ctx, [uncertain], uncertain)
    assert reasons2 and "outside" in reasons2[0]


# ---- rotation-cap escalation ------------------------------------------------


def test_escalates_when_rotation_pinned_at_cap() -> None:
    config = AppConfig()
    assert should_escalate([], True, 0.8, config, rotation_deg=3.0)
    assert should_escalate([], True, 0.8, config, rotation_deg=-2.96)


def test_no_escalation_below_rotation_cap() -> None:
    config = AppConfig()
    assert not should_escalate([], True, 0.8, config, rotation_deg=1.5)


def test_no_rotation_escalation_when_rotation_locked() -> None:
    config = AppConfig()
    config.set("lock_alignment_rotation", True)
    assert not should_escalate([], True, 0.8, config, rotation_deg=3.0)


def test_escalated_config_widens_rotation_cap() -> None:
    config = AppConfig()
    esc = escalated_config(config)
    assert esc.get("max_alignment_rotation_deg") == 10.0
    assert esc.get("refine_position_mode") == "always"


# ---- parsimony low-structure fallback ---------------------------------------


def _run_parsimony(
    monkeypatch,
    *,
    g_with: float,
    g_zero: float,
    ncc_with: float = 0.9,
    ncc_zero: float = 0.9,
) -> list[float]:
    # Renders are keyed by whether the state still carries the pan value.
    monkeypatch.setattr(
        pa_mod,
        "_render_state",
        lambda state, raw, canvas, config, rot: "with" if state[1] else "zero",
    )
    ref = SimpleNamespace(
        gradient_ncc=lambda render: g_with if render == "with" else g_zero,
        score=lambda render: ncc_with if render == "with" else ncc_zero,
    )
    state = [1.2, 40.0, -20.0, 0.0]
    _drop_unjustified_position(state, ref, None, (1920, 1080), AppConfig(), True)
    return state


def test_parsimony_keeps_position_on_clear_gradient_gain(monkeypatch) -> None:
    state = _run_parsimony(monkeypatch, g_with=0.60, g_zero=0.30)
    assert state[1] == 40.0
    assert state[2] == -20.0


def test_parsimony_zeroes_drift_on_structured_content(monkeypatch) -> None:
    # Plenty of structure (g_with above the low-structure floor) but no real gain.
    state = _run_parsimony(monkeypatch, g_with=0.55, g_zero=0.50)
    assert state[1] == 0.0
    assert state[2] == 0.0


def test_parsimony_low_structure_fallback_keeps_real_reframe(monkeypatch) -> None:
    # Gradients too weak to clear min_gain anywhere, but position still helps
    # both signals → the NCC tier keeps it.
    state = _run_parsimony(monkeypatch, g_with=0.20, g_zero=0.15, ncc_with=0.93, ncc_zero=0.88)
    assert state[1] == 40.0


def test_parsimony_low_structure_still_zeroes_noise(monkeypatch) -> None:
    # Weak gradients and the NCC barely moves → drift, zeroed.
    state = _run_parsimony(monkeypatch, g_with=0.20, g_zero=0.15, ncc_with=0.90, ncc_zero=0.895)
    assert state[1] == 0.0


def test_parsimony_fallback_needs_positive_gradient_trend(monkeypatch) -> None:
    # Position makes the gradient match worse — never rescued by the NCC tier.
    state = _run_parsimony(monkeypatch, g_with=0.15, g_zero=0.20, ncc_with=0.95, ncc_zero=0.88)
    assert state[1] == 0.0
