"""Phase 3 pre-registration and experiment-provenance contract tests."""
from __future__ import annotations

import json
from pathlib import Path

from jev_trading.labels.barriers import HORIZONS, SL_BPS, TP_BPS, barrier_grid

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "experiment_id",
    "hypothesis",
    "data_source",
    "data_range",
    "feature_set",
    "target",
    "model",
    "seed",
    "train_period",
    "validation_period",
    "purge",
    "cost_model",
    "threshold",
    "metrics",
    "trade_count",
    "result",
    "decision",
}


def test_config_freezes_grid_splits_costs_and_no_2025_selection():
    config = json.loads((ROOT / "configs/phase3.json").read_text())
    assert tuple(config["barriers"]["take_profit_bps"]) == TP_BPS
    assert tuple(config["barriers"]["stop_loss_bps"]) == SL_BPS
    assert tuple(config["barriers"]["horizons_minutes"]) == HORIZONS
    assert len(barrier_grid()) == 100
    assert config["data"]["allowed_end_exclusive_ms"] == 1735689600000
    assert config["data"]["historically_consumed_2025_plus_allowed_for_selection"] is False
    assert config["splits"]["embargo_minutes"] == 60
    assert config["model"]["ensemble_uncertainty_used_as_economic_penalty"] is False
    assert config["economics"]["minimum_edge_over_cost"] == 2.0


def test_every_execute_phase3_experiment_has_required_metadata():
    for number in range(20, 33):
        matches = list((ROOT / "experiments").glob(f"EXP-{number:03d}-*/metrics.json"))
        assert len(matches) == 1, f"EXP-{number:03d} must have exactly one canonical metrics file"
        record = json.loads(matches[0].read_text())
        assert REQUIRED <= set(record), f"EXP-{number:03d} missing {sorted(REQUIRED - set(record))}"
        assert record["decision"]
        if record["decision"].startswith("NOT RUN"):
            assert record["data_source"].startswith("NOT RUN")
        else:
            assert record["data_source"] == "artifacts/phase0-baseline-final-d/pre_oos_2021_2024.parquet"


def test_target_shape_artifact_contains_each_pre_registered_cell_once():
    import csv

    path = ROOT / "experiments/EXP-023-target-shape-sweep/target-shape.csv"
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 100
    assert len({row["config_id"] for row in rows}) == 100
    assert {int(row["tp_bps"]) for row in rows} == set(TP_BPS)
    assert {int(row["sl_bps"]) for row in rows} == set(SL_BPS)
    assert {int(row["horizon_minutes"]) for row in rows} == set(HORIZONS)


def test_phase3_registry_and_provenance_cover_all_ids():
    registry = json.loads((ROOT / "experiments/phase3-registry.json").read_text())
    provenance = json.loads((ROOT / "experiments/phase3-provenance.json").read_text())
    verification = json.loads((ROOT / "experiments/phase3-verification.json").read_text())
    ids = [row["experiment_id"] for row in registry["experiments"]]
    assert ids == [f"EXP-{number:03d}" for number in range(20, 33)]
    assert provenance["source"]["2025_plus_used_for_selection"] is False
    assert provenance["phase2_frozen_status"] == "NO EDGE"
    assert verification["data_integrity"]["pre_2025_only"] is True
    assert verification["economic_integrity"]["discrete_funding"] is True
    assert verification["research_integrity"]["frontier_laya_jev_rl_live_modified"] is False
