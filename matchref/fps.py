"""Frame rate helpers (23.976 / 29.97 NTSC, tolerance checks)."""

from __future__ import annotations

# Exact film / NTSC fractional rates
FPS_23976 = 24000.0 / 1001.0  # 23.9760239…
FPS_2997 = 30000.0 / 1001.0  # 29.9700299…

CANONICAL_FPS = (
    FPS_23976,
    24.0,
    25.0,
    FPS_2997,
    30.0,
    48.0,
    50.0,
    59.94,
    60.0,
)


def effective_fps_from_xml_rate(timebase: float, ntsc: bool) -> float:
    """
    Premiere / DaVinci XMEML: timebase 24 + ntsc TRUE → 23.976 playback.
    """
    if ntsc:
        if abs(timebase - 24.0) < 0.15:
            return FPS_23976
        if abs(timebase - 30.0) < 0.15:
            return FPS_2997
    return float(timebase)


def normalize_fps(value: float) -> float:
    """Snap detected float to nearest standard editorial rate."""
    best = min(CANONICAL_FPS, key=lambda c: abs(c - value))
    if abs(best - value) <= 0.08:
        return best
    return float(value)


def fps_near_equal(a: float, b: float, tolerance: float = 0.01) -> bool:
    """True only for the same editorial rate (24 ≠ 23.976)."""
    if a <= 0 or b <= 0:
        return False
    return abs(normalize_fps(a) - normalize_fps(b)) <= tolerance


def format_fps(value: float) -> str:
    n = normalize_fps(value)
    if abs(n - FPS_23976) < 0.002:
        return "23.976"
    if abs(n - FPS_2997) < 0.002:
        return "29.97"
    if abs(n - round(n)) < 0.01:
        return str(int(round(n)))
    return f"{n:.3f}"
