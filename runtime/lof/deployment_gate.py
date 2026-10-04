from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from runtime.deployment_gate import (
    DeploymentGateError,
    validate_deployment_gate,
)


LOF_RELEASE_PROFILE = "lof-opportunity-runtime-v1"

LOF_REQUIRED_CANONICAL_PATHS = (
    "05 套利研究/LOF机会发现/00_当前有效/00_恢复入口.md",
    "05 套利研究/LOF机会发现/00_当前有效/01_LOF机会发现_Canonical.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Market-Snapshot-Contract-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Data-Source-Baseline-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Web-View-Baseline-V0.1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Realtime-Estimated-NAV-Resolver-Classification-V0.1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Estimated-NAV-Resolver-Contract-V0.1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Estimate-Validation-Depth-Execution-V0.1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-R2-Main-Promotion-V0.1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Estimated-NAV-Persistence-Governance-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Estimated-NAV-Freshness-Audit-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Proxy-Resolution-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-FX-CNH-Fallback-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Cloud-Smoke-vs-Production-Preflight-V0.1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Production-Source-Preflight-Contract-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-Release-Profile-V1.md",
    "05 套利研究/LOF机会发现/02_数据与监控/LOF-SZSE-Official-Relay-V1.md",
)


def validate_lof_deployment_gate(
    *,
    data_root: Path,
    deployment: dict | None = None,
    write_audit: bool = True,
) -> dict:
    return validate_deployment_gate(
        data_root=data_root,
        deployment=deployment,
        write_audit=write_audit,
        required_canonical_paths=LOF_REQUIRED_CANONICAL_PATHS,
        expected_release_profile=LOF_RELEASE_PROFILE,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate LOF production deployment gate."
    )
    parser.add_argument(
        "--data-root",
        default=os.environ.get("LOF_RUNTIME_DATA_ROOT", "runtime_data/lof"),
    )
    args = parser.parse_args(argv)

    try:
        result = validate_lof_deployment_gate(
            data_root=Path(args.data_root),
            write_audit=True,
        )
    except DeploymentGateError as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "release_profile": LOF_RELEASE_PROFILE,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
