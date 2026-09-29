from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .deployment_gate import LOF_RELEASE_PROFILE


_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _validate_sha(name: str, value: str) -> str:
    value = value.strip()
    if not _SHA_RE.fullmatch(value):
        raise ValueError(f"{name} must be a 40-character git SHA")
    return value.lower()


def prepare_lof_deployment_candidate(
    *,
    data_root: Path,
    application_commit_sha: str,
    knowledge_commit_sha: str,
    deployment_method: str = "release-preparation",
) -> tuple[Path, dict[str, Any]]:
    application_sha = _validate_sha(
        "application_commit_sha",
        application_commit_sha,
    )
    knowledge_sha = _validate_sha(
        "knowledge_commit_sha",
        knowledge_commit_sha,
    )

    snapshot_manifest_path = (
        data_root
        / "knowledge_snapshots"
        / knowledge_sha
        / "manifest.json"
    )
    if not snapshot_manifest_path.is_file():
        raise FileNotFoundError(
            f"knowledge snapshot manifest missing: {snapshot_manifest_path}"
        )

    snapshot_manifest = _read_json(snapshot_manifest_path)
    if snapshot_manifest.get("knowledge_commit_sha") != knowledge_sha:
        raise ValueError("snapshot manifest knowledge commit mismatch")
    if snapshot_manifest.get("release_profile") != LOF_RELEASE_PROFILE:
        raise ValueError("snapshot manifest LOF release profile mismatch")

    payload = {
        "release_profile": LOF_RELEASE_PROFILE,
        "application_commit_sha": application_sha,
        "knowledge_commit_sha": knowledge_sha,
        "knowledge_snapshot_manifest_sha256": _sha256(
            snapshot_manifest_path
        ),
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "deployment_method": deployment_method,
    }

    path = data_root / "deployment_manifest.candidate.json"
    _write_atomic(path, payload)
    return path, payload


def promote_lof_deployment_manifest(
    *,
    data_root: Path,
    preflight_result_path: Path,
    deployment_method: str,
) -> tuple[Path, dict[str, Any]]:
    candidate_path = data_root / "deployment_manifest.candidate.json"
    if not candidate_path.is_file():
        raise FileNotFoundError(
            f"deployment candidate missing: {candidate_path}"
        )
    if not preflight_result_path.is_file():
        raise FileNotFoundError(
            f"preflight result missing: {preflight_result_path}"
        )

    candidate = _read_json(candidate_path)
    preflight = _read_json(preflight_result_path)

    if candidate.get("release_profile") != LOF_RELEASE_PROFILE:
        raise ValueError("candidate release_profile mismatch")

    status = str(preflight.get("status") or "")
    if status not in {"PASS", "WARN"}:
        raise ValueError(
            f"production source preflight not promotable: {status or 'MISSING'}"
        )

    candidate_app_sha = _validate_sha(
        "candidate application_commit_sha",
        str(candidate.get("application_commit_sha") or ""),
    )
    preflight_app_sha = _validate_sha(
        "preflight application_commit_sha",
        str(preflight.get("application_commit_sha") or ""),
    )
    if candidate_app_sha != preflight_app_sha:
        raise ValueError(
            "preflight application commit does not match deployment candidate"
        )

    knowledge_sha = _validate_sha(
        "candidate knowledge_commit_sha",
        str(candidate.get("knowledge_commit_sha") or ""),
    )
    snapshot_manifest_path = (
        data_root
        / "knowledge_snapshots"
        / knowledge_sha
        / "manifest.json"
    )
    if not snapshot_manifest_path.is_file():
        raise FileNotFoundError(
            f"knowledge snapshot manifest missing: {snapshot_manifest_path}"
        )

    current_snapshot_hash = _sha256(snapshot_manifest_path)
    expected_snapshot_hash = str(
        candidate.get("knowledge_snapshot_manifest_sha256") or ""
    )
    if current_snapshot_hash != expected_snapshot_hash:
        raise ValueError(
            "knowledge snapshot manifest changed after candidate preparation"
        )

    payload = dict(candidate)
    payload.update(
        {
            "source_preflight_status": status,
            "source_preflight_checked_at": preflight.get("checked_at"),
            "source_preflight_version": preflight.get("preflight_version"),
            "deployed_at": datetime.now(timezone.utc).isoformat(),
            "deployment_method": deployment_method,
        }
    )

    path = data_root / "deployment_manifest.json"
    _write_atomic(path, payload)
    return path, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or promote LOF deployment manifest."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--data-root", required=True)
    prepare.add_argument("--application-commit-sha", required=True)
    prepare.add_argument("--knowledge-commit-sha", required=True)
    prepare.add_argument(
        "--deployment-method",
        default="release-preparation",
    )

    promote = sub.add_parser("promote")
    promote.add_argument("--data-root", required=True)
    promote.add_argument("--preflight-result", required=True)
    promote.add_argument("--deployment-method", required=True)

    args = parser.parse_args(argv)

    if args.command == "prepare":
        path, payload = prepare_lof_deployment_candidate(
            data_root=Path(args.data_root),
            application_commit_sha=args.application_commit_sha,
            knowledge_commit_sha=args.knowledge_commit_sha,
            deployment_method=args.deployment_method,
        )
    else:
        path, payload = promote_lof_deployment_manifest(
            data_root=Path(args.data_root),
            preflight_result_path=Path(args.preflight_result),
            deployment_method=args.deployment_method,
        )

    print(
        json.dumps(
            {
                "status": "PASS",
                "path": str(path),
                "manifest": payload,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
