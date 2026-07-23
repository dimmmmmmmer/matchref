"""Second-chance refine for rejected samples: forced position search, widened limits."""

from __future__ import annotations

from typing import Any, cast

from matchref.config import AppConfig


class ConfigOverlay:
    """Read-through config view whose overrides win over the wrapped config."""

    def __init__(self, base: AppConfig, overrides: dict[str, Any]) -> None:
        self._base = base
        self._overrides = dict(overrides)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        return self._base.get(key, default)


def escalation_enabled(config: AppConfig) -> bool:
    return bool(config.get("refine_escalation_enabled", True))


def should_escalate(
    reasons: list[str],
    used_position: bool,
    first_ncc: float,
    config: AppConfig,
    *,
    rotation_deg: float = 0.0,
) -> bool:
    """Retry only when limits (not match quality) rejected the sample, or position never ran.

    A hopeless first match (very low NCC) is excluded: a wider search there would
    only fit noise. The escalation triggers are (a) the pan/tilt plausibility
    limit fired — the reframe may simply be bigger than the default 15% ceiling —
    (b) the first refine's rotation is pinned at the rotation cap, so the real
    rotation may be larger, and (c) the zoom-first descent never searched
    position, so a real reframe was never even attempted.
    """
    if not escalation_enabled(config):
        return False
    if float(first_ncc) < float(config.get("escalation_min_first_ncc", 0.5)):
        return False
    if any("pan/tilt" in reason for reason in reasons):
        return True
    if _rotation_pinned_at_cap(rotation_deg, config):
        return True
    return not used_position


def _rotation_pinned_at_cap(rotation_deg: float, config: AppConfig) -> bool:
    """A result sitting on the rotation clamp means the descent hit the wall."""
    if bool(config.get("lock_alignment_rotation", False)):
        return False
    max_rot = float(config.get("max_alignment_rotation_deg", 3.0))
    return max_rot > 0 and abs(float(rotation_deg)) >= max_rot - 0.05


def escalated_config(config: AppConfig) -> AppConfig:
    """A config view with position forced on and the pan/tilt + rotation caps widened."""
    overrides: dict[str, Any] = {
        "refine_position_mode": "always",
        "max_refine_pan_tilt_fraction": float(
            config.get("escalated_max_refine_pan_tilt_fraction", 0.30)
        ),
        "max_alignment_rotation_deg": float(
            config.get("escalated_max_alignment_rotation_deg", 10.0)
        ),
    }
    explicit = float(config.get("max_refine_pan_tilt_pixels", 0.0))
    if explicit > 0:
        overrides["max_refine_pan_tilt_pixels"] = explicit * 2.0
    # Downstream refine/accept code only calls .get(); the overlay is duck-typed.
    return cast(AppConfig, ConfigOverlay(config, overrides))
