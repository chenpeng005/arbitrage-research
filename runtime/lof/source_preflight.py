from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .runtime_session import LofRuntimeSession
from .szse_relay import DEFAULT_SZSE_RELAY_BASE_URL
from .szse_relay import fetch_szse_relay_bundle


PREFLIGHT_VERSION = "lof-production-source-preflight-v1"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    status: str
    hard: bool
    value: float | int | str | None
    threshold: str | None = None
    detail: str | None = None


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _check_min_ratio(
    *,
    name: str,
    numerator: int,
    denominator: int,
    threshold: float,
    hard: bool,
) -> PreflightCheck:
    value = _ratio(numerator, denominator)
    return PreflightCheck(
        name=name,
        status="PASS" if value >= threshold else "FAIL",
        hard=hard,
        value=round(value, 6),
        threshold=f">={threshold:.0%}",
        detail=f"{numerator}/{denominator}",
    )


def evaluate_preflight(
    *,
    snapshot: dict[str, Any],
    r1_context_resolved: int,
    r1_context_unresolved: int,
    expect_fresh_quotes: bool,
    application_commit_sha: str | None,
    checked_at: datetime,
    source_transport: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = snapshot.get("rows") or []
    universe_count = int(snapshot.get("universe_count") or 0)
    quality = snapshot.get("quality_summary") or {}
    lane_errors = dict(snapshot.get("lane_errors") or {})

    quote_fresh = int(quality.get("quote_fresh_count") or 0)
    quote_stale = int(quality.get("quote_stale_count") or 0)
    quote_unavailable = int(quality.get("quote_unavailable_count") or 0)
    quote_available = quote_fresh + quote_stale

    nav_available = int(quality.get("official_nav_available_count") or 0)
    state_available = int(quality.get("state_available_count") or 0)
    estimated_available = int(
        quality.get("estimated_nav_available_count") or 0
    )
    estimated_stale = int(quality.get("estimated_nav_stale_count") or 0)
    estimated_unavailable = int(
        quality.get("estimated_nav_unavailable_count") or 0
    )

    r1_total = r1_context_resolved + r1_context_unresolved
    source_transport = dict(source_transport or {})

    checks: list[PreflightCheck] = [
        PreflightCheck(
            name="universe_sanity",
            status="PASS" if universe_count >= 300 else "FAIL",
            hard=True,
            value=universe_count,
            threshold=">=300",
        ),
        PreflightCheck(
            name="snapshot_row_invariant",
            status="PASS" if len(rows) == universe_count else "FAIL",
            hard=True,
            value=len(rows),
            threshold=f"=={universe_count}",
        ),
        _check_min_ratio(
            name="quote_available_coverage",
            numerator=quote_available,
            denominator=universe_count,
            threshold=0.95,
            hard=True,
        ),
        _check_min_ratio(
            name="official_nav_coverage",
            numerator=nav_available,
            denominator=universe_count,
            threshold=0.90,
            hard=True,
        ),
        _check_min_ratio(
            name="trade_state_coverage",
            numerator=state_available,
            denominator=universe_count,
            threshold=0.90,
            hard=True,
        ),
        PreflightCheck(
            name="estimated_nav_nonzero",
            status=(
                "PASS"
                if (
                    estimated_available > 0
                    if expect_fresh_quotes
                    else (estimated_available + estimated_stale) > 0
                )
                else "FAIL"
            ),
            hard=True,
            value=(
                estimated_available
                if expect_fresh_quotes
                else estimated_available + estimated_stale
            ),
            threshold=(
                "available>0"
                if expect_fresh_quotes
                else "available+stale>0"
            ),
            detail=(
                f"available={estimated_available};stale={estimated_stale}"
            ),
        ),
    ]

    if expect_fresh_quotes:
        checks.append(
            _check_min_ratio(
                name="quote_fresh_coverage",
                numerator=quote_fresh,
                denominator=universe_count,
                threshold=0.95,
                hard=True,
            )
        )
    else:
        checks.append(
            PreflightCheck(
                name="quote_fresh_coverage",
                status="SKIP",
                hard=False,
                value=round(_ratio(quote_fresh, universe_count), 6),
                threshold=">=95% when --expect-fresh-quotes",
                detail="freshness not required for this preflight run",
            )
        )

    if r1_total > 0:
        checks.append(
            _check_min_ratio(
                name="r1_proxy_mapping_coverage",
                numerator=r1_context_resolved,
                denominator=r1_total,
                threshold=0.80,
                hard=True,
            )
        )
    else:
        checks.append(
            PreflightCheck(
                name="r1_proxy_mapping_coverage",
                status="FAIL",
                hard=True,
                value=0,
                threshold=">=80%",
                detail="no R1 context was built",
            )
        )

    relay_fetched_at = source_transport.get("relay_fetched_at")
    if relay_fetched_at:
        try:
            relay_time = datetime.fromisoformat(str(relay_fetched_at))
            if relay_time.tzinfo is None:
                relay_time = relay_time.replace(tzinfo=checked_at.tzinfo)
            age_hours = max(
                0.0,
                (checked_at - relay_time.astimezone(checked_at.tzinfo)).total_seconds()
                / 3600,
            )
            checks.append(
                PreflightCheck(
                    name="szse_relay_freshness",
                    status="PASS" if age_hours <= 96 else "FAIL",
                    hard=True,
                    value=round(age_hours, 3),
                    threshold="<=96h",
                )
            )
        except Exception:
            checks.append(
                PreflightCheck(
                    name="szse_relay_freshness",
                    status="FAIL",
                    hard=True,
                    value=str(relay_fetched_at),
                    threshold="valid ISO timestamp and <=96h",
                )
            )

    critical_lane_names = {"quote", "official_nav", "trade_state"}
    critical_lane_errors = {
        key: value
        for key, value in lane_errors.items()
        if key in critical_lane_names
    }
    optional_lane_errors = {
        key: value
        for key, value in lane_errors.items()
        if key not in critical_lane_names
    }

    if source_transport:
        universe_transport = source_transport.get(
            "szse_universe_transport"
        )
        nav_transport = source_transport.get("szse_nav_transport")
        allowed = {"DIRECT_OFFICIAL", "OFFICIAL_RELAY"}
        checks.append(
            PreflightCheck(
                name="szse_transport",
                status=(
                    "PASS"
                    if universe_transport in allowed
                    and nav_transport in allowed
                    else "FAIL"
                ),
                hard=True,
                value=(
                    f"universe={universe_transport};"
                    f"nav={nav_transport}"
                ),
                threshold=(
                    "each in DIRECT_OFFICIAL|OFFICIAL_RELAY"
                ),
                detail=(
                    f"fetched_at={source_transport.get('relay_fetched_at')};"
                    f"manifest_sha256="
                    f"{source_transport.get('relay_manifest_sha256')}"
                    if (
                        universe_transport == "OFFICIAL_RELAY"
                        or nav_transport == "OFFICIAL_RELAY"
                    )
                    else None
                ),
            )
        )

    checks.append(
        PreflightCheck(
            name="critical_lane_errors",
            status="PASS" if not critical_lane_errors else "FAIL",
            hard=True,
            value=len(critical_lane_errors),
            threshold="==0",
            detail=(
                None
                if not critical_lane_errors
                else json.dumps(critical_lane_errors, ensure_ascii=False)
            ),
        )
    )

    hard_failures = [
        asdict(check)
        for check in checks
        if check.hard and check.status == "FAIL"
    ]

    warnings: list[str] = []
    if optional_lane_errors:
        warnings.append(
            "optional_lane_errors="
            + json.dumps(optional_lane_errors, ensure_ascii=False)
        )
    if snapshot.get("collector_status") == "DEGRADED" and not lane_errors:
        warnings.append("collector_status=DEGRADED without lane_errors")

    if hard_failures:
        status = "FAIL"
    elif warnings:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "preflight_version": PREFLIGHT_VERSION,
        "status": status,
        "checked_at": checked_at.isoformat(),
        "application_commit_sha": application_commit_sha,
        "collector_status": snapshot.get("collector_status"),
        "metrics": {
            "universe_count": universe_count,
            "row_count": len(rows),
            "quote_available_count": quote_available,
            "quote_fresh_count": quote_fresh,
            "quote_stale_count": quote_stale,
            "quote_unavailable_count": quote_unavailable,
            "official_nav_available_count": nav_available,
            "state_available_count": state_available,
            "estimated_nav_available_count": estimated_available,
            "estimated_nav_stale_count": estimated_stale,
            "estimated_nav_unavailable_count": estimated_unavailable,
            "r1_context_resolved": r1_context_resolved,
            "r1_context_unresolved": r1_context_unresolved,
        },
        "checks": [asdict(check) for check in checks],
        "warnings": warnings,
        "errors": hard_failures,
        "lane_errors": lane_errors,
        "source_transport": source_transport,
    }


def run_production_source_preflight(
    *,
    as_of: datetime | None = None,
    timeout: int = 20,
    max_quote_age_seconds: int = 180,
    expect_fresh_quotes: bool = False,
    application_commit_sha: str | None = None,
    szse_relay_base_url: str | None = DEFAULT_SZSE_RELAY_BASE_URL,
) -> dict[str, Any]:
    now = as_of or datetime.now(SHANGHAI_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI_TZ)

    try:
        session = LofRuntimeSession.build(
            as_of=now,
            timeout=timeout,
            szse_relay_base_url=szse_relay_base_url,
        )
        snapshot = session.collect(
            generated_at=now,
            market_cutoff=now,
            max_quote_age_seconds=max_quote_age_seconds,
            timeout=timeout,
            snapshot_id="production-source-preflight",
        )
        szse_codes = {
            row.code for row in session.universe if row.exchange == "SZSE"
        }
        universe_relay = any(
            row.exchange == "SZSE"
            and row.source == "SZSE_OFFICIAL_RELAY"
            for row in session.universe
        )
        nav_relay = any(
            row.get("code") in szse_codes
            and row.get("official_nav_source") == "SZSE_OFFICIAL_RELAY"
            for row in (snapshot.get("rows") or [])
        )
        source_transport: dict[str, Any] = {
            "szse_universe_transport": (
                "OFFICIAL_RELAY" if universe_relay else "DIRECT_OFFICIAL"
            ),
            "szse_nav_transport": (
                "OFFICIAL_RELAY" if nav_relay else "DIRECT_OFFICIAL"
            ),
        }
        bundle = session.szse_relay_bundle
        if bundle is not None and (universe_relay or nav_relay):
            source_transport.update(
                {
                    "relay_base_url": bundle.base_url,
                    "relay_fetched_at": bundle.fetched_at,
                    "relay_manifest_sha256": bundle.manifest_sha256,
                }
            )

        return evaluate_preflight(
            snapshot=snapshot,
            r1_context_resolved=session.context_build.r1_resolved_count,
            r1_context_unresolved=session.context_build.r1_unresolved_count,
            expect_fresh_quotes=expect_fresh_quotes,
            application_commit_sha=application_commit_sha,
            checked_at=now,
            source_transport=source_transport,
        )
    except Exception as exc:
        return {
            "preflight_version": PREFLIGHT_VERSION,
            "status": "FAIL",
            "checked_at": now.isoformat(),
            "application_commit_sha": application_commit_sha,
            "collector_status": None,
            "metrics": {},
            "checks": [],
            "warnings": [],
            "errors": [
                {
                    "name": "runtime_exception",
                    "status": "FAIL",
                    "hard": True,
                    "value": None,
                    "threshold": None,
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            ],
            "lane_errors": {},
            "source_transport": {},
        }


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run LOF production live-source preflight."
    )
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-quote-age-seconds", type=int, default=180)
    parser.add_argument(
        "--expect-fresh-quotes",
        action="store_true",
        help="Require >=95% fresh quote coverage (use during market hours).",
    )
    parser.add_argument(
        "--application-commit-sha",
        default=os.environ.get("APPLICATION_COMMIT_SHA"),
    )
    parser.add_argument(
        "--szse-relay-base-url",
        default=os.environ.get(
            "LOF_SZSE_RELAY_BASE_URL",
            DEFAULT_SZSE_RELAY_BASE_URL,
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON audit output path.",
    )
    args = parser.parse_args(argv)

    result = run_production_source_preflight(
        timeout=args.timeout,
        max_quote_age_seconds=args.max_quote_age_seconds,
        expect_fresh_quotes=args.expect_fresh_quotes,
        application_commit_sha=args.application_commit_sha,
        szse_relay_base_url=args.szse_relay_base_url,
    )

    if args.output:
        _write_result(Path(args.output), result)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"PASS", "WARN"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
