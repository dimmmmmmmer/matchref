"""Edit match mode helpers."""

from matchref.config import AppConfig
from matchref.edit_match_mode import (
    compose_result_with_baseline,
    is_absolute_edit_match,
    use_baseline_compensation_for_match,
)


def test_absolute_default() -> None:
    cfg = AppConfig()
    assert is_absolute_edit_match(cfg)
    assert not use_baseline_compensation_for_match(cfg)
    assert not compose_result_with_baseline(cfg)


def test_delta_legacy() -> None:
    cfg = AppConfig()
    cfg.set("edit_match_mode", "delta")
    cfg.set("compensate_clip_transform", True)
    cfg.set("compose_with_clip_transform", True)
    assert not is_absolute_edit_match(cfg)
    assert use_baseline_compensation_for_match(cfg)
    assert compose_result_with_baseline(cfg)
