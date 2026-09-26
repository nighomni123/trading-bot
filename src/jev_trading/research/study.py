"""Stage 12 state-conditional study with Benjamini-Hochberg FDR.

Long horizons overlap heavily at 1m sampling (72h = 4320x), so inference is run
on a NON-OVERLAPPING decision grid per horizon, and the number of tests is
reported so FDR is meaningful. No effect is selected; everything is reported.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .data import HORIZONS
from .state import STATE_COLUMNS

#: Pre-registered two-way combinations (Phase 20). Deliberately small.
STATE_COMBINATIONS = (
    ("TREND_STATE",),
    ("VOL_STATE",),
    ("RANGE_STATE",),
    ("OI_STATE",),
    ("FUNDING_STATE",),
    ("PRICE_LOCATION_STATE",),
    ("TREND_STATE", "VOL_STATE"),
    ("TREND_STATE", "OI_STATE"),
    ("VOL_STATE", "OI_STATE"),
    ("FUNDING_STATE", "OI_STATE"),
)

MIN_SAMPLES = 30


def _welch(a: np.ndarray, b: np.ndarray) -> dict:
    if a.size < MIN_SAMPLES or b.size < MIN_SAMPLES:
        return {"diff": None, "t": None, "n_cond": int(a.size), "n_uncond": int(b.size)}
    diff = a.mean() - b.mean()
    se = np.sqrt(a.var(ddof=1) / a.size + b.var(ddof=1) / b.size)
    t = diff / se if se > 0 else 0.0
    # Two-sided p from the t distribution via a normal approximation (large n).
    from math import erfc, sqrt
    p = erfc(abs(t) / sqrt(2))
    return {"diff": float(diff), "t": float(t), "p": float(p),
            "n_cond": int(a.size), "n_uncond": int(b.size)}


def benjamini_hochberg(pvalues: list[float], q: float = 0.10) -> list[float]:
    """Return BH-adjusted q-values in the input order."""
    finite = [(i, p) for i, p in enumerate(pvalues) if p is not None and not np.isnan(p)]
    m = len(finite)
    if m == 0:
        return [float("nan")] * len(pvalues)
    order = sorted(finite, key=lambda t: t[1])
    adj = [float("nan")] * len(pvalues)
    running = 1.0
    for rank in range(m, 0, -1):
        idx, p = order[rank - 1]
        running = min(running, p * m / rank)
        adj[idx] = running
    return adj


def state_study(frame: pl.DataFrame, *, horizons: dict[str, int] | None = None,
                combinations=STATE_COMBINATIONS) -> dict:
    """Conditional vs unconditional forward return for each state/horizon.

    `frame` must already be on a non-overlapping grid for the given horizon;
    the caller passes `non_overlapping_grid` output. We slice per horizon.
    """
    horizons = horizons or HORIZONS
    rows = []
    for hname, _ in horizons.items():
        col = f"forward_return_{hname}"
        if col not in frame.columns:
            continue
        ret = frame[col].to_numpy()
        finite = np.isfinite(ret)
        for combo in combinations:
            if not all(c in frame.columns for c in combo):
                continue
            key = "+".join(combo)
            for level in sorted({v for v in frame[combo[0]].drop_nulls().to_list()}, key=str):
                mask = np.ones(frame.height, dtype=bool)
                for c in combo:
                    mask &= (frame[c] == level).to_numpy()
                cond = ret[finite & mask]
                uncond = ret[finite & ~mask]
                test = _welch(cond, uncond)
                if test["diff"] is None:
                    continue
                rows.append({
                    "horizon": hname, "state": key, "level": str(level),
                    "diff_bps": test["diff"] * 1e4, "t": test["t"], "p": test.get("p"),
                    "n_cond": test["n_cond"], "n_uncond": test["n_uncond"],
                    "cond_mean_bps": float(cond.mean() * 1e4),
                    "uncond_mean_bps": float(uncond.mean() * 1e4),
                })
    # FDR across all tested hypotheses (exploratory discovery).
    pvals = [r["p"] for r in rows]
    qvals = benjamini_hochberg(pvals, q=0.10)
    for r, q in zip(rows, qvals):
        r["q_bh"] = q
    n_tests = len(rows)
    n_sig = sum(1 for r in rows if r.get("q_bh") is not None and not np.isnan(r["q_bh"]) and r["q_bh"] < 0.10)
    return {"n_hypotheses_tested": n_tests, "n_significant_fdr10": n_sig, "rows": rows}
