"""Stage 8: read-only quant/economic diagnostic.

Answers *why* candidates are rejected. Changes nothing: no threshold, cost,
horizon, R:R, quant, or risk parameter is modified. No LLM call. No execution.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import polars as pl

from jev_trading.environment import assess_regime, build_market_environment
from jev_trading.live_intelligence.config import LiveSettings
from jev_trading.live_intelligence.decision_quality import (
    _quant_hypothesis,
    causal_window,
    future_outcome,
    load_history,
    sample_timestamps,
    _dt,
)
from jev_trading.live_intelligence.policy import PolicyFinalizer, make_candidate
from jev_trading.live_intelligence.quant import (
    QuantRegistry,
    analyze_path,
    build_completed_path_samples,
    calculate_economic_value,
)
from jev_trading.live_intelligence.schemas import (
    DataQuality,
    PositionState,
)

HISTORY = "data/btcusdt_1m.parquet"


# ---------------------------------------------------------------- step 1 + 2


def _fixed_cost_bps(settings: LiveSettings) -> float:
    costs = settings.costs
    return (2 * costs.fee_bps_per_side + 2 * costs.slippage_bps_per_side + costs.latency_bps)


def required_p_target(
    *, stop_fraction: float, target_fraction: float, fixed_cost: float, p_stop: float,
    timeout_return: float = 0.0,
) -> float | None:
    """Minimum p_target for net_expected_value == 0 at this barrier geometry.

    From economic.py:
        gross = p_t*target - p_s*stop + p_to*timeout      (p_to = 1 - p_s - p_t)
        net   = gross - cost
    Setting net = 0 and substituting p_to gives a linear equation in p_t:
        p_t * (target - timeout) = cost - p_s*stop + (1 - p_s)*timeout
    """
    denominator = target_fraction - timeout_return
    if denominator <= 0:
        return None
    base = p_stop * stop_fraction - max(0.0, 1.0 - p_stop) * timeout_return
    return (fixed_cost + base) / denominator


def diagnose_candidate(
    frame: pl.DataFrame, at: datetime, settings: LiveSettings, *,
    horizon_minutes: int | None = None, rr_multiple: float | None = None,
) -> dict[str, Any] | None:
    """Build one candidate from causal data and decompose its economics."""
    bars = causal_window(frame, at, warmup=settings.market.warmup_bars)
    if bars.height < settings.market.warmup_bars:
        return None
    horizon = horizon_minutes or (settings.policy.maximum_holding_seconds // 60)
    environment = build_market_environment(
        bars, ticks=[], quality=DataQuality(safe_for_trading=True, stale=False),
        position=PositionState(), decision_timestamp=at,
    )
    price = environment.price.last
    if not price:
        return None

    hypothesis = _quant_hypothesis(environment, assess_regime(environment), f"q-{int(at.timestamp())}")
    base_candidate = make_candidate(environment, hypothesis, settings=settings)
    if base_candidate is None:
        return None

    state = environment.timeframes["15m"]
    atr_fraction = state.atr_fraction or 0.001
    rr = rr_multiple if rr_multiple is not None else settings.policy.target_atr_multiple / settings.policy.stop_atr_multiple
    stop_fraction = max(price * atr_fraction * settings.policy.stop_atr_multiple, price * settings.policy.minimum_distance_bps / 10_000)
    stop_fraction = stop_fraction / price
    target_fraction = stop_fraction * rr

    samples = build_completed_path_samples(
        bars, side=base_candidate.side, target_fraction=target_fraction,
        stop_fraction=stop_fraction, horizon_minutes=horizon, max_samples=500,
    )
    if not samples:
        return None
    path = analyze_path(
        environment, samples, side=base_candidate.side, target_fraction=target_fraction,
        stop_fraction=stop_fraction, horizon_minutes=horizon, horizon_seconds=horizon * 60,
    )
    if path.path_probabilities is None:
        return None

    costs = settings.costs.assumptions(horizon * 60, environment.derivatives.funding or 0.0)
    value = calculate_economic_value(
        base_candidate, path.path_probabilities, costs,
        timeout_return_fraction=path.expected_timeout_return or 0.0,
        expected_duration_seconds=path.expected_duration_seconds,
        sample_size=path.empirical_sample_size or 0,
    )
    fixed = _fixed_cost_bps(settings) / 10_000
    required = required_p_target(
        stop_fraction=stop_fraction, target_fraction=target_fraction,
        fixed_cost=fixed, p_stop=path.path_probabilities["stop"],
        timeout_return=path.expected_timeout_return or 0.0,
    )
    realized = future_outcome(frame, at, base_candidate.side.value, horizon)
    return {
        "timestamp": at.isoformat(),
        "side": base_candidate.side.value,
        "atr_bps": atr_fraction * 10_000,
        "stop_bps": stop_fraction * 10_000,
        "target_bps": target_fraction * 10_000,
        "rr": round(rr, 2),
        "horizon_minutes": horizon,
        "sample_size": path.empirical_sample_size,
        "p_target": path.path_probabilities["target"],
        "p_stop": path.path_probabilities["stop"],
        "p_timeout": path.path_probabilities["timeout"],
        "gross_bps": value.gross_expected_payoff * 10_000,
        "fees_bps": value.fees * 10_000,
        "slippage_bps": value.slippage * 10_000,
        "funding_bps": value.funding * 10_000,
        "latency_bps": value.latency * 10_000,
        "net_bps": value.net_expected_value * 10_000,
        "required_p_target": required,
        "observed_over_required": (path.path_probabilities["target"] / required) if required else None,
        "expected_mfe_bps": (path.expected_payoff or 0.0) * 10_000,
        "expected_mae_bps": (path.expected_downside or 0.0) * 10_000,
        "realized_return_bps": (realized["realized_return"] or 0.0) * 10_000,
        "realized_mfe_bps": (realized["mfe"] or 0.0) * 10_000,
        "realized_mae_bps": (realized["mae"] or 0.0) * 10_000,
    }


def rejection_reasons(
    frame: pl.DataFrame, stamps: list[datetime], settings: LiveSettings,
) -> tuple[list[dict[str, Any]], Counter]:
    """Step 1: structured policy rejection attribution per candidate."""
    policy = PolicyFinalizer(settings)
    rows: list[dict[str, Any]] = []
    reasons: Counter = Counter()
    for at in stamps:
        detail = diagnose_candidate(frame, at, settings)
        if detail is None:
            continue
        bars = causal_window(frame, at, warmup=settings.market.warmup_bars)
        environment = build_market_environment(
            bars, ticks=[], quality=DataQuality(safe_for_trading=True, stale=False),
            position=PositionState(), decision_timestamp=at,
        )
        hypothesis = _quant_hypothesis(environment, assess_regime(environment), f"q-{int(at.timestamp())}")
        candidate = make_candidate(environment, hypothesis, settings=settings)
        samples = build_completed_path_samples(
            bars, side=candidate.side, target_fraction=detail["target_bps"] / 10_000,
            stop_fraction=detail["stop_bps"] / 10_000,
            horizon_minutes=detail["horizon_minutes"], max_samples=500,
        )
        path = analyze_path(
            environment, samples, side=candidate.side,
            target_fraction=detail["target_bps"] / 10_000, stop_fraction=detail["stop_bps"] / 10_000,
            horizon_minutes=detail["horizon_minutes"], horizon_seconds=detail["horizon_minutes"] * 60,
        )
        costs = settings.costs.assumptions(detail["horizon_minutes"] * 60, environment.derivatives.funding or 0.0)
        value = calculate_economic_value(
            candidate, path.path_probabilities, costs,
            timeout_return_fraction=path.expected_timeout_return or 0.0,
            expected_duration_seconds=path.expected_duration_seconds,
            sample_size=path.empirical_sample_size or 0,
        )
        decision = policy.finalize(
            environment, hypothesis, value, None,
            position=PositionState(), candidate=candidate, require_jev=False,
        )
        for reason in decision.reasons:
            reasons[reason] += 1
        rows.append({**detail, "policy_action": decision.action.value,
                     "policy_reasons": list(decision.reasons),
                     "policy_eligible": decision.action.value in {"ENTER_LONG", "ENTER_SHORT"}})
    return rows, reasons


# ------------------------------------------------------------------ step 3


def geometry_sweep(
    frame: pl.DataFrame, stamps: list[datetime], settings: LiveSettings, *,
    horizons=(15, 30, 60, 120, 240), rrs=(1.0, 2.0, 3.0),
) -> list[dict[str, Any]]:
    """Step 3: does barrier geometry or horizon bind? Measurement only."""
    out = []
    for horizon in horizons:
        for rr in rrs:
            details = [diagnose_candidate(frame, at, settings, horizon_minutes=horizon, rr_multiple=rr) for at in stamps]
            details = [d for d in details if d]
            if not details:
                continue
            fixed = _fixed_cost_bps(settings)
            positives = [d for d in details if d["net_bps"] > 0]
            out.append({
                "horizon_minutes": horizon, "rr": rr, "n": len(details),
                "median_p_target": _median([d["p_target"] for d in details]),
                "median_p_stop": _median([d["p_stop"] for d in details]),
                "median_p_timeout": _median([d["p_timeout"] for d in details]),
                "median_gross_bps": _median([d["gross_bps"] for d in details]),
                "median_net_bps": _median([d["net_bps"] for d in details]),
                "best_net_bps": max(d["net_bps"] for d in details),
                "net_positive_count": len(positives),
                "median_required_p_target": _median([d["required_p_target"] for d in details if d["required_p_target"]]),
                "median_observed_over_required": _median(
                    [d["observed_over_required"] for d in details if d["observed_over_required"]]
                ),
                "fixed_cost_bps": fixed,
            })
    return out


# ------------------------------------------------------------------ step 4


def cost_decomposition(rows: list[dict[str, Any]], frame: pl.DataFrame) -> dict[str, Any]:
    """Step 4: where the edge actually goes."""
    if not rows:
        return {}
    empirical = _empirical_round_trip_bps(frame)
    return {
        "configured": {
            "fees_bps": _median([r["fees_bps"] for r in rows]),
            "slippage_bps": _median([r["slippage_bps"] for r in rows]),
            "funding_bps": _median([r["funding_bps"] for r in rows]),
            "latency_bps": _median([r["latency_bps"] for r in rows]),
            "total_bps": _median([r["fees_bps"] + r["slippage_bps"] + r["funding_bps"] + r["latency_bps"] for r in rows]),
        },
        "gross_bps": {
            "median": _median([r["gross_bps"] for r in rows]),
            "max": max(r["gross_bps"] for r in rows),
        },
        "cost_stack_vs_gross": {
            "median_cost_as_multiple_of_median_gross": _median([
                r["fees_bps"] + r["slippage_bps"] + r["funding_bps"] + r["latency_bps"]
                for r in rows
            ]) / _median([r["gross_bps"] for r in rows]) if _median([r["gross_bps"] for r in rows]) else None,
        },
        "empirical_round_trip_bps": empirical,
        "break_even_cost_bps_to_match_median_gross": _median([r["gross_bps"] for r in rows]),
    }


def _empirical_round_trip_bps(frame: pl.DataFrame, sample: int = 200_000) -> float:
    """Observed close->next-open friction, a lower bound on round-trip cost."""
    tail = frame.tail(sample)
    opens, closes = tail["open"].to_list(), tail["close"].to_list()
    moves = [abs(o - c) / c * 10_000 for c, o in zip(closes, opens[1:]) if c]
    return _median(moves) if moves else float("nan")


# ------------------------------------------------------------------ step 5


CALIBRATION_BUCKETS = ((0.0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 1.01))


def calibration_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 5: predicted p_target vs realized hit rate and return."""
    out = []
    for low, high in CALIBRATION_BUCKETS:
        bucket = [r for r in rows if low <= r["p_target"] < high and r["p_timeout"] < 1.0]
        if not bucket:
            continue
        predicted = _median([r["p_target"] for r in bucket])
        # realized "hit" = forward return reached the target distance within horizon
        hits = [1.0 if r["realized_mfe_bps"] >= r["target_bps"] else 0.0 for r in bucket]
        out.append({
            "bucket": f"{low:.2f}-{high:.2f}",
            "n": len(bucket),
            "median_predicted_p_target": predicted,
            "realized_target_hit_rate": sum(hits) / len(hits),
            "realized_mfe_bps": _median([r["realized_mfe_bps"] for r in bucket]),
            "realized_mae_bps": _median([r["realized_mae_bps"] for r in bucket]),
            "realized_return_bps": _median([r["realized_return_bps"] for r in bucket]),
        })
    return out


def _median(values) -> float | None:
    ordered = sorted(v for v in values if v is not None)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


# ------------------------------------------------------------------ verdict


def classify_verdict(rows: list[dict[str, Any]], sweep: list[dict[str, Any]], costs: dict[str, Any]) -> dict[str, Any]:
    """Case A/B/C classification from measured numbers, not opinion."""
    if not rows:
        return {"case": "INDETERMINATE", "reason": "no candidates"}
    ratios = [r["observed_over_required"] for r in rows if r["observed_over_required"]]
    median_ratio = _median(ratios) or 0.0
    median_gross = _median([r["gross_bps"] for r in rows]) or 0.0
    median_pt = _median([r["p_target"] for r in rows]) or 0.0
    any_positive = any(cell["net_positive_count"] > 0 for cell in sweep)
    best = max(sweep, key=lambda c: c["median_net_bps"]) if sweep else None

    if median_ratio < 0.5:
        case = "CASE_B_OR_C"
        reason = (
            f"Observed p_target reaches only {median_ratio:.1%} of the level required to break even. "
            f"Gross median is {median_gross:+.1f}bps, so the opportunity is absent *before* the "
            f"{costs['configured']['total_bps']:.0f}bps cost stack is applied. This is a geometry/horizon "
            f"problem, not primarily a cost problem."
        )
    elif median_gross > costs["configured"]["total_bps"] * 0.8:
        case = "CASE_C"
        reason = (
            f"Gross median {median_gross:+.1f}bps approaches the {costs['configured']['total_bps']:.0f}bps cost "
            f"stack, so a real move is present but costs consume it."
        )
    else:
        case = "CASE_A"
        reason = "Observed target probability is near the required level; calibration is the binding issue."

    return {
        "case": case,
        "reason": reason,
        "median_observed_over_required_p_target": median_ratio,
        "median_predicted_p_target": median_pt,
        "median_gross_bps": median_gross,
        "any_geometry_with_positive_net": any_positive,
        "best_geometry": (
            {"horizon_minutes": best["horizon_minutes"], "rr": best["rr"],
             "median_net_bps": best["median_net_bps"]} if best else None
        ),
    }


# ----------------------------------------------------------------- entry


def run_quant_diagnostic(
    settings: LiveSettings, *, count: int = 60, history: str = HISTORY,
    eval_end: datetime | None = None, step_minutes: int = 4320, calibration_rows: int = 200,
) -> dict[str, Any]:
    frame = load_history(history)
    end = eval_end or _dt(frame["timestamp"].max() or 0)
    stamps = sample_timestamps(frame, count, end=end, step_minutes=step_minutes)

    rows, reasons = rejection_reasons(frame, stamps, settings)
    sweep = geometry_sweep(frame, stamps[:20], settings)
    costs = cost_decomposition(rows, frame)

    cal_stamps = sample_timestamps(frame, calibration_rows, end=end, step_minutes=step_minutes)
    cal_rows = [diagnose_candidate(frame, at, settings) for at in cal_stamps]
    cal_rows = [r for r in cal_rows if r]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_candidates": len(rows),
        "policy_rejection_histogram": dict(reasons),
        "policy_eligible": sum(1 for r in rows if r["policy_eligible"]),
        "economics_per_candidate": rows,
        "required_vs_observed": {
            "median_ratio": _median([r["observed_over_required"] for r in rows if r["observed_over_required"]]),
            "min_ratio": min((r["observed_over_required"] for r in rows if r["observed_over_required"]), default=None),
            "max_ratio": max((r["observed_over_required"] for r in rows if r["observed_over_required"]), default=None),
        },
        "geometry_sweep": sweep,
        "cost_decomposition": costs,
        "calibration": calibration_table(cal_rows),
        "calibration_sample_size": len(cal_rows),
        "verdict": classify_verdict(rows, sweep, costs),
        "execution": "NOT_INVOKED",
    }
