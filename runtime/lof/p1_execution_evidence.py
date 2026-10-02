from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REGISTRY_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "p1_execution_evidence_v0_1.json"
)


def load_execution_evidence(
    path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    source = Path(path) if path is not None else REGISTRY_PATH
    if not source.is_file():
        return {}
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("rows") or {}
    return {
        str(code): dict(value)
        for code, value in rows.items()
        if isinstance(value, dict)
    }


def match_execution_evidence(
    row: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    if not evidence:
        return "NOT_FOUND", None

    status = str(row.get("subscription_status") or "UNKNOWN")
    if status != "LIMITED":
        return "NOT_APPLICABLE_STATUS", evidence

    try:
        current_limit = float(row.get("daily_subscription_limit"))
        evidence_limit = float(evidence.get("current_limit_reference"))
    except (TypeError, ValueError):
        return "LIMIT_UNAVAILABLE", evidence

    tolerance = max(0.01, abs(evidence_limit) * 1e-9)
    if abs(current_limit - evidence_limit) > tolerance:
        return "LIMIT_MISMATCH", evidence

    return "MATCHED", evidence
