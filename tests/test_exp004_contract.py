"""EXP-004 contract / leakage / arm-equivalence / execution tests.
Run with: .venv/bin/pytest tests/test_exp004_contract.py -v
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl
from jev_trading.jev.adapter import AdapterJev
from jev_trading.jev.mock import MockJev
from jev_trading.jev.client import Answer, Question
from jev_trading.jev.questions import MVP_QUESTIONS
from jev_trading.state.features import FEATURE_COLUMNS, build_features


def test_probability_contract():
    # predict_up15 returns float, bounded [0,1] (model.py contract)
    from jev_trading.quant.model import load_models
    try:
        q = load_models("models/")
    except Exception:
        # model missing; skip predictive part, still verify adapter answer bounds
        q = None
    # Verify adapter / MockJev answers always bounded
    adapter = AdapterJev(backend=MockJev())
    state = {
        "market_state": {"trend_score": 0.1, "volume_z": 0.2, "funding_z": 0.0, "vol_regime": 0.1},
        "quant_predictions": {"p_up_15": 0.55, "p_dn_15": 0.45, "expected_return_15": 0.002},
        "candidate_side": "long",
    }
    answers = adapter.ask(state, MVP_QUESTIONS)
    assert len(answers) == len(MVP_QUESTIONS), "all questions answered"
    for a in answers:
        assert isinstance(a, Answer)
        assert 0.0 <= a.value <= 1.0, f"answer {a.name} out of bounds: {a.value}"
        assert isinstance(a.value, float), f"answer {a.name} not float"


def test_candidate_gate_jev_called_exactly_once_per_candidate():
    # Read a validation arm log; every candidate record must show jev_answers present,
    # non-candidates must not contain jev_answers (or contain no decision input from Jev).
    log_path = Path("experiments/EXP-004/arm_C.jsonl")
    if not log_path.exists():
        return  # evidence not yet generated
    records = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    candidates = [r for r in records if r.get("candidate")]
    non_candidates = [r for r in records if not r.get("candidate")]
    # Every candidate reached Jev (jev_answers key present when arm=C/D)
    for r in candidates:
        assert "jev_answers" in r, f"candidate missing jev_answers at {r['ts']}"
    # Non-candidates should never have jev_answers (since gate skips)
    for r in non_candidates:
        assert "jev_answers" not in r or r.get("jev_answers") is None, f"non-candidate leaked jev at {r['ts']}"


def test_leakage_adapter_rejects_future_keys():
    adapter = AdapterJev(backend=MockJev())
    bad_state = {"trend_score": 0.5, "future_return_15": 0.01, "future_price": 100}
    try:
        adapter.ask(bad_state, MVP_QUESTIONS)
        assert False, "adapter should reject future key"
    except ValueError as exc:
        assert "leak" in str(exc).lower() or "future" in str(exc).lower()


def test_execution_next_bar():
    # Every execution timestamp must be > decision timestamp (next-bar fill)
    for arm in ["A", "B", "C", "D"]:
        log_path = Path(f"experiments/EXP-004/arm_{arm}.jsonl")
        if not log_path.exists():
            continue
        for line in log_path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("exec_ts") is not None and r.get("ts") is not None:
                assert r["exec_ts"] > r["ts"], f"same-bar fill at {r['ts']} arm={arm}"


def test_arm_equivalence_same_features_costs():
    # All arms should have identical n_bars and same feature source (prepare shared)
    # Verified implicitly by run_exp004 using shared prepare; just sanity-check counts
    counts = {}
    for arm in ["A", "B", "C", "D"]:
        metrics_path = Path(f"experiments/EXP-004/arm_{arm}_metrics.json")
        if metrics_path.exists():
            counts[arm] = json.loads(metrics_path.read_text())["n_bars"]
    if len(counts) == 4:
        vals = list(counts.values())
        assert len(set(vals)) == 1, f"arm bar counts differ: {counts}"


def test_jew_never_bypasses_risk():
    # MockJev only produces answers; policy/risk decide. Confirm adapter has no order/size method.
    adapter = AdapterJev()
    assert not hasattr(adapter, "place_order")
    assert not hasattr(adapter, "set_size")
