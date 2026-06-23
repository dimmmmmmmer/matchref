"""Unreadable media is reported with a reason (once per path), not silently."""

from __future__ import annotations

import logging

from matchref.config import AppConfig
from matchref.frame_provider import FrameProvider


def test_missing_media_returns_none_and_warns_once(caplog) -> None:
    fp = FrameProvider(AppConfig())
    with caplog.at_level(logging.WARNING, logger="matchref"):
        assert fp.get_online_frame("/no/such/file.mov", 0) is None
        assert fp.get_online_frame("/no/such/file.mov", 5) is None  # second call, same path

    warnings = [r for r in caplog.records if "Cannot read media" in r.getMessage()]
    assert len(warnings) == 1  # deduplicated per path
    assert "/no/such/file.mov" in warnings[0].getMessage()


def test_media_frame_count_zero_for_missing(caplog) -> None:
    fp = FrameProvider(AppConfig())
    with caplog.at_level(logging.WARNING, logger="matchref"):
        assert fp.get_media_frame_count("/no/such/file.mov") == 0
