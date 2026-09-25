"""Prepare a fail-closed AI input for one Path Research task."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

AI_INPUT_VERSION = "path-research-ai-input-v1"
TASK_CONTRACT_PATH = (
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/"
    "Path-Research/Path-Research-Task-Contract-V1.md"
)
EVIDENCE_CONTRACT_PATH = (
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/"
    "Path-Research/Path-Research-Evidence-Pack-V1.md"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_snapshot_text(
    snapshot_dir: Path,
    manifest: dict[str, Any],
    canonical_path: str,
) -> tuple[str, dict[str, Any]]:
    matches = [
        item for item in manifest.get("files", [])
        if item.get("canonical_path") == canonical_path
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"knowledge snapshot does not contain unique canonical path: {canonical_path}"
        )
    meta = matches[0]
    local = snapshot_dir / str(meta["local_file"])
    if not local.exists():
        raise FileNotFoundError(f"knowledge snapshot file missing: {local}")
    actual = _sha256(local)
    if actual != meta.get("sha256"):
        raise RuntimeError(
            f"knowledge snapshot sha256 mismatch for {canonical_path}"
        )
    return local.read_text(encoding="utf-8"), meta


def prepare_path_research_ai_input(
    task_id: str,
    data_root: Path,
) -> dict[str, Any]:
    task_path = data_root / "research_tasks" / f"{task_id}.json"
    evidence_path = data_root / "research_evidence" / f"{task_id}.json"
    if not task_path.exists():
        raise FileNotFoundError(f"research task not found: {task_path}")
    if not evidence_path.exists():
        raise FileNotFoundError(f"research evidence pack not found: {evidence_path}")

    task = _read_json(task_path)
    evidence = _read_json(evidence_path)

    if task.get("task_id") != task_id or evidence.get("task_id") != task_id:
        raise RuntimeError("task/evidence identity mismatch")
    if task.get("trigger_key") != evidence.get("trigger_key"):
        raise RuntimeError("task/evidence trigger_key mismatch")
    if task.get("path_id") != evidence.get("path_id"):
        raise RuntimeError("task/evidence path_id mismatch")
    if task.get("market_cutoff") != evidence.get("market_cutoff"):
        raise RuntimeError("task/evidence market_cutoff mismatch")

    knowledge_sha = str(task.get("knowledge_commit_sha") or "")
    if not knowledge_sha:
        raise RuntimeError("task has no knowledge_commit_sha")
    if evidence.get("knowledge_commit_sha") != knowledge_sha:
        raise RuntimeError(
            "task and evidence pack were not built from the same knowledge_commit_sha"
        )

    snapshot_dir = data_root / "knowledge_snapshots" / knowledge_sha
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"knowledge snapshot not deployed for task commit: {knowledge_sha}"
        )
    manifest = _read_json(manifest_path)
    if manifest.get("knowledge_commit_sha") != knowledge_sha:
        raise RuntimeError("knowledge snapshot manifest commit mismatch")

    path_canonical, path_meta = _load_snapshot_text(
        snapshot_dir,
        manifest,
        str(task["path_research_canonical_path"]),
    )
    task_contract, task_meta = _load_snapshot_text(
        snapshot_dir,
        manifest,
        TASK_CONTRACT_PATH,
    )
    evidence_contract, evidence_meta = _load_snapshot_text(
        snapshot_dir,
        manifest,
        EVIDENCE_CONTRACT_PATH,
    )

    work_dir = data_root / "path_research_work" / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    expected_result_id = f"pr_{task_id}"
    payload = {
        "task_type": "PATH_RESEARCH",
        "ai_input_version": AI_INPUT_VERSION,
        "created_at": _now(),
        "expected_path_result_id": expected_result_id,
        "research_cutoff": task["market_cutoff"],
        "path_research_task": task,
        "evidence_pack": evidence,
        "canonical_context": {
            "path_research_canonical": {
                "canonical_path": task["path_research_canonical_path"],
                "sha256": path_meta["sha256"],
                "text": path_canonical,
            },
            "task_contract": {
                "canonical_path": TASK_CONTRACT_PATH,
                "sha256": task_meta["sha256"],
                "text": task_contract,
            },
            "evidence_pack_contract": {
                "canonical_path": EVIDENCE_CONTRACT_PATH,
                "sha256": evidence_meta["sha256"],
                "text": evidence_contract,
            },
        },
        "knowledge_snapshot": {
            "knowledge_commit_sha": knowledge_sha,
            "manifest_ref": str(manifest_path),
            "source_repository": manifest.get("source_repository"),
        },
    }
    input_path = work_dir / "ai_input.json"
    _write_json(input_path, payload)

    descriptor = {
        "status": "PASS",
        "task_id": task_id,
        "path_id": task["path_id"],
        "bond_code": task["bond_code"],
        "knowledge_commit_sha": knowledge_sha,
        "expected_path_result_id": expected_result_id,
        "work_dir": str(work_dir),
        "input_path": str(input_path),
        "input_bytes": input_path.stat().st_size,
    }
    _write_json(work_dir / "input_build_result.json", descriptor)
    return descriptor
