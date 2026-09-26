"""Alpha hypothesis registry (Phase 28).

A hypothesis has a lifecycle status, but status is a DETERMINISTIC research
decision recorded by a human/experiment artifact. Nothing in the system may
promote itself to VALIDATED.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .schema import ALPHA_EXPERIMENT_VERSION, EVENT_DEFINITION_VERSION, FEATURE_SET_VERSION, TARGET_VERSION

Status = Literal["RESEARCH", "PROMISING", "FALSIFIED", "VALIDATED", "RETIRED"]
ROOT = Path("research/alphas")

#: Statuses a deterministic artifact may set automatically. VALIDATED is
#: deliberately absent: promotion is a human research decision.
AUTO_STATUSES = {"RESEARCH", "FALSIFIED", "PROMISING"}


def hypothesis_path(alpha_id: str) -> Path:
    return ROOT / "hypotheses" / f"{alpha_id}.json"


def result_path(alpha_id: str) -> Path:
    return ROOT / "results" / f"{alpha_id}.json"


def new_hypothesis(alpha_id: str, name: str, description: str, *, features: list[str],
                   event_definition: dict[str, Any], targets: list[str], horizons: list[int],
                   status: Status = "RESEARCH") -> dict[str, Any]:
    if status not in AUTO_STATUSES | {"RETIRED"}:
        raise ValueError("hypothesis creation must not start as VALIDATED")
    return {
        "alpha_id": alpha_id,
        "name": name,
        "description": description,
        "features": features,
        "event_definition": event_definition,
        "targets": targets,
        "horizons": horizons,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_version": ALPHA_EXPERIMENT_VERSION,
        "feature_version": FEATURE_SET_VERSION,
        "event_version": EVENT_DEFINITION_VERSION,
        "target_version": TARGET_VERSION,
        "status": status,
    }


def write_hypothesis(hypothesis: dict[str, Any]) -> Path:
    path = hypothesis_path(hypothesis["alpha_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hypothesis, indent=2, sort_keys=True) + "\n")
    return path


def read_hypothesis(alpha_id: str) -> dict[str, Any]:
    return json.loads(hypothesis_path(alpha_id).read_text())


def list_hypotheses() -> list[dict[str, Any]]:
    directory = ROOT / "hypotheses"
    if not directory.exists():
        return []
    return [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]


def write_result(alpha_id: str, payload: dict[str, Any]) -> Path:
    path = result_path(alpha_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
    return path


def set_status(alpha_id: str, status: Status, *, justification: str) -> dict[str, Any]:
    """Status changes are recorded with a mandatory justification."""
    if status not in AUTO_STATUSES | {"VALIDATED", "RETIRED"}:
        raise ValueError(f"unknown status: {status}")
    if not justification.strip():
        raise ValueError("a status change requires a written justification")
    hypothesis = read_hypothesis(alpha_id)
    hypothesis["status"] = status
    hypothesis["status_justification"] = justification
    hypothesis["status_updated_at"] = datetime.now(timezone.utc).isoformat()
    write_hypothesis(hypothesis)
    return hypothesis
