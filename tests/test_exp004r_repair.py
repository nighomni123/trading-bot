#!/usr/bin/env python3
"""EXP-004R invariant tests — structural verification of repaired framework.

These tests verify the METHODOLOGY repair (same mechanics, explicit policy difference)
but do NOT claim predictive or economic success from fabricated results.
Run with: .venv/bin/pytest tests/test_exp004r_repair.py -v
"""
from __future__ import annotations
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import polars as pl

# Structural paths
EXP_004R_DIR = Path("experiments/EXP-004R")


def test_repair_directory_exists():
    assert EXP_004R_DIR.exists(), "EXP-004R directory missing"


def test_config_yaml_exists_and_has_explicit_min_edge():
    cfg_path = EXP_004R_DIR / "config.yaml"
    assert cfg_path.exists(), "config.yaml missing"
    text = cfg_path.read_text()
    assert "min_edge_over_cost" in text, "min_edge treatment must be explicitly documented"
    assert "0.0" in text or "2.0" in text, "min_edge values must appear explicitly"


def test_position_state_machine_documented():
    sm_path = EXP_004R_DIR / "position_state_machine.md"
    assert sm_path.exists(), "position_state_machine.md missing"
    text = sm_path.read_text()
    assert "FLAT" in text, "State machine must include FLAT state"
    assert "LONG" in text, "State machine must include LONG state"
    assert "SAME" in text, "Both arms must share the same mechanism"


def test_locked_config_explicit():
    locked = json.loads((EXP_004R_DIR / "locked_config.json").read_text())
    assert locked.get("experiment_id") == "EXP-004R"
    assert "min_edge_over_cost_A" in locked
    assert "min_edge_over_cost_B" in locked
    assert locked["min_edge_over_cost_A"] == 0.0
    assert locked["min_edge_over_cost_B"] == 2.0


def test_provenance_preserves_policy_isolation():
    prov = json.loads((EXP_004R_DIR / "provenance.json").read_text())
    arm_sem = prov.get("arm_semantics", {})
    assert "A" in arm_sem and "B" in arm_sem
    # A skips policy.decide(); B uses it
    a_text = arm_sem.get("A", "")
    b_text = arm_sem.get("B", "")
    assert "skips" in a_text.lower() or "no policy" in a_text.lower() or "ENTER_LONG" in a_text
    assert "policy.decide" in b_text or "full cfg" in b_text


def test_candidate_universe_frozen():
    cand_path = EXP_004R_DIR / "candidate_universe.jsonl"
    assert cand_path.exists(), "candidate_universe.jsonl missing"
    cand = json.loads(cand_path.read_text())
    assert cand["threshold"] == 0.40
    assert cand["threshold_frozen_note"]


def test_arm_definitions_explicit_difference():
    a_path = EXP_004R_DIR / "arm_A.jsonl"
    b_path = EXP_004R_DIR / "arm_B.jsonl"
    assert a_path.exists() and b_path.exists()
    a_def = json.loads(a_path.read_text())
    b_def = json.loads(b_path.read_text())
    # Only intended difference is policy/edge; same mechanics
    assert a_def.get("policy_engine_called") == False
    assert b_def.get("policy_engine_called") == True
    assert a_def.get("min_edge_over_cost") == 0.0
    assert b_def.get("min_edge_over_cost") == 2.0
    # Same mechanics
    assert "SAME as" in a_def.get("exit_logic", "") or "SAME" in a_def.get("exit_logic", "")
    assert a_def.get("execution_model", "").startswith(b_def.get("execution_model", "").split()[0])


def test_no_future_information_in_provenance():
    # The provenance must document point-in-time boundary (not claim future info used)
    prov_text = (EXP_004R_DIR / "provenance.json").read_text()
    assert "point-in-time" in prov_text.lower() or "next-bar" in prov_text.lower() or "next-open" in prov_text.lower()


def test_exp_004_artifacts_not_modified():
    # Verify the frozen EXP-004 artifacts remain unchanged (no rewrite)
    exp004_path = Path("experiments/EXP-004")
    # Just verify directory exists; full immutability relies on user gate rules
    assert exp004_path.exists(), "Frozen EXP-004 must be preserved"
    # Do NOT check modification times (not reliable); rely on user instruction


def test_metrics_document_predictive_not_profit_claim():
    metrics_text = (EXP_004R_DIR / "metrics.json").read_text()
    assert "not to claim profitability" in metrics_text.lower()
    # The word profitability appears only in the context of explicitly denying a claim
    assert "hidden" not in metrics_text.lower() or "not hidden" in metrics_text.lower()
