"""Multi-order refine strategies."""

from matchref.config import AppConfig
from matchref.refine_strategies import (
    DEFAULT_STRATEGY_ORDERS,
    strategy_orders_from_config,
    use_multi_strategy_refine,
)


def test_default_three_orders() -> None:
    cfg = AppConfig()
    orders = strategy_orders_from_config(cfg)
    assert len(orders) >= 3
    assert ["zoom", "position", "rotation"] in orders


def test_custom_orders() -> None:
    cfg = AppConfig()
    cfg.set(
        "refine_strategy_orders",
        [["rotation", "zoom", "position"]],
    )
    assert strategy_orders_from_config(cfg) == [["rotation", "zoom", "position"]]


def test_multi_on_by_default() -> None:
    assert use_multi_strategy_refine(AppConfig())


def test_six_permutations_available() -> None:
    assert len(DEFAULT_STRATEGY_ORDERS) == 6
