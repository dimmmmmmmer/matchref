"""Clip-selection UI helpers (pure; no Qt)."""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.selection_ui import (
    RESOLVE_COLORS,
    SELECTION_MODES,
    apply_selection_to_config,
    mode_uses_color,
    mode_uses_track,
    selection_from_config,
)


def test_modes_and_colors_are_offered() -> None:
    values = [v for v, _ in SELECTION_MODES]
    assert "clip_color" in values and "flagged" in values and "track" in values
    assert "Purple" in RESOLVE_COLORS and len(RESOLVE_COLORS) == 16


def test_mode_predicates() -> None:
    assert mode_uses_color("clip_color")
    assert mode_uses_color("flagged")
    assert mode_uses_color("auto")
    assert not mode_uses_color("all_filtered")
    assert mode_uses_track("track")
    assert not mode_uses_track("clip_color")


def test_apply_sets_mode_color_and_track() -> None:
    cfg = AppConfig()
    apply_selection_to_config(cfg, mode="clip_color", color="Green", track_index=2)
    assert cfg.get("clip_selection_mode") == "clip_color"
    assert cfg.get("selection_clip_color") == "Green"
    assert cfg.get("selection_flag_color") == "Green"  # flag shares the color
    assert cfg.get("video_track_index") == 2


def test_apply_normalizes_blank_color_and_track() -> None:
    cfg = AppConfig()
    apply_selection_to_config(cfg, mode="AUTO", color="  ", track_index=0)
    assert cfg.get("clip_selection_mode") == "auto"
    assert cfg.get("selection_clip_color") == "Purple"
    assert cfg.get("video_track_index") == 1  # clamped to >= 1


def test_roundtrip_through_config() -> None:
    cfg = AppConfig()
    apply_selection_to_config(cfg, mode="flagged", color="Red", track_index=3)
    assert selection_from_config(cfg) == ("flagged", "Red", 3)


def test_selection_from_config_defaults() -> None:
    assert selection_from_config(AppConfig()) == ("auto", "Purple", 1)
