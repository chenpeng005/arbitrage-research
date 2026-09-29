"""Production deployment preflight for Runtime + Knowledge Snapshot consistency."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GATE_VERSION = "runtime-deployment-gate-v1"

REQUIRED_CANONICAL_PATHS = (
    "05 套利研究/AI-Engineering-Runtime/05_Path Knowledge/01_到期现金兑付/02_Path-Research.md",
    "05 套利研究/AI-Engineering-Runtime/05_Path Knowledge/03_回售/03_Path-Research.md",
    "05 套利研究/AI-Engineering-Runtime/05_Path Knowledge/02_下修/02_Path-Research.md",
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/Path-Research/Path-Research-Task-Contract-V2.md",
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/Path-Research/Path-Research-Evidence-Pack-V2.md",
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/Path-Research/Path-Research-Result-Contract-V2.md",
    "05 套利研究/AI-Engineering-Runtime/04_数据与状态系统/Daily-Incremental-Information-Lane-V1.md",
)


class DeploymentGateError(RuntimeError):
    """Raised when production deployment state is not internally consistent."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_audit(data_root: Path, payload: dict[str, Any]) -> None:
    path = data_root / "deployment" / "latest_gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def validate_deployment_gate(
    *,
    data_root: Path,
    deployment: dict[str, Any] | None = None,
    write_audit: bool = True,
    required_canonical_paths: tuple[str, ...] | list[str] | None = None,
    expected_release_profile: str | None = None,
) -> dict[str, Any]:
    """Fail closed unless deployment manifest and Knowledge Snapshot are one release.

    The default canonical set remains the existing convertible-bond Runtime
    profile. Other applications may inject their own required canonical paths
    without changing the default production behavior.
    """

    manifest_path = data_root / "deployment_manifest.json"
    if deployment is None:
        if not manifest_path.exists():
            raise DeploymentGateError(f"deployment manifest missing: {manifest_path}")
        deployment = _read_json(manifest_path)

    knowledge_sha = str(deployment.get("knowledge_commit_sha") or "").strip()
    application_sha = str(deployment.get("application_commit_sha") or "").strip()
    release_profile = str(deployment.get("release_profile") or "").strip()
    required_paths = tuple(
        required_canonical_paths
        if required_canonical_paths is not None
        else REQUIRED_CANONICAL_PATHS
    )
    expected_snapshot_hash = str(
        deployment.get("knowledge_snapshot_manifest_sha256") or ""
    ).strip()

    try:
        if not application_sha:
            raise DeploymentGateError("deployment manifest has no application_commit_sha")
        if not knowledge_sha:
            raise DeploymentGateError("deployment manifest has no knowledge_commit_sha")
        if not expected_snapshot_hash:
            raise DeploymentGateError(
                "deployment manifest has no knowledge_snapshot_manifest_sha256"
            )
        if expected_release_profile is not None:
            if release_profile != expected_release_profile:
                raise DeploymentGateError(
                    "deployment manifest release_profile does not match "
                    f"expected profile: {expected_release_profile}"
                )
        if not required_paths:
            raise DeploymentGateError("required canonical path set is empty")

        snapshot_dir = data_root / "knowledge_snapshots" / knowledge_sha
        snapshot_manifest_path = snapshot_dir / "manifest.json"
        if not snapshot_manifest_path.exists():
            raise DeploymentGateError(
                f"knowledge snapshot missing for deployment commit: {knowledge_sha}"
            )

        actual_snapshot_hash = _sha256(snapshot_manifest_path)
        if actual_snapshot_hash != expected_snapshot_hash:
            raise DeploymentGateError(
                "knowledge snapshot manifest hash does not match deployment manifest"
            )

        snapshot_manifest = _read_json(snapshot_manifest_path)
        if snapshot_manifest.get("knowledge_commit_sha") != knowledge_sha:
            raise DeploymentGateError(
                "knowledge snapshot manifest commit does not match deployment manifest"
            )

        files = snapshot_manifest.get("files")
        if not isinstance(files, list) or not files:
            raise DeploymentGateError("knowledge snapshot manifest has no files")

        by_canonical: dict[str, dict[str, Any]] = {}
        snapshot_root = snapshot_dir.resolve()
        for item in files:
            canonical_path = str(item.get("canonical_path") or "")
            local_file = str(item.get("local_file") or "")
            expected_hash = str(item.get("sha256") or "")
            if not canonical_path or not local_file or not expected_hash:
                raise DeploymentGateError("knowledge snapshot contains incomplete file metadata")
            if canonical_path in by_canonical:
                raise DeploymentGateError(
                    f"duplicate canonical path in knowledge snapshot: {canonical_path}"
                )
            local_path = (snapshot_dir / local_file).resolve()
            try:
                local_path.relative_to(snapshot_root)
            except ValueError as exc:
                raise DeploymentGateError(
                    f"knowledge snapshot local_file escapes snapshot directory: {local_file}"
                ) from exc
            if not local_path.exists():
                raise DeploymentGateError(
                    f"knowledge snapshot file missing: {canonical_path}"
                )
            actual_hash = _sha256(local_path)
            if actual_hash != expected_hash:
                raise DeploymentGateError(
                    f"knowledge snapshot file sha256 mismatch: {canonical_path}"
                )
            expected_size = item.get("byte_size")
            if expected_size is not None and local_path.stat().st_size != int(expected_size):
                raise DeploymentGateError(
                    f"knowledge snapshot file byte_size mismatch: {canonical_path}"
                )
            by_canonical[canonical_path] = item

        missing = [
            path for path in required_paths if path not in by_canonical
        ]
        if missing:
            raise DeploymentGateError(
                "knowledge snapshot missing required canonical paths: "
                + "; ".join(missing)
            )

        result = {
            "gate_version": GATE_VERSION,
            "status": "PASS",
            "checked_at": _now(),
            "application_commit_sha": application_sha,
            "knowledge_commit_sha": knowledge_sha,
            "knowledge_snapshot_manifest_sha256": actual_snapshot_hash,
            "snapshot_file_count": len(files),
            "required_canonical_count": len(required_paths),
            "release_profile": release_profile or None,
        }
        if write_audit:
            _write_audit(data_root, result)
        return result
    except Exception as exc:
        result = {
            "gate_version": GATE_VERSION,
            "status": "FAIL",
            "checked_at": _now(),
            "application_commit_sha": application_sha or None,
            "knowledge_commit_sha": knowledge_sha or None,
            "release_profile": release_profile or None,
            "error": f"{type(exc).__name__}: {exc}",
        }
        if write_audit:
            _write_audit(data_root, result)
        if isinstance(exc, DeploymentGateError):
            raise
        raise DeploymentGateError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate production deployment gate")
    parser.add_argument(
        "--data-root",
        default=os.environ.get("RUNTIME_DATA_ROOT", "runtime_data"),
    )
    args = parser.parse_args(argv)
    data_root = Path(args.data_root)
    try:
        result = validate_deployment_gate(data_root=data_root, write_audit=True)
    except DeploymentGateError as exc:
        print(
            json.dumps(
                {
                    "gate_version": GATE_VERSION,
                    "status": "FAIL",
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
