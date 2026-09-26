"""Research registry and economic scorecard tests."""
from __future__ import annotations

import pytest

from jev_trading.microstructure.economics import (
    STAGE10_MEASURED_COST_BPS, economic_scorecard, scorecard_from_study,
)
from jev_trading.microstructure.registry import (
    new_hypothesis, set_status, write_hypothesis, ROOT,
)


@pytest.fixture(autouse=True)
def _tmp_registry(tmp_path, monkeypatch):
    monkeypatch.setattr("jev_trading.microstructure.registry.ROOT", tmp_path / "alphas")


def test_hypothesis_cannot_be_created_as_validated():
    with pytest.raises(ValueError):
        new_hypothesis("A0", "n", "d", features=[], event_definition={}, targets=[], horizons=[5],
                       status="VALIDATED")


def test_hypothesis_roundtrip_and_status_requires_justification():
    h = new_hypothesis("EXP-11-FLOW", "flow reversal", "test", features=["volume_imbalance_5m"],
                       event_definition={"event": "EVENT_FLOW_REVERSAL"},
                       targets=["forward_return_30m"], horizons=[30])
    write_hypothesis(h)
    updated = set_status("EXP-11-FLOW", "PROMISING", justification="event study t>3, needs fresh validation")
    assert updated["status"] == "PROMISING"
    with pytest.raises(ValueError):
        set_status("EXP-11-FLOW", "FALSIFIED", justification="   ")


def test_scorecard_applies_cost_and_flags_viability():
    good = economic_scorecard("E", 20.0, 100, cost_bps=11.0)
    assert good["net_expected_bps"] == pytest.approx(9.0)
    assert good["economically_viable"] is True
    bad = economic_scorecard("E", 1.0, 100, cost_bps=11.0)
    assert bad["economically_viable"] is False
    assert bad["cost_multiple"] == pytest.approx(11.0)


def test_scorecard_carries_stage10_provenance_and_never_edits_config():
    from jev_trading.microstructure.economics import STAGE10_COST_PROVENANCE
    study = {"events": {"E": {"n_events": 100, "by_horizon": {"30m": {
        "test": {"diff": 0.002}, "conditional": {}, "unconditional": {}}}}}}
    card = scorecard_from_study(study)
    assert card["cost_provenance"]["measured_at_cost_bps"] == STAGE10_MEASURED_COST_BPS
    assert card["n_rows"] == 1
    # Config is untouched by importing/using the scorecard.
    import json, pathlib
    cfg = json.loads(pathlib.Path("configs/live.json").read_text())
    assert cfg["costs"]["slippage_bps_per_side"] == 2.0  # unchanged
