"""Stage 12 research plots. Every plot answers a stated research question.

No decorative charts. If a plot cannot be produced from real results, it is not
created.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

PLOTS = Path("research/stage12/plots")
H_ORDER = ["1h", "2h", "4h", "8h", "12h", "24h", "48h", "72h"]
COST_BPS = 11.006


def _save(fig, name: str) -> str:
    PLOTS.mkdir(parents=True, exist_ok=True)
    path = PLOTS / name
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def plot_edge_vs_cost(study: dict, path: str = "edge_vs_cost.png") -> str:
    """THE key chart: max |conditional edge| per horizon vs execution cost.

    Question: does any horizon produce a predictable move that beats the cost?
    """
    xs, ys = [], []
    for h in H_ORDER:
        block = study.get(h)
        if not block:
            continue
        diffs = [abs(r["diff_bps"]) for r in block["rows"]]
        if diffs:
            xs.append(H_ORDER.index(h))
            ys.append(max(diffs))
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(xs, ys, "o-", color="#2b6cb0", label="max |conditional edge| (bps)")
    ax.axhline(COST_BPS, color="#c53030", linestyle="--", label=f"measured cost {COST_BPS:.2f} bps")
    ax.set_xticks(xs); ax.set_xticklabels([H_ORDER[i] for i in xs])
    ax.set_xlabel("horizon"); ax.set_ylabel("bps")
    ax.set_title("Predictable edge vs execution cost by horizon")
    ax.legend(); ax.grid(alpha=0.3)
    return _save(fig, path)


def plot_state_effects(study: dict, horizon: str = "24h", top: int = 12, path: str = "state_effects.png") -> str:
    """Question: which market states show the largest conditional return?"""
    rows = sorted(study.get(horizon, {}).get("rows", []), key=lambda r: -abs(r["diff_bps"]))[:top]
    if not rows:
        return ""
    labels = [f"{r['state']}={r['level']}" for r in rows][::-1]
    vals = [r["diff_bps"] for r in rows][::-1]
    errs = [abs(r["diff_bps"]) / max(abs(r["t"]), 1e-9) for r in rows][::-1]
    fig, ax = plt.subplots(figsize=(9, max(3, 0.35 * len(rows))))
    colors = ["#2b6cb0" if v > 0 else "#c53030" for v in vals]
    ax.barh(labels, vals, xerr=errs, color=colors, alpha=0.85)
    ax.axvline(0, color="k", lw=0.8)
    ax.axvline(COST_BPS, color="#c53030", ls="--", lw=1)
    ax.axvline(-COST_BPS, color="#c53030", ls="--", lw=1)
    ax.set_xlabel(f"conditional - unconditional return (bps), horizon {horizon}")
    ax.set_title("Conditional return by market state (dashed = execution cost)")
    ax.grid(alpha=0.3, axis="x")
    return _save(fig, path)


def plot_year_stability(stability: dict, key: str, path: str = "year_stability.png") -> str:
    """Question: is the effect present in independent years, or one lucky year?"""
    series = stability.get(key)
    if not series:
        return ""
    years = sorted(series)
    diffs = [series[y].get("diff_bps") for y in years]
    good = [d for d in diffs if d is not None]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(years, [d if d is not None else 0 for d in diffs],
           color=["#2f855a" if (d or 0) > 0 else "#c53030" for d in diffs])
    ax.axhline(0, color="k", lw=0.8)
    ax.axhline(COST_BPS, color="#c53030", ls="--", lw=1, label=f"cost {COST_BPS:.1f} bps")
    ax.set_ylabel("conditional - unconditional (bps)")
    ax.set_title(f"Year stability: {key}")
    ax.legend(); ax.grid(alpha=0.3, axis="y")
    return _save(fig, path)


def plot_decile(wf: dict, horizon: str, path: str = "decile.png") -> str:
    """Question: does predicted rank track realized return out of sample?"""
    folds = wf.get(horizon, {}).get("folds", [])
    fold = folds[-1] if folds else None
    if not fold or "decile_means_bps" not in fold:
        return ""
    means = fold["decile_means_bps"]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.bar(range(1, len(means) + 1), means, color="#2b6cb0", alpha=0.85)
    ax.set_xlabel("predicted decile (1 = lowest)"); ax.set_ylabel("realized mean return (bps)")
    ax.set_title(f"Predicted vs realized by decile, {horizon} fold {fold['fold']}")
    ax.grid(alpha=0.3, axis="y")
    return _save(fig, path)


def plot_tail_probability(joined_stats: dict, path: str = "tail_probability.png") -> str:
    """Question: how does the probability of a large move scale with horizon?"""
    horizons = list(joined_stats.keys())
    p20 = [joined_stats[h].get("p_up_20bps", np.nan) for h in horizons]
    p100 = [joined_stats[h].get("p_up_100bps", np.nan) for h in horizons]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(range(len(horizons)), p20, "o-", label="P(> +20 bps)")
    ax.plot(range(len(horizons)), p100, "o-", label="P(> +100 bps)")
    ax.set_xticks(range(len(horizons))); ax.set_xticklabels(horizons)
    ax.set_xlabel("horizon"); ax.set_ylabel("probability")
    ax.set_title("Probability of an upward move exceeding a threshold")
    ax.legend(); ax.grid(alpha=0.3)
    return _save(fig, path)


def build_all(study: dict, stability: dict, wf: dict, tail: dict) -> dict:
    out = {
        "edge_vs_cost": plot_edge_vs_cost(study),
        "state_effects_24h": plot_state_effects(study, "24h"),
        "year_stability_trend_up_24h": plot_year_stability(stability, "TREND_STATE=UP@24h"),
        "decile_24h": plot_decile(wf, "24h"),
        "tail_probability": plot_tail_probability(tail),
    }
    return {k: v for k, v in out.items() if v}
