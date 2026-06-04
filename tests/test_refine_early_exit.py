"""Early exit when refine NCC already meets quality threshold."""

import numpy as np

from matchref.clip_edit_transform import ClipEditTransform
from matchref.config import AppConfig
from matchref.refine_strategies import refine_multi_strategy


def test_stops_after_first_order_meets_ncc_threshold() -> None:
    cfg = AppConfig()
    cfg.set(
        "refine_strategy_orders",
        [["zoom", "position", "rotation"], ["position", "zoom", "rotation"]],
    )
    cfg.set("refine_early_exit_on_quality", True)
    cfg.set("auto_reframe_ncc_threshold", 0.95)

    attempts = {"n": 0}

    def cost_fn(_p: list[float]) -> float:
        return 0.0

    def score_fn(_img: np.ndarray) -> float:
        attempts["n"] += 1
        return 0.96 if attempts["n"] == 1 else 0.99

    online = np.zeros((64, 64, 3), dtype=np.uint8)
    offline = np.zeros((64, 64, 3), dtype=np.uint8)
    outcome = refine_multi_strategy(
        online_raw=online,
        offline_fit=offline,
        canvas_size=(64, 64),
        config=cfg,
        initial=ClipEditTransform(),
        cost_fn=cost_fn,
        score_fn=score_fn,
    )
    assert attempts["n"] == 1
    assert outcome.ncc >= 0.95


def test_tries_all_orders_when_early_exit_disabled() -> None:
    cfg = AppConfig()
    cfg.set(
        "refine_strategy_orders",
        [["zoom"], ["position"]],
    )
    cfg.set("refine_early_exit_on_quality", False)

    attempts = {"n": 0}

    def score_fn(_img: np.ndarray) -> float:
        attempts["n"] += 1
        return 0.96

    outcome = refine_multi_strategy(
        online_raw=np.zeros((32, 32, 3), dtype=np.uint8),
        offline_fit=np.zeros((32, 32, 3), dtype=np.uint8),
        canvas_size=(32, 32),
        config=cfg,
        initial=ClipEditTransform(),
        cost_fn=lambda _p: 0.0,
        score_fn=score_fn,
    )
    assert attempts["n"] == 2
    assert outcome.ncc >= 0.95
