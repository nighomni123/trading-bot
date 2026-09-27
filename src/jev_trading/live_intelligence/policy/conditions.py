"""Deterministic evaluation of model-proposed conditions.

A pure function of the observed environment. It performs no I/O, consults no
provider, and has no opinion about whether a condition is *reasonable* -- only
about whether it currently holds.

The governing rule is that incomplete evidence is never a satisfied condition.
A missing price, an unknown timeframe, or a null threshold returns False, so an
unevaluable rule can never be the reason code opens a position.
"""
from __future__ import annotations

from ..schemas import ConditionSpec, MarketEnvironment


def _price(environment: MarketEnvironment) -> float | None:
    price = environment.price.last
    return price if price is not None and price > 0 else None


def evaluate_condition(environment: MarketEnvironment, spec: ConditionSpec | None) -> bool:
    """Return True only when `spec` provably holds against `environment`."""
    if spec is None:
        return False
    state = environment.timeframes.get(spec.timeframe)
    if state is None:
        # A condition about a timeframe the environment does not carry cannot
        # be checked, so it cannot be satisfied.
        return False

    if spec.kind == "immediate":
        return True
    if spec.kind == "trend_up":
        return state.trend_direction == "UP"
    if spec.kind == "trend_down":
        return state.trend_direction == "DOWN"

    value = spec.value
    if value is None:
        return False

    if spec.kind in {"price_above", "price_below"}:
        price = _price(environment)
        if price is None:
            return False
        return price > value if spec.kind == "price_above" else price < value

    if spec.kind in {"vwap_above", "vwap_below"}:
        vwap = state.vwap
        if vwap is None or vwap <= 0:
            return False
        return vwap > value if spec.kind == "vwap_above" else vwap < value

    if spec.kind in {"atr_above", "atr_below"}:
        atr = state.atr_fraction
        if atr is None:
            return False
        # ATR is a fraction of price; the condition value is in basis points so
        # the model states a magnitude rather than a dimensionless ratio.
        atr_bps = atr * 10_000
        return atr_bps > value if spec.kind == "atr_above" else atr_bps < value

    if spec.kind == "spread_below_bps":
        spread = environment.liquidity.spread
        if spread is None:
            return False
        return spread * 10_000 < value

    return False


def first_satisfied(
    environment: MarketEnvironment, specs: tuple[ConditionSpec, ...]
) -> str | None:
    """Describe the first satisfied condition, or None if none holds."""
    for spec in specs:
        if evaluate_condition(environment, spec):
            return spec.describe()
    return None
