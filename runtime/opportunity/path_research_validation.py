"""Validation for AI PATH_RESEARCH structured output."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_STATUS = {"COMPLETED", "NEEDS_EVIDENCE", "UNRESOLVED"}
REQUIRED_FIELDS = (
    "path_result_id",
    "task_id",
    "trigger_key",
    "bond_code",
    "bond_name",
    "path_id",
    "research_cutoff",
    "path_research_canonical_path",
    "knowledge_commit_sha",
    "review_ready",
    "research_status",
    "fact_spine",
    "judgments",
    "key_evidence",
    "unknown_a",
    "unknown_b",
    "key_risks",
    "failure_conditions",
    "next_update_nodes",
    "economic_status_at_research",
    "economic_judgment_reference",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _fail(run_dir: Path, errors: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    payload = {
        "status": "FAIL",
        "validated_at": _now(),
        "errors": errors,
        "warnings": warnings or [],
    }
    _write_json(run_dir / "path_research_validation.json", payload)
    return payload


def validate_path_research_result(
    run_dir: Path,
    result_file: Path,
) -> dict[str, Any]:
    input_path = run_dir / "ai_input.json"
    if not input_path.exists() or not result_file.exists():
        missing = [
            p.name for p in (input_path, result_file) if not p.exists()
        ]
        return _fail(run_dir, [f"missing required file(s): {', '.join(missing)}"])

    request = _read_json(input_path)
    result = _read_json(result_file)
    task = request.get("path_research_task") or {}
    errors: list[str] = []
    warnings: list[str] = []

    missing = [key for key in REQUIRED_FIELDS if key not in result]
    if missing:
        errors.append("Path Result missing fields: " + ", ".join(missing))

    identity_pairs = [
        ("task_id", task.get("task_id")),
        ("trigger_key", task.get("trigger_key")),
        ("bond_code", str(task.get("bond_code") or "").zfill(6)),
        ("bond_name", task.get("bond_name")),
        ("path_id", task.get("path_id")),
        ("path_research_canonical_path", task.get("path_research_canonical_path")),
        ("knowledge_commit_sha", task.get("knowledge_commit_sha")),
    ]
    for key, expected in identity_pairs:
        actual = result.get(key)
        if key == "bond_code" and actual is not None:
            actual = str(actual).zfill(6)
        if actual != expected:
            errors.append(f"{key} mismatch: expected={expected!r}, actual={actual!r}")

    expected_result_id = request.get("expected_path_result_id")
    if result.get("path_result_id") != expected_result_id:
        errors.append("path_result_id does not match expected deterministic id")

    cutoff = str(request.get("research_cutoff") or "")
    if str(result.get("research_cutoff") or "") != cutoff:
        errors.append("research_cutoff mismatch")

    if result.get("economic_status_at_research") != "KEEP":
        errors.append("economic_status_at_research must remain KEEP")

    status = result.get("research_status")
    if status not in ALLOWED_STATUS:
        errors.append(f"invalid research_status={status!r}")

    review_ready = result.get("review_ready")
    if not isinstance(review_ready, bool):
        errors.append("review_ready must be boolean")
    elif review_ready and status != "COMPLETED":
        errors.append("review_ready=true requires research_status=COMPLETED")

    unknown_a = result.get("unknown_a")
    unknown_b = result.get("unknown_b")
    for key, value in (("unknown_a", unknown_a), ("unknown_b", unknown_b)):
        if not isinstance(value, list):
            errors.append(f"{key} must be a list")

    if review_ready and isinstance(unknown_b, list) and unknown_b:
        errors.append("review_ready=true cannot contain material UNKNOWN-B")

    for key in ("fact_spine", "judgments", "economic_judgment_reference"):
        if not isinstance(result.get(key), dict):
            errors.append(f"{key} must be an object")

    for key in ("key_risks", "failure_conditions", "next_update_nodes"):
        if not isinstance(result.get(key), list):
            errors.append(f"{key} must be a list")

    evidence = result.get("key_evidence")
    if not isinstance(evidence, list):
        errors.append("key_evidence must be a list")
        evidence = []

    fetched_manifest_path = run_dir / "semantic_evidence_manifest.json"
    fetched_map: dict[str, dict[str, Any]] = {}
    if fetched_manifest_path.exists():
        manifest = _read_json(fetched_manifest_path)
        fetched_map = {
            str(item.get("evidence_id")): item
            for item in manifest.get("evidence", [])
            if item.get("evidence_id")
        }

    seen_ids: set[str] = set()
    for idx, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"key_evidence[{idx}] must be an object")
            continue
        for field in (
            "evidence_id",
            "source_type",
            "title",
            "source_date",
            "locator",
            "supports",
            "confidence",
        ):
            if field not in item:
                errors.append(f"key_evidence[{idx}] missing {field}")
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id:
            if evidence_id in seen_ids:
                errors.append(f"duplicate evidence_id={evidence_id}")
            seen_ids.add(evidence_id)

        if evidence_id in fetched_map:
            fetched = fetched_map[evidence_id]
            locator = str(item.get("locator") or "")
            valid = {
                str(fetched.get("pdf_url") or ""),
                str(fetched.get("detail_url") or ""),
            }
            valid.discard("")
            if locator and locator not in valid:
                errors.append(
                    f"evidence_id={evidence_id} locator does not match fetched evidence"
                )
        elif evidence_id and not evidence_id.startswith("PRELOADED:"):
            warnings.append(
                f"evidence_id={evidence_id} is not a fetched evidence id or PRELOADED id"
            )

    if errors:
        return _fail(run_dir, errors, warnings)

    normalized = run_dir / "path_research_result.json"
    if result_file.resolve() != normalized.resolve():
        normalized.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    validation = {
        "status": "PASS",
        "validated_at": _now(),
        "task_id": task.get("task_id"),
        "path_result_id": result.get("path_result_id"),
        "bond_code": str(task.get("bond_code") or "").zfill(6),
        "path_id": task.get("path_id"),
        "research_status": status,
        "review_ready": review_ready,
        "evidence_count": len(evidence),
        "unknown_a_count": len(unknown_a or []),
        "unknown_b_count": len(unknown_b or []),
        "warnings": warnings,
        "result_ref": "path_research_result.json",
    }
    _write_json(run_dir / "path_research_validation.json", validation)
    return validation
