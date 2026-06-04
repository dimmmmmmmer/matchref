"""Future matching modes (stubs)."""

from __future__ import annotations

from matchref.config import AppConfig


def perspective_match_enabled(config: AppConfig) -> bool:
    return bool(config.get("perspective_match_enabled", False))


def lens_distortion_match_enabled(config: AppConfig) -> bool:
    return bool(config.get("lens_distortion_match_enabled", False))


def dynamic_sampling_enabled(config: AppConfig) -> bool:
    return bool(config.get("dynamic_sampling_enabled", False))


def not_implemented(feature: str) -> None:
    raise NotImplementedError(
        f"{feature} is reserved for a future release. Enable only after implementation."
    )
