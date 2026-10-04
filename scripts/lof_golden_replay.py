#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path
from typing import Any

from runtime.lof.resolver import ResolverInput, resolve_estimated_nav


REPLAY_VERSION = "LOF_GOLDEN_REPLAY_RUNNER_V1"
DEFAULT_FIXTURE = Path("tests/fixtures/lof_golden_replay_v1.json")


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(str(value))


def _resolver_input(case: dict) -> ResolverInput:
    raw = case["input"]
    return ResolverInput(
        fund_code=str(case["code"]),
        resolver_class=str(case["resolver_class"]),
        resolver_method=str(case["method"]),
        proxy_id=f"GOLDEN:{case['code']}",
        official_nav=_decimal(raw.get("official_nav")),
        proxy_anchor_value=_decimal(raw.get("proxy_anchor_value")),
        proxy_current_value=_decimal(raw.get("proxy_current_value")),
        proxy_anchor_time=_datetime(raw.get("proxy_anchor_time")),
        proxy_current_time=_datetime(raw.get("proxy_current_time")),
        as_of=_datetime(raw.get("as_of")),
        max_proxy_age_seconds=int(raw.get("max_proxy_age_seconds") or 0),
        exposure_ratio=_decimal(raw.get("exposure_ratio")),
        fx_anchor=_decimal(raw.get("fx_anchor")),
        fx_current=_decimal(raw.get("fx_current")),
        tracking_adjustment=(
            _decimal(raw.get("tracking_adjustment")) or Decimal("1")
        ),
        quality=str(raw.get("quality") or "UNKNOWN"),
        fx_current_time=_datetime(raw.get("fx_current_time")),
        fx_source=raw.get("fx_source"),
    )


def run_fixture(
    fixture_path: str | Path,
    *,
    domains: set[str] | None = None,
    tolerance: Decimal = Decimal("0.000000000001"),
) -> dict:
    path = Path(fixture_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases") or []
    selected = [
        case
        for case in cases
        if not domains or str(case.get("domain")) in domains
    ]

    failures: list[dict] = []
    passed = 0

    for case in selected:
        result = resolve_estimated_nav(_resolver_input(case))
        expected = case["expected"]
        expected_nav = _decimal(expected.get("estimated_nav"))

        reasons: list[str] = []
        if result.estimated_nav_status != expected.get("status"):
            reasons.append(
                "status "
                f"{result.estimated_nav_status} != {expected.get('status')}"
            )
        if result.estimated_nav_quality != expected.get("quality"):
            reasons.append(
                "quality "
                f"{result.estimated_nav_quality} != {expected.get('quality')}"
            )
        if expected_nav is None:
            if result.estimated_nav is not None:
                reasons.append(
                    f"expected nav=None got {result.estimated_nav}"
                )
        else:
            if result.estimated_nav is None:
                reasons.append("estimated nav unexpectedly None")
            elif abs(result.estimated_nav - expected_nav) > tolerance:
                reasons.append(
                    "nav diff "
                    f"{result.estimated_nav - expected_nav} "
                    f"> {tolerance}"
                )

        if reasons:
            failures.append(
                {
                    "code": case.get("code"),
                    "name": case.get("name"),
                    "domain": case.get("domain"),
                    "method": case.get("method"),
                    "reasons": reasons,
                    "actual": {
                        "estimated_nav": (
                            str(result.estimated_nav)
                            if result.estimated_nav is not None
                            else None
                        ),
                        "status": result.estimated_nav_status,
                        "quality": result.estimated_nav_quality,
                        "error": result.error,
                    },
                }
            )
        else:
            passed += 1

    return {
        "version": REPLAY_VERSION,
        "fixture_version": payload.get("version"),
        "fixture_path": str(path),
        "domains": sorted(domains or []),
        "case_count": len(selected),
        "passed": passed,
        "failed": len(failures),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic LOF golden resolver replay."
    )
    parser.add_argument(
        "--fixture",
        default=str(DEFAULT_FIXTURE),
    )
    parser.add_argument(
        "--domain",
        action="append",
        default=[],
        help="Optional replay domain filter, e.g. R1 / R3 / R5.",
    )
    args = parser.parse_args(argv)

    result = run_fixture(
        args.fixture,
        domains=set(args.domain) if args.domain else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
