"""Pure helpers backing the clip-selection UI (no Qt dependency).

Keeps the GUI thin: the dropdown options and the config <-> selection mapping live
here so they can be unit-tested without a Qt event loop.
"""

from __future__ import annotations

from matchref.config import AppConfig

# (config value, human label) for the "Clips to process" dropdown.
SELECTION_MODES: list[tuple[str, str]] = [
    ("auto", "Automatic (timeline selection → clip color → all)"),
    ("clip_color", "By clip color"),
    ("flagged", "By flag"),
    ("selected", "Selected in the timeline"),
    ("all_filtered", "All clips (minus the reference)"),
    ("track", "A whole video track"),
]

# DaVinci Resolve's 16 clip/flag colors, in UI order.
RESOLVE_COLORS: list[str] = [
    "Blue",
    "Cyan",
    "Green",
    "Yellow",
    "Red",
    "Pink",
    "Purple",
    "Fuchsia",
    "Rose",
    "Lavender",
    "Sky",
    "Mint",
    "Lemon",
    "Sand",
    "Cocoa",
    "Cream",
]

_COLOR_MODES = {"auto", "clip_color", "colored", "flagged"}


def mode_uses_color(mode: str) -> bool:
    """Whether the chosen mode needs a color picker (clip color / flag)."""
    return str(mode).lower() in _COLOR_MODES


def mode_uses_track(mode: str) -> bool:
    return str(mode).lower() == "track"


def selection_from_config(config: AppConfig) -> tuple[str, str, int]:
    """Read (mode, color, track_index) from config for populating the UI."""
    mode = str(config.get("clip_selection_mode", "auto")).lower()
    color = (
        str(
            config.get("selection_clip_color") or config.get("selection_flag_color") or "Purple"
        ).strip()
        or "Purple"
    )
    track = int(config.get("video_track_index", 1) or 1)
    return mode, color, track


def apply_selection_to_config(
    config: AppConfig, *, mode: str, color: str, track_index: int
) -> None:
    """Write the UI selection back to config (clip color and flag share the color)."""
    config.set("clip_selection_mode", str(mode).lower())
    color = str(color).strip() or "Purple"
    config.set("selection_clip_color", color)
    config.set("selection_flag_color", color)
    config.set("video_track_index", max(1, int(track_index)))
