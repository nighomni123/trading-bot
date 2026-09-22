#!/usr/bin/env python3
"""jev-trading dashboard — Streamlit UI for monitoring the project.

Run: .venv/bin/streamlit run scripts/dashboard.py
Or:  .venv/bin/python scripts/dashboard.py  (CLI mode if streamlit not installed)

Pages:
  Status      — Phase overview, gate verdict, test count
  Experiments — List all experiments with verdicts and metrics
  Frontier    — Latest strategy artifact parameters and rationale
  Regime      — Current market regime from live data
  Backtest    — Run ablation simulations with parameter tuning
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on path when run via `streamlit run scripts/dashboard.py`
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    import streamlit as st
except ImportError:
    st = None

from jev_trading.dashboard.core import (
    PHASES,
    GATES,
    load_experiments,
    load_model_metrics,
    load_frontier_artifacts,
    get_test_status,
    load_latest_bars,
    run_backtest,
)


def _status_text(status: str) -> str:
    """Map status string to emoji prefix."""
    s = status.upper()
    if s.startswith("DONE"):
        return "✅ " + status
    if "FAIL" in s:
        return "🟥 " + status
    if "BLOCKED" in s:
        return "⬜ " + status
    if s.startswith("MOCK") or "PARTIAL" in s or "MOCK" in s:
        return "🟡 " + status
    return "🟡 " + status


def page_status():
    st.header("Project Status")
    st.caption("jev-trading — frontier model + Jev + deterministic risk")

    cols = st.columns(2)
    with cols[0]:
        passed, failed = get_test_status()
        st.metric("Tests", f"{passed} passed" + (f" / {failed} failed" if failed else ""))
    with cols[1]:
        metrics = load_model_metrics()
        if metrics:
            auc = metrics.get("metrics", {}).get("lgbm_auc")
            if auc is not None:
                st.metric("Model AUC (valid)", f"{auc:.3f}")

    st.subheader("Phase Status (P0 → P9)")
    for phase, status in PHASES:
        st.markdown(f"**{phase}**" + f" — {_status_text(status)}")

    st.markdown("---")
    st.subheader("Research Gates")
    for gate_id, info in GATES.items():
        color = "green" if info["verdict"] == "PASS" else "red"
        st.markdown(
            f"**{gate_id}**: {info['question']} → "
            f":{color}[{info['verdict']}] — {info['detail']}"
        )

    bars = load_latest_bars()
    if bars is not None:
        st.subheader("Data Summary")
        n = bars.height
        date_range = f"{bars['timestamp'].min()} → {bars['timestamp'].max()}"
        st.write(f"- Rows: {n:,}")

    exp_count = len(load_experiments())
    fa_count = len(load_frontier_artifacts())
    st.subheader("Artifacts")
    st.write(f"- Experiments: {exp_count}")
    st.write(f"- Frontier strategy artifacts: {fa_count}")


def page_experiments():
    st.header("Experiments")
    experiments = load_experiments()

    if not experiments:
        st.info("No experiments found in experiments/EXP-*")
        return

    rows = []
    for exp in experiments:
        row = {
            "Experiment": exp.get("exp_id", "?"),
            "Question": exp.get("experiment_id", "?"),
        }
        verdict_key = "verdict"
        verdict_val = None
        for k, v in exp.items():
            if "verdict" in k.lower():
                verdict_val = v
                break
        if verdict_val is None:
            verdict_val = exp.get("verdict", "—")
        row["Verdict"] = verdict_val
        metrics = exp.get("results_base_fee1x", {})
        rows.append(row)

    import polars as pl

    df = pl.DataFrame(rows)
    st.dataframe(df, use_container_width=True)

    selected = st.selectbox(
        "Select experiment for details",
        options=[e.get("exp_id", "?") for e in experiments],
    )
    if selected:
        exp = next(e for e in experiments if e.get("exp_id") == selected)
        st.subheader(f"Details: {selected}")
        st.json({k: v for k, v in exp.items() if k != "by_year"})


def page_frontier():
    st.header("Frontier Strategy Artifacts")
    artifacts = load_frontier_artifacts()

    if not artifacts:
        st.info("No frontier_strategy.yaml found in experiments/EXP-*")
        st.markdown(
            "Run the frontier strategist first: "
            "`python scripts/run_frontier.py` (TODO)"
        )
        return

    selected = st.selectbox(
        "Select experiment",
        options=list(artifacts.keys()),
    )
    artifact = artifacts[selected]
    st.subheader(f"Strategy Artifact: {selected}")

    meta = artifact.get("metadata", {})
    if meta:
        st.markdown(f"**Hypothesis:** {meta.get('hypothesis', '—')}")
        st.markdown(f"**Regime target:** {meta.get('regime_target', '—')}")
        st.markdown(f"**Expected impact:** {meta.get('expected_impact', '—')}")
        rationale = meta.get("rationale", [])
        if rationale:
            st.write("**Rationale:**")
            for r in rationale:
                st.markdown(f"- {r}")

    quant = artifact.get("quant", {})
    jev = artifact.get("jev", {})
    policy = artifact.get("policy", {})

    col1, col2 = st.columns(2)
    with col1:
        st.subsubheader = st.subheader("Quant Parameters")
        st.json(quant)
    with col2:
        st.subheader("Jev Parameters")
        st.json(jev)

    st.subheader("Policy Thresholds")
    st.json(policy)


def page_regime():
    st.header("Market Regime")

    from jev_trading.state.features import build_features, build_state_dict
    from jev_trading.frontier.world_model import classify_regime

    bars = load_latest_bars()
    if bars is None:
        st.warning("No data found at data/btcusdt_1m.parquet")
        st.info("Run `python scripts/fetch_data.py` to generate bar data.")
        return

    features = build_features(bars)
    latest_row = features.row(-1, named=True)
    state = build_state_dict(latest_row)
    regime = classify_regime(state)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Volatility", regime.volatility)
    with col2:
        st.metric("Trend", regime.trend)
    with col3:
        st.metric("Funding", regime.funding)
    with col4:
        st.metric("Duration (bars)", str(regime.duration_bars))

    st.subheader("State features")
    state_display = {k: round(v, 6) if isinstance(v, float) else v for k, v in state.items() if k != "ts"}
    st.json(state_display)


def page_backtest():
    st.header("Backtest Runner (P7 Ablation)")

    st.sidebar.header("Parameters")
    start = st.sidebar.text_input("Start date", value="2024-01-01")
    end = st.sidebar.text_input("End date", value="2025-01-01")
    arms = st.sidebar.text_input("Arms (comma-separated)", value="random,threshold,policy,full,nojev")
    fee_mult = st.sidebar.text_input("Fee multipliers (comma-separated)", value="1.0")
    p_thr = st.sidebar.slider("P threshold", 0.0, 1.0, 0.40, 0.01)
    seed = st.sidebar.number_input("Seed", value=7)
    stride = st.sidebar.number_input("Stride (dev only; 1 = normal)", value=1, min_value=1)

    if st.sidebar.button("Run Backtest", type="primary"):
        with st.spinner("Running ablation..."):
            results = run_backtest(
                start=start,
                end=end,
                arms=arms,
                fee_mult=fee_mult,
                p_thr=p_thr,
                seed=seed,
                stride=stride,
            )

        if not results:
            st.error("No results — check data/models exist.")
            return

        import polars as pl

        rows = []
        for r in results:
            rows.append({
                "Arm": r.get("arm", "?"),
                "Entries": r.get("n_entries", 0),
                "Win Rate": f"{r.get('win_rate', 0):.1%}" if r.get("win_rate") else "—",
                "Net PnL": f"${r.get('net_pnl', 0):,.2f}",
                "Return %": f"{r.get('return_pct', 0):.2f}%",
                "Max DD %": f"{r.get('max_dd_pct', 0):.2f}%",
                "Fees": f"${r.get('fees', 0):.2f}",
                "Fee Mult": r.get("fee_mult", 1.0),
                "P Thr": r.get("p_thr", 0.40),
            })

        df = pl.DataFrame(rows)
        st.dataframe(df, use_container_width=True)

        st.subheader("Per-year breakdown")
        for r in results:
            if "by_year" in r:
                st.write(f"**{r.get('arm', '?')} (fee_mult={r.get('fee_mult', 1.0)})**")
                import polars as pl2
                yr_rows = []
                for yr, m in r["by_year"].items():
                    yr_rows.append({
                        "Year": yr,
                        "Bars": m["n_bars"],
                        "Net PnL": f"${m['net_pnl']:,.2f}",
                        "Entries": m["n_entries"],
                        "Exits": m["n_exits"],
                        "Max DD %": f"{m['max_dd_pct']:.2f}%",
                    })
                st.dataframe(pl2.DataFrame(yr_rows), use_container_width=True)
    else:
        st.info("Set parameters in the sidebar and click **Run Backtest**.")


PAGES = {
    "Status": page_status,
    "Experiments": page_experiments,
    "Frontier": page_frontier,
    "Regime": page_regime,
    "Backtest": page_backtest,
}


def main():
    if st is None:
        print("Streamlit not installed. Install with: pip install streamlit")
        print("Or use CLI mode: python scripts/dashboard.py status")
        return

    st.set_page_config(
        page_title="jev-trading Dashboard",
        page_icon="📊",
        layout="wide",
    )

    with st.sidebar:
        st.title("jev-trading")
        st.markdown("---")
        page = st.radio("Navigation", list(PAGES.keys()), index=0)

    PAGES[page]()


if __name__ == "__main__":
    main()
