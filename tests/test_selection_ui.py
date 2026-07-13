"""Clip-selection UI helpers (pure; no Qt)."""

from __future__ import annotations

from matchref.config import AppConfig
from matchref.selection_ui import (
    CLIP_COLORS,
    FLAG_COLORS,
    SELECTION_MODES,
    apply_selection_to_config,
    colors_for_mode,
    mode_uses_color,
    mode_uses_track,
    selection_from_config,
)


def test_modes_are_offered() -> None:
    values = [v for v, _ in SELECTION_MODES]
    assert "clip_color" in values and "flagged" in values and "track" in values


def test_palettes_match_resolve() -> None:
    # Resolve has two DIFFERENT 16-color palettes: timeline clip colors and
    # flag colors. Offering flag names for clip selection can never match.
    assert len(CLIP_COLORS) == 16 and len(FLAG_COLORS) == 16
    assert {"Orange", "Teal", "Navy", "Chocolate"} <= set(CLIP_COLORS)
    assert {"Cyan", "Fuchsia", "Lavender", "Cream"} <= set(FLAG_COLORS)
    assert "Cyan" not in CLIP_COLORS and "Teal" not in FLAG_COLORS
    # The project default must be valid in both palettes.
    assert "Purple" in CLIP_COLORS and "Purple" in FLAG_COLORS


def test_colors_for_mode() -> None:
    assert colors_for_mode("flagged") == FLAG_COLORS
    assert colors_for_mode("clip_color") == CLIP_COLORS
    assert colors_for_mode("auto") == CLIP_COLORS


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
    assert cfg.get("selection_flag_color") == "Green"  # Green is valid in both palettes
    assert cfg.get("video_track_index") == 2


def test_apply_keeps_palettes_separate() -> None:
    cfg = AppConfig()
    apply_selection_to_config(cfg, mode="flagged", color="Cyan", track_index=1)
    assert cfg.get("selection_flag_color") == "Cyan"
    # Cyan is not a clip color — the clip-color key must keep its default.
    assert cfg.get("selection_clip_color") == "Purple"
    apply_selection_to_config(cfg, mode="clip_color", color="Teal", track_index=1)
    assert cfg.get("selection_clip_color") == "Teal"
    # Teal is not a flag color — the flag key must keep the previous value.
    assert cfg.get("selection_flag_color") == "Cyan"


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
    apply_selection_to_config(cfg, mode="clip_color", color="Navy", track_index=3)
    assert selection_from_config(cfg) == ("clip_color", "Navy", 3)


def test_selection_from_config_defaults() -> None:
    assert selection_from_config(AppConfig()) == ("auto", "Purple", 1)
