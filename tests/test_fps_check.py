"""Timeline FPS / resolution validation."""

from __future__ import annotations

import logging

import pytest

from matchref.config import AppConfig
from matchref.conform_edl import EdlEvent
from matchref.conform_index import ConformIndex
from matchref.fps_check import FpsMismatchError, validate_against_timeline
from matchref.timecode import TimecodeFormat
from matchref.timeline_context import TimelineContext

HUB = TimelineContext(fps=24.0, width=1920, height=1080)


def _cfg() -> AppConfig:
    # Don't inherit a real offline_reference_path from the developer's user_config —
    # validate_against_timeline would probe that file. Keep the test hermetic.
    cfg = AppConfig()
    cfg.set("offline_reference_path", "")
    return cfg


def _conform(fps: float) -> ConformIndex:
    idx = ConformIndex(_cfg())
    idx.events = [EdlEvent(1, "A", "V", "C", 0, 1, 0, 1)]  # is_available
    idx.timecode_format = TimecodeFormat(fps=fps)
    return idx


def test_no_conform_no_reference_passes() -> None:
    validate_against_timeline(HUB, _cfg())  # no raise


def test_matching_conform_fps_passes() -> None:
    validate_against_timeline(HUB, _cfg(), conform=_conform(24.0))  # no raise


def test_mismatched_conform_fps_raises_when_strict() -> None:
    cfg = _cfg()
    cfg.set("require_fps_match", True)
    with pytest.raises(FpsMismatchError):
        validate_against_timeline(HUB, cfg, conform=_conform(25.0))


def test_mismatched_conform_fps_warns_when_not_strict(caplog) -> None:
    cfg = _cfg()
    cfg.set("require_fps_match", False)
    with caplog.at_level(logging.WARNING, logger="matchref"):
        validate_against_timeline(HUB, cfg, conform=_conform(25.0))  # no raise
    assert any("mismatch" in r.getMessage().lower() for r in caplog.records)
