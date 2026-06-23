"""Mechanics hardening: ECC model choice, keyframe early-exit, apply read-back."""

from __future__ import annotations

import cv2
import pytest

from matchref.alignment import (
    AlignmentResult,
    _choose_ecc_result,
    _ecc_motion_modes,
)
from matchref.config import AppConfig
from matchref.edit_apply import EditTransformApplier
from matchref.timeline_context import TimelineContext
from matchref.transform_analysis import confident_break_allowed
from matchref.transform_convert import ResolvedTransform

# --- #4: homography is never solved for an affine Edit target ---------------


def test_homography_falls_back_to_affine() -> None:
    cfg = AppConfig()
    cfg.set("ecc_motion_mode", "homography")
    cfg.set("ecc_single_motion_mode", True)
    assert _ecc_motion_modes(cfg) == [cv2.MOTION_AFFINE]


def test_multi_mode_never_includes_homography() -> None:
    cfg = AppConfig()
    cfg.set("ecc_motion_mode", "homography")
    cfg.set("ecc_single_motion_mode", False)
    modes = _ecc_motion_modes(cfg)
    assert cv2.MOTION_HOMOGRAPHY not in modes
    assert cv2.MOTION_AFFINE in modes


# --- #3: prefer the simplest model that already clears the threshold --------


def test_choose_prefers_lowest_dof_among_passing() -> None:
    euclidean = AlignmentResult(ok=True, ecc_score=0.90)
    affine = AlignmentResult(ok=True, ecc_score=0.95)  # higher score, more DOF
    chosen = _choose_ecc_result([(4, euclidean), (6, affine)], threshold=0.85)
    assert chosen is euclidean  # both pass → simpler model wins despite lower score


def test_choose_falls_back_to_best_score_when_none_pass() -> None:
    weak = AlignmentResult(ok=True, ecc_score=0.50)
    less_weak = AlignmentResult(ok=True, ecc_score=0.70)
    chosen = _choose_ecc_result([(4, weak), (6, less_weak)], threshold=0.85)
    assert chosen is less_weak


def test_choose_empty_is_none() -> None:
    assert _choose_ecc_result([], threshold=0.85) is None


# --- #2: a confident sample may not flatten a possible keyframe ramp ---------


def test_break_allowed_when_keyframing_off() -> None:
    assert confident_break_allowed([1.2], keyframing=False, zoom_spread_limit=1.06)


def test_break_blocked_on_single_sample_when_keyframing_on() -> None:
    assert not confident_break_allowed([1.2], keyframing=True, zoom_spread_limit=1.06)


def test_break_allowed_when_two_samples_agree() -> None:
    assert confident_break_allowed([1.20, 1.21], keyframing=True, zoom_spread_limit=1.06)


def test_break_blocked_when_samples_diverge() -> None:
    # 1.0 → 1.5 is a 1.5x spread: an animated ramp, not a static zoom.
    assert not confident_break_allowed([1.0, 1.5], keyframing=True, zoom_spread_limit=1.06)


# --- #1 / #6: apply is verified, partial writes are hard failures -----------


class _FakeItem:
    """Stand-in TimelineItem recording SetProperty / GetProperty calls."""

    def __init__(
        self,
        fail_keys: set[str] | None = None,
        read_offset: dict[str, float] | None = None,
        has_getter: bool = True,
    ) -> None:
        self.props: dict[str, object] = {}
        self._fail = fail_keys or set()
        self._offset = read_offset or {}
        self._has_getter = has_getter

    def SetProperty(self, key: str, value: object) -> bool:
        if key in self._fail:
            return False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.props[key] = float(value) + self._offset.get(key, 0.0)
        else:
            self.props[key] = value
        return True

    def __getattr__(self, name: str):  # only expose GetProperty when requested
        if name == "GetProperty" and self._has_getter:
            return lambda key: self.props.get(key)
        raise AttributeError(name)


def _applier(cfg: AppConfig) -> EditTransformApplier:
    ctx = TimelineContext(fps=24.0, width=1920, height=1080)
    return EditTransformApplier(resolve=None, config=cfg, timeline_ctx=ctx)


def _resolved() -> ResolvedTransform:
    return ResolvedTransform(
        edit_zoom_x=1.25, edit_zoom_y=1.25, edit_pan=10.0, edit_tilt=-5.0, edit_rotation=0.0
    )


def _full_match_config() -> AppConfig:
    cfg = AppConfig()
    cfg.set("match_scale", True)
    cfg.set("match_position", True)
    cfg.set("match_rotation", True)
    return cfg


def test_apply_writes_and_verifies_clean() -> None:
    item = _FakeItem()
    _applier(_full_match_config())._set_edit_properties(item, _resolved())
    assert item.props["ZoomX"] == pytest.approx(1.25)
    assert item.props["Pan"] == pytest.approx(10.0)


def test_partial_setproperty_failure_raises() -> None:
    item = _FakeItem(fail_keys={"Tilt"})
    with pytest.raises(RuntimeError, match="Tilt"):
        _applier(_full_match_config())._set_edit_properties(item, _resolved())


def test_readback_mismatch_raises() -> None:
    item = _FakeItem(read_offset={"ZoomX": 0.5})  # Resolve "stored" a different value
    with pytest.raises(RuntimeError, match="not confirmed"):
        _applier(_full_match_config())._set_edit_properties(item, _resolved())


def test_readback_disabled_skips_verification() -> None:
    cfg = _full_match_config()
    cfg.set("verify_apply_readback", False)
    item = _FakeItem(read_offset={"ZoomX": 0.5})
    _applier(cfg)._set_edit_properties(item, _resolved())  # no raise


def test_missing_getproperty_does_not_block() -> None:
    item = _FakeItem(has_getter=False)
    _applier(_full_match_config())._set_edit_properties(item, _resolved())  # best-effort
