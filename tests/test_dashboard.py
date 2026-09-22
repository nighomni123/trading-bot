"""Tests for the dashboard core logic."""

import json
from pathlib import Path

import pytest

from jev_trading.dashboard.core import (
    PHASES,
    GATES,
    get_phase_status,
    get_gate_verdict,
    load_experiments,
    load_model_metrics,
    load_frontier_artifacts,
    get_test_status,
    load_latest_bars,
    _parse_yaml,
)


def test_phase_status_has_all_phases():
    phases = get_phase_status()
    assert len(phases) >= 9  # P0 through P9
    assert phases[0][0] == "P0 Bootstrap"
    assert any("P9" in p[0] for p in phases)


def test_gate_verdict_has_p4_and_p7():
    gates = get_gate_verdict()
    assert "P4" in gates
    assert "P7" in gates
    assert gates["P4"]["verdict"] == "PASS"
    assert gates["P7"]["verdict"] == "FAIL"


def test_load_experiments_returns_list():
    experiments = load_experiments()
    assert isinstance(experiments, list)
    assert len(experiments) >= 1  # at least EXP-001


def test_load_experiments_has_exp_ids():
    experiments = load_experiments()
    ids = [e["exp_id"] for e in experiments]
    assert "EXP-001" in ids
    assert "EXP-002" in ids
    assert "EXP-003" in ids


def test_load_experiments_has_verdict():
    experiments = load_experiments()
    exp003 = next((e for e in experiments if e["exp_id"] == "EXP-003"), None)
    assert exp003 is not None
    assert exp003.get("verdict", "").upper().startswith("FAIL") or "FAIL" in str(exp003.get("verdict", ""))


def test_load_frontier_artifacts_returns_dict():
    artifacts = load_frontier_artifacts()
    assert isinstance(artifacts, dict)


def test_load_model_metrics_returns_dict_or_none():
    metrics = load_model_metrics()
    if metrics is not None:
        assert isinstance(metrics, dict)
        assert "metrics" in metrics
        assert "lgbm_auc" in metrics["metrics"]


def test_load_latest_bars_returns_dataframe_or_none():
    bars = load_latest_bars()
    # May be None if data file doesn't exist (gitignored)
    if bars is not None:
        assert "timestamp" in bars.columns
        assert "close" in bars.columns


def test_parse_yaml_with_json_content(tmp_path):
    f = tmp_path / "test.json"
    f.write_text(json.dumps({"key": "value"}))
    result = _parse_yaml(f)
    assert result == {"key": "value"}


def test_get_test_status_returns_tuple():
    result = get_test_status()
    assert isinstance(result, tuple)
    assert len(result) == 2
    assert all(isinstance(x, int) for x in result)
    assert result[0] >= 0  # passed count


def test_phases_contain_frontier():
    phases = get_phase_status()
    assert any("Frontier" in p[0] for p in phases)


def test_phases_contain_shadow():
    phases = get_phase_status()
    assert any("Shadow" in p[0] for p in phases)


def test_gates_contain_both():
    gates = get_gate_verdict()
    assert set(gates.keys()) >= {"P4", "P7"}


def test_experiments_have_required_fields():
    experiments = load_experiments()
    for exp in experiments:
        assert "exp_id" in exp
