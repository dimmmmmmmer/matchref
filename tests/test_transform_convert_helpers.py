"""Edit-value helpers behind resolve_transform (zoom base, rotation gates)."""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.transform_convert import _edit_rotation, _edit_zoom


def test_edit_zoom_scales_by_base() -> None:
    cfg = AppConfig()
    cfg.set("edit_zoom_base", 1.0)
    assert _edit_zoom(1.5, cfg) == 1.5


def test_edit_zoom_legacy_percent_mode() -> None:
    # A base > 10 is treated as the legacy "percent" mode: 100.0 → factor 1.0.
    cfg = AppConfig()
    cfg.set("edit_zoom_base", 100.0)
    assert _edit_zoom(1.5, cfg) == 1.5


def test_edit_zoom_ignores_scale_when_match_scale_off() -> None:
    cfg = AppConfig()
    cfg.set("edit_zoom_base", 1.0)
    cfg.set("match_scale", False)
    assert _edit_zoom(1.5, cfg) == 1.0


def test_edit_zoom_clamped() -> None:
    cfg = AppConfig()
    assert _edit_zoom(1000.0, cfg) == 16.0
    assert _edit_zoom(0.0, cfg) == 0.001


def test_edit_rotation_zeroed_when_unmatched() -> None:
    cfg = AppConfig()
    cfg.set("match_rotation", False)
    cfg.set("edit_priority_zoom", False)
    assert _edit_rotation(2.0, cfg) == 0.0


def test_edit_rotation_inverted() -> None:
    cfg = AppConfig()
    cfg.set("match_rotation", True)
    cfg.set("edit_priority_zoom", False)
    cfg.set("invert_rotation", True)
    assert _edit_rotation(2.0, cfg) == -2.0


def test_edit_rotation_out_of_range_zeroed() -> None:
    cfg = AppConfig()
    cfg.set("match_rotation", True)
    cfg.set("edit_priority_zoom", False)
    cfg.set("max_alignment_rotation_deg", 3.0)
    assert _edit_rotation(5.0, cfg) == 0.0
    assert _edit_rotation(2.0, cfg) == 2.0
