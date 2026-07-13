"""Escalation retry policy + match_position-aware reframe triggers."""

from __future__ import annotations

import numpy as np

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.escalation import ConfigOverlay, escalated_config, should_escalate
from matchref.match_quality import sample_refine_accepted
from matchref.reframe_detect import ecc_suggests_reframe, should_refine_position


def _warp(tx: float, ty: float) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty]], dtype=np.float32)


# --- ConfigOverlay -----------------------------------------------------------


def test_overlay_overrides_win_and_fall_through() -> None:
    cfg = AppConfig()
    overlay = ConfigOverlay(cfg, {"max_refine_pan_tilt_fraction": 0.30})
    assert overlay.get("max_refine_pan_tilt_fraction") == 0.30
    # Non-overridden keys read through to the base config.
    assert overlay.get("min_match_score") == cfg.get("min_match_score")
    assert overlay.get("missing-key", "fallback") == "fallback"


def test_escalated_config_values() -> None:
    cfg = AppConfig()
    esc = escalated_config(cfg)
    assert esc.get("refine_position_mode") == "always"
    assert esc.get("max_refine_pan_tilt_fraction") == 0.30
    # No explicit pixel limit set → the fraction path stays authoritative.
    assert float(esc.get("max_refine_pan_tilt_pixels", 0.0)) == 0.0


def test_escalated_config_doubles_explicit_pixel_limit() -> None:
    cfg = AppConfig()
    cfg.set("max_refine_pan_tilt_pixels", 100.0)
    assert float(escalated_config(cfg).get("max_refine_pan_tilt_pixels")) == 200.0


# --- should_escalate ---------------------------------------------------------


def test_escalates_on_pan_tilt_limit_reason() -> None:
    cfg = AppConfig()
    reasons = ["pan/tilt 300,10 exceed 288px"]
    assert should_escalate(reasons, used_position=True, first_ncc=0.9, config=cfg)


def test_escalates_when_position_was_never_searched() -> None:
    cfg = AppConfig()
    reasons = ["NCC 0.9100 < 0.95"]
    assert should_escalate(reasons, used_position=False, first_ncc=0.91, config=cfg)


def test_no_escalation_for_quality_reject_with_position_tried() -> None:
    cfg = AppConfig()
    reasons = ["NCC 0.9100 < 0.95"]
    assert not should_escalate(reasons, used_position=True, first_ncc=0.91, config=cfg)


def test_no_escalation_on_hopeless_first_match() -> None:
    cfg = AppConfig()
    reasons = ["pan/tilt 300,10 exceed 288px"]
    assert not should_escalate(reasons, used_position=False, first_ncc=0.3, config=cfg)


def test_no_escalation_when_disabled() -> None:
    cfg = AppConfig()
    cfg.set("refine_escalation_enabled", False)
    reasons = ["pan/tilt 300,10 exceed 288px"]
    assert not should_escalate(reasons, used_position=False, first_ncc=0.9, config=cfg)


# --- escalated accept gates --------------------------------------------------


def test_escalated_gate_accepts_big_reframe_base_rejects() -> None:
    # A 25%-of-frame pan: over the default 15% ceiling, inside the escalated 30%.
    cfg = AppConfig()
    cfg.set("accept_mode", "score")
    edit = ClipEditTransform(zoom_x=1.1, zoom_y=1.1, pan=480.0, tilt=0.0, rotation_deg=0.0)
    size = (1920, 1080)
    ok_base, reasons_base, _ = sample_refine_accepted(
        ncc=0.99, gradient_ncc=0.8, edit=edit, ecc_scale=1.1, timeline_size=size, config=cfg
    )
    assert not ok_base
    assert any("pan/tilt" in r for r in reasons_base)
    ok_esc, reasons_esc, _ = sample_refine_accepted(
        ncc=0.99,
        gradient_ncc=0.8,
        edit=edit,
        ecc_scale=1.1,
        timeline_size=size,
        config=escalated_config(cfg),
    )
    assert ok_esc, reasons_esc


def test_escalated_gate_still_rejects_garbage() -> None:
    # Widened limits must not admit a low-quality match.
    cfg = AppConfig()
    cfg.set("accept_mode", "score")
    edit = ClipEditTransform(zoom_x=1.1, zoom_y=1.1, pan=480.0, tilt=0.0, rotation_deg=0.0)
    ok, reasons, _ = sample_refine_accepted(
        ncc=0.50,
        gradient_ncc=0.1,
        edit=edit,
        ecc_scale=1.1,
        timeline_size=(1920, 1080),
        config=escalated_config(cfg),
    )
    assert not ok
    assert reasons


# --- match_position strengthens the auto triggers ----------------------------


def test_small_shift_triggers_only_when_position_requested() -> None:
    cfg = AppConfig()
    size = (1920, 1080)
    shift = _warp(6.0, 0.0)  # between snap (2px) and the default trigger (12px)
    cfg.set("match_position", False)
    assert not ecc_suggests_reframe(shift, size, cfg)
    cfg.set("match_position", True)
    assert ecc_suggests_reframe(shift, size, cfg)


def test_refine_sample_escalates_and_accepts(monkeypatch) -> None:
    # Wire-level check: a pan/tilt-limit rejection triggers the escalated retry,
    # and its (widened-gate) acceptance marks the sample accept_via="escalated".
    import logging
    from types import SimpleNamespace

    import matchref.analysis_sampling as sampling_mod
    from matchref.analysis_context import _SampleFrames
    from matchref.models import SamplePoint, TransformSample
    from matchref.transform_analysis import TransformAnalyzer

    cfg = AppConfig()
    cfg.set("accept_mode", "score")
    big_pan_edit = ClipEditTransform(zoom_x=1.1, zoom_y=1.1, pan=480.0, tilt=0.0, rotation_deg=0.0)
    first_outcome = SimpleNamespace(
        ncc=0.99, gradient_ncc=0.8, edit=big_pan_edit, used_position=True, strategy="test"
    )
    monkeypatch.setattr(
        sampling_mod,
        "refine_resolve_edit",
        lambda *a, **k: SimpleNamespace(
            ncc=0.99, gradient_ncc=0.8, edit=big_pan_edit, used_position=True, strategy="esc"
        ),
    )

    analyzer = TransformAnalyzer.__new__(TransformAnalyzer)
    analyzer.config = cfg
    analyzer.logger = logging.getLogger("test")
    analyzer._timeline_canvas_size = lambda: (1920, 1080)  # type: ignore[method-assign]
    analyzer._run_refine = lambda *a, **k: first_outcome  # type: ignore[method-assign]
    analyzer._stash_ecc_candidate = lambda *a: None  # type: ignore[method-assign]
    analyzer._record_sample_reject = (  # type: ignore[method-assign]
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not reject"))
    )

    sample = TransformSample(sample_point=SamplePoint.MID, clip_local_frame=0, timeline_frame=0)
    loaded = _SampleFrames(
        sample=sample,
        terminal=False,
        online_raw=np.zeros((16, 16, 3), np.uint8),
        offline=np.zeros((16, 16, 3), np.uint8),
        absolute=True,
    )
    ctx = SimpleNamespace(
        result=SimpleNamespace(clip_name="clip", samples=[], warnings=[]),
        baseline=None,
    )
    point = SimpleNamespace(value="mid")

    rejected = analyzer._refine_sample(
        ctx, point, loaded, SimpleNamespace(scale=1.1), _warp(5, 3), None
    )
    assert rejected is None  # accepted, not recorded as a reject
    assert sample.accept_via == "escalated"
    assert sample.refined_edit is big_pan_edit


def test_ncc_floor_rises_when_position_requested() -> None:
    cfg = AppConfig()
    cfg.set("auto_reframe_ncc_threshold", 0.95)
    identity = ClipEditTransform(zoom_x=1.0, zoom_y=1.0, pan=0.0, tilt=0.0, rotation_deg=0.0)
    kwargs = {
        "config": cfg,
        "initial": identity,
        "warp": None,
        "timeline_size": (1920, 1080),
        "ncc_after_zoom": 0.96,  # above the 0.95 floor, below the requested 0.97
    }
    cfg.set("match_position", False)
    assert not should_refine_position(**kwargs)
    cfg.set("match_position", True)
    assert should_refine_position(**kwargs)
