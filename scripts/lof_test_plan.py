#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import shlex
from typing import Iterable


PLAN_VERSION = "LOF_TEST_IMPACT_V1"

DOMAIN_TESTS = {
    "ui": [
        "tests.test_lof_static_method_labels",
        "tests.test_lof_static_r2c_profile",
        "tests.test_lof_static_shadow_registry",
        "tests.test_lof_web_nav_freshness",
    ],
    "r1": [
        "tests.test_lof_domestic_index_resolver",
        "tests.test_lof_index_proxy",
        "tests.test_lof_mapping",
        "tests.test_lof_r1_component_fallback",
        "tests.test_lof_r1_pipeline",
        "tests.test_lof_csi_component_proxy",
    ],
    "r2": [
        "tests.test_lof_r2_promotion",
        "tests.test_lof_r2c_profile_summary",
        "tests.test_lof_shadow_registry",
    ],
    "r3_fx": [
        "tests.test_lof_fx_fallback",
        "tests.test_lof_qdii_index_resolver",
        "tests.test_lof_qdii_proxy_registry",
        "tests.test_lof_r3_hk_freshness",
        "tests.test_lof_r3_pipeline",
        "tests.test_lof_r3_pipeline_hk_audit",
        "tests.test_lof_r3_us_timing",
    ],
    "r5": [
        "tests.test_lof_commodity_quote",
        "tests.test_lof_commodity_resolver",
        "tests.test_lof_futures_overlay",
        "tests.test_lof_r5_164701_registry",
        "tests.test_lof_r5_pipeline",
        "tests.test_lof_r5_quality_registry",
    ],
    "storage": [
        "tests.test_lof_estimate_persistence",
        "tests.test_lof_estimate_validation",
        "tests.test_lof_snapshot_store",
        "tests.test_lof_snapshot",
    ],
    "freshness": [
        "tests.test_lof_estimate_freshness_audit",
        "tests.test_lof_estimate_reliability",
        "tests.test_lof_estimated_nav_lane",
    ],
    "collector": [
        "tests.test_lof_http_json",
        "tests.test_lof_nav",
        "tests.test_lof_quote",
        "tests.test_lof_source_preflight",
        "tests.test_lof_state",
        "tests.test_lof_szse_relay",
        "tests.test_lof_tracking_index",
        "tests.test_lof_universe",
    ],
    "runtime_core": [
        "tests.test_lof_context_builder",
        "tests.test_lof_controller",
        "tests.test_lof_estimated_nav_lane",
        "tests.test_lof_resolver",
        "tests.test_lof_runtime_loop",
        "tests.test_lof_runtime_session",
        "tests.test_lof_snapshot",
        "tests.test_lof_snapshot_job",
    ],
    "deployment": [
        "tests.test_lof_deployment_gate",
        "tests.test_lof_knowledge_snapshot",
        "tests.test_lof_release_manifest",
        "tests.test_deployment_gate",
    ],
    "test_governance": [
        "tests.test_lof_test_governance",
    ],
}

RULES = [
    (
        "ui",
        [
            "runtime/lof/static/**",
        ],
    ),
    (
        "r1",
        [
            "runtime/lof/r1_*.py",
            "runtime/lof/domestic_index_resolver.py",
            "runtime/lof/index_proxy.py",
            "runtime/lof/index_quote*.py",
            "runtime/lof/csi_*.py",
            "runtime/lof/mapping.py",
            "runtime/lof/tracking_index.py",
            "runtime/lof/data/*r1*",
            "runtime/lof/data/*index*",
        ],
    ),
    (
        "r2",
        [
            "runtime/lof/r2*.py",
            "runtime/lof/shadow_registry.py",
            "runtime/lof/data/*r2*",
        ],
    ),
    (
        "r3_fx",
        [
            "runtime/lof/r3_*.py",
            "runtime/lof/qdii_*.py",
            "runtime/lof/fx.py",
            "runtime/lof/fx_*.py",
            "runtime/lof/foreign_quote.py",
            "runtime/lof/hk_history.py",
            "runtime/lof/us_history.py",
            "runtime/lof/data/*qdii*",
            "runtime/lof/data/*fx*",
        ],
    ),
    (
        "r5",
        [
            "runtime/lof/r5_*.py",
            "runtime/lof/commodity_*.py",
            "runtime/lof/futures_*.py",
            "runtime/lof/data/*commodity*",
            "runtime/lof/data/*r5*",
        ],
    ),
    (
        "storage",
        [
            "runtime/lof/snapshot_store.py",
            "runtime/lof/snapshot_archive.py",
            "runtime/lof/snapshot_compression.py",
            "runtime/lof/snapshot_retention.py",
            "runtime/lof/estimate_persistence.py",
            "runtime/lof/estimate_validation.py",
            "runtime/lof/estimate_model_registry.py",
        ],
    ),
    (
        "freshness",
        [
            "runtime/lof/estimate_freshness_audit.py",
            "runtime/lof/estimate_reliability.py",
        ],
    ),
    (
        "collector",
        [
            "runtime/lof/nav.py",
            "runtime/lof/quote.py",
            "runtime/lof/universe.py",
            "runtime/lof/source_preflight.py",
            "runtime/lof/szse_relay.py",
            "runtime/lof/http_json.py",
        ],
    ),
    (
        "runtime_core",
        [
            "runtime/lof/resolver.py",
            "runtime/lof/estimated_nav_lane.py",
            "runtime/lof/context_builder.py",
            "runtime/lof/controller.py",
            "runtime/lof/runtime_loop.py",
            "runtime/lof/runtime_session.py",
            "runtime/lof/snapshot.py",
            "runtime/lof/snapshot_job.py",
        ],
    ),
    (
        "deployment",
        [
            "runtime/lof/deployment_gate.py",
            "runtime/lof/knowledge_snapshot.py",
            "runtime/lof/release_manifest.py",
            "runtime/deployment_gate.py",
        ],
    ),
    (
        "test_governance",
        [
            "scripts/lof_test_plan.py",
            "scripts/lof_golden_replay.py",
            "scripts/lof_historical_replay.py",
            "tests/fixtures/lof_golden_replay_v1.json",
            "tests/test_lof_test_governance.py",
            ".github/workflows/lof-runtime-tests.yml",
            ".github/workflows/lof-release-bundle.yml",
        ],
    ),
]

FULL_LIVE_PATTERNS = [
    "runtime/lof/nav.py",
    "runtime/lof/quote.py",
    "runtime/lof/universe.py",
    "runtime/lof/source_preflight.py",
    "runtime/lof/szse_relay.py",
    "runtime/lof/http_json.py",
]

TARGETED_LIVE_DOMAINS = {"r1", "r3_fx", "r5"}
GOLDEN_DOMAINS = {"r1", "r3_fx", "r5", "runtime_core", "freshness"}
HISTORICAL_REPLAY_DOMAINS = {
    "r1",
    "r2",
    "r3_fx",
    "r5",
    "storage",
    "freshness",
    "runtime_core",
}

AFFECTED_RESOLVERS = {
    "r1": ["R1_DOMESTIC_INDEX"],
    "r2": ["R2_DOMESTIC_OTHER"],
    "r3_fx": ["R3_QDII_INDEX", "R5_SPECIAL"],
    "r5": ["R5_SPECIAL"],
    "storage": ["ALL"],
    "freshness": ["ALL"],
    "collector": ["ALL"],
    "runtime_core": ["ALL"],
}


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern)


def _domain_for_path(path: str) -> set[str]:
    domains: set[str] = set()
    for domain, patterns in RULES:
        if any(_matches(path, pattern) for pattern in patterns):
            domains.add(domain)

    if path.startswith("tests/test_lof_") and path.endswith(".py"):
        stem = Path(path).stem
        # Changed tests must always run themselves, even if no source rule
        # maps to the same domain.
        domains.add(f"__test__:{stem}")

    if path == "tests/test_deployment_gate.py":
        domains.add("deployment")

    return domains


def _test_module_for_changed_test(domain: str) -> str | None:
    prefix = "__test__:"
    if not domain.startswith(prefix):
        return None
    return f"tests.{domain[len(prefix):]}"


def build_plan(changed_files: Iterable[str], *, force_full: bool = False) -> dict:
    files = sorted({str(value).strip() for value in changed_files if str(value).strip()})
    domains: set[str] = set()
    unknown_lof_runtime: list[str] = []

    for path in files:
        matched = _domain_for_path(path)
        domains |= matched
        if path.startswith("runtime/lof/") and not matched:
            unknown_lof_runtime.append(path)

    source_domains = {d for d in domains if not d.startswith("__test__:")}

    selected_tests: list[str] = []
    for domain in sorted(domains):
        changed_test = _test_module_for_changed_test(domain)
        if changed_test:
            selected_tests.append(changed_test)
            continue
        selected_tests.extend(DOMAIN_TESTS.get(domain, []))

    # A tiny core guard is cheap and catches formula/enum breakage without
    # turning every change into the 200+ test full suite.
    if source_domains & {"r1", "r2", "r3_fx", "r5", "runtime_core"}:
        selected_tests.extend(
            [
                "tests.test_lof_resolver",
                "tests.test_lof_resolver_classification",
            ]
        )

    selected_tests = list(dict.fromkeys(selected_tests))

    runtime_logic_changed = any(
        path.startswith("runtime/lof/")
        and not path.startswith("runtime/lof/static/")
        for path in files
    )

    full_regression_required = bool(
        force_full
        or unknown_lof_runtime
        or "runtime_core" in source_domains
        or len(
            source_domains
            & {"r1", "r2", "r3_fx", "r5", "storage", "freshness", "collector"}
        )
        >= 4
    )

    full_live_required = any(
        any(_matches(path, pattern) for pattern in FULL_LIVE_PATTERNS)
        for path in files
    )

    if full_live_required:
        live_scope = "FULL"
    elif source_domains & TARGETED_LIVE_DOMAINS:
        live_scope = "TARGETED"
    else:
        live_scope = "NONE"

    affected_resolvers: list[str] = []
    for domain in sorted(source_domains):
        affected_resolvers.extend(AFFECTED_RESOLVERS.get(domain, []))
    if "ALL" in affected_resolvers:
        affected_resolvers = ["ALL"]
    else:
        affected_resolvers = list(dict.fromkeys(affected_resolvers))

    golden_required = bool(source_domains & GOLDEN_DOMAINS)
    historical_replay_required = bool(
        source_domains & HISTORICAL_REPLAY_DOMAINS
    )

    # Full regression is a release barrier, not a development-commit default.
    # The targeted workflow reports this flag; release workflow/manual release
    # performs the full suite once for the exact candidate SHA.
    tier = (
        "FULL_RELEASE_REQUIRED"
        if full_regression_required
        else "TARGETED"
        if selected_tests
        else "NO_LOF_TESTS"
    )

    return {
        "version": PLAN_VERSION,
        "changed_files": files,
        "domains": sorted(source_domains),
        "unknown_lof_runtime": unknown_lof_runtime,
        "tier": tier,
        "selected_test_modules": selected_tests,
        "golden_required": golden_required,
        "historical_replay_required": historical_replay_required,
        "live_scope": live_scope,
        "affected_resolver_classes": affected_resolvers,
        "full_regression_required_before_release": bool(
            runtime_logic_changed or full_regression_required
        ),
        "full_live_preflight_required_before_release": full_live_required,
        "run_shared_gate": bool(
            "deployment" in source_domains
            or runtime_logic_changed
        ),
    }


def _write_github_output(path: Path, plan: dict) -> None:
    values = {
        "tier": plan["tier"],
        "test_modules": " ".join(plan["selected_test_modules"]),
        "has_tests": str(bool(plan["selected_test_modules"])).lower(),
        "golden_required": str(plan["golden_required"]).lower(),
        "historical_replay_required": str(
            plan["historical_replay_required"]
        ).lower(),
        "live_scope": plan["live_scope"],
        "affected_resolvers": ",".join(
            plan["affected_resolver_classes"]
        ),
        "full_regression_required": str(
            plan["full_regression_required_before_release"]
        ).lower(),
        "full_live_preflight_required": str(
            plan["full_live_preflight_required_before_release"]
        ).lower(),
        "run_shared_gate": str(plan["run_shared_gate"]).lower(),
    }
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Plan risk-scoped LOF tests from changed files."
    )
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        dest="changed_files",
    )
    parser.add_argument(
        "--changed-files-file",
        default=None,
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
    )
    parser.add_argument(
        "--github-output",
        default=None,
    )
    args = parser.parse_args(argv)

    changed = list(args.changed_files)
    if args.changed_files_file:
        changed.extend(
            Path(args.changed_files_file)
            .read_text(encoding="utf-8")
            .splitlines()
        )

    plan = build_plan(changed, force_full=args.force_full)
    print(json.dumps(plan, ensure_ascii=False, indent=2))

    if args.github_output:
        _write_github_output(Path(args.github_output), plan)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
