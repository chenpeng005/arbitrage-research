"""Downward Revision Path Economic Judgment V1."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.market_map.resolver import ResolverError, resolve_bond_valuation

JUDGMENT_VERSION = "downward-revision-economic-judgment-v1"


def _valuation(
    manifest_path: Path,
    market_row: dict[str, Any],
    target_cv: float,
) -> dict[str, Any]:
    return resolve_bond_valuation(
        manifest_path,
        bond_code=str(market_row["bond_code"]).zfill(6),
        target_CV=float(target_cv),
        target_remaining_months=float(market_row["remaining_months"]),
        target_remaining_size=float(market_row["remaining_size"]),
        scenario_id=f"revision-cv-{target_cv:.6f}",
        require_formal=True,
    )


def _drop(
    market_row: dict[str, Any],
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "economic_status": "DROP",
        "reason": reason,
        "current_price": float(market_row["current_bond_price"]),
        "current_cv": float(market_row["current_conversion_value"]),
        **extra,
    }


def _insufficient(
    market_row: dict[str, Any],
    reason: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "economic_status": "INSUFFICIENT_DATA",
        "reason": reason,
        "current_price": float(market_row["current_bond_price"]),
        "current_cv": float(market_row["current_conversion_value"]),
        **extra,
    }


def judge_one(
    market_row: dict[str, Any],
    contract_fact: dict[str, Any] | None,
    manifest_path: Path,
) -> dict[str, Any]:
    price = float(market_row["current_bond_price"])
    current_cv = float(market_row["current_conversion_value"])
    stock_price = float(market_row["current_stock_price"])

    # 100 is the optimistic revision-only CV upper bound under Discovery V1.
    if current_cv >= 100.0:
        return _drop(
            market_row,
            "NO_INCREMENTAL_REVISION_VALUE",
            revision_cv_ceiling=100.0,
            decision_invariance_stage="CV_UPPER_BOUND",
        )

    try:
        optimistic = _valuation(manifest_path, market_row, 100.0)
    except ResolverError as exc:
        return _insufficient(
            market_row,
            "VALUATION_RESOLVER_ERROR",
            error=str(exc),
        )

    optimistic_ref = float(optimistic["valuation"]["discovery_reference"])
    optimistic_spread = optimistic_ref - price
    if optimistic_spread <= 0:
        return _drop(
            market_row,
            "NO_POSITIVE_ECONOMIC_SPACE_AT_CV100",
            revision_cv_ceiling=100.0,
            discovery_reference=optimistic_ref,
            discovery_spread=optimistic_spread,
            model_zone=optimistic["model_position"]["model_zone"],
            valuation_sensitive=False,
            decision_invariance_stage="OPTIMISTIC_CV100",
        )

    # Only optimistic candidates require mechanism facts.
    if not contract_fact or contract_fact.get("status") != "READY":
        return _insufficient(
            market_row,
            "REVISION_CONTRACT_FACTS_REQUIRED",
            optimistic_reference=optimistic_ref,
            optimistic_spread=optimistic_spread,
        )

    if not contract_fact.get("revision_clause_available"):
        return _drop(
            market_row,
            "PERMANENTLY_UNAVAILABLE",
            mechanism_available=False,
            permanent_revision_blocker=False,
            optimistic_reference=optimistic_ref,
            optimistic_spread=optimistic_spread,
        )

    if contract_fact.get("permanent_revision_blocker"):
        return _drop(
            market_row,
            "PERMANENTLY_UNAVAILABLE",
            mechanism_available=True,
            permanent_revision_blocker=True,
            blocker_basis=contract_fact.get("permanent_revision_blocker_basis"),
            reset_start=contract_fact.get("reset_start"),
            maturity_date=contract_fact.get("maturity_date"),
            optimistic_reference=optimistic_ref,
            optimistic_spread=optimistic_spread,
        )

    nav_raw = contract_fact.get("latest_audited_nav_per_share")
    if nav_raw is None:
        return _insufficient(
            market_row,
            "AUDITED_NAV_REQUIRED_FOR_SCREEN",
            mechanism_available=True,
            permanent_revision_blocker=False,
            optimistic_reference=optimistic_ref,
            optimistic_spread=optimistic_spread,
        )
    nav = float(nav_raw)

    # Current snapshot has no optimistic candidate with S < 1, so a possible
    # one-yuan par floor is decision-irrelevant. Escalate instead of assuming
    # if a future snapshot enters that region.
    if stock_price < 1.0:
        return _insufficient(
            market_row,
            "PAR_VALUE_FLOOR_EVIDENCE_REQUIRED",
            stock_price=stock_price,
            audited_nav=nav,
            optimistic_reference=optimistic_ref,
            optimistic_spread=optimistic_spread,
        )

    if stock_price >= nav:
        return {
            "economic_status": "KEEP",
            "reason": "POSITIVE_REVISION_ECONOMIC_SPACE",
            "mechanism_available": True,
            "permanent_revision_blocker": False,
            "current_price": price,
            "current_cv": current_cv,
            "stock_price": stock_price,
            "audited_nav": nav,
            "nav_floor_decision_sensitive": False,
            "nav_floor_applicable": contract_fact.get("nav_floor_applicable"),
            "revision_cv_ceiling": 100.0,
            "revision_cv_ceiling_basis": "NAV_SCREEN_NOT_BINDING",
            "neutral_reference": float(optimistic["valuation"]["neutral_reference"]),
            "discovery_reference": optimistic_ref,
            "discovery_spread": optimistic_spread,
            "model_zone": optimistic["model_position"]["model_zone"],
            "valuation_sensitive": False,
        }

    nav_cv_ceiling = 100.0 * stock_price / nav

    if nav_cv_ceiling <= current_cv:
        nav_decision = "DROP"
        nav_reason = "NO_INCREMENTAL_REVISION_VALUE_UNDER_NAV_FLOOR"
        nav_valuation = None
    else:
        try:
            nav_valuation = _valuation(manifest_path, market_row, nav_cv_ceiling)
        except ResolverError as exc:
            return _insufficient(
                market_row,
                "VALUATION_RESOLVER_ERROR",
                error=str(exc),
                nav_cv_ceiling=nav_cv_ceiling,
            )
        if not nav_valuation["model_position"]["inside_support"]:
            nav_decision = "UNKNOWN"
            nav_reason = "NAV_BRANCH_OUTSIDE_MODEL_SUPPORT"
        else:
            nav_reference = float(nav_valuation["valuation"]["discovery_reference"])
            nav_decision = "KEEP" if nav_reference > price else "DROP"
            nav_reason = (
                "POSITIVE_REVISION_ECONOMIC_SPACE"
                if nav_decision == "KEEP"
                else "NO_POSITIVE_ECONOMIC_SPACE"
            )

    # No-NAV branch is already known KEEP. If NAV branch is also KEEP, exact
    # clause applicability cannot change the economic decision.
    if nav_decision == "KEEP":
        assert nav_valuation is not None
        nav_reference = float(nav_valuation["valuation"]["discovery_reference"])
        return {
            "economic_status": "KEEP",
            "reason": "POSITIVE_REVISION_ECONOMIC_SPACE_ALL_NAV_BRANCHES",
            "mechanism_available": True,
            "permanent_revision_blocker": False,
            "current_price": price,
            "current_cv": current_cv,
            "stock_price": stock_price,
            "audited_nav": nav,
            "nav_floor_decision_sensitive": False,
            "nav_floor_applicable": contract_fact.get("nav_floor_applicable"),
            "revision_cv_ceiling": None,
            "revision_cv_ceiling_min": nav_cv_ceiling,
            "revision_cv_ceiling_max": 100.0,
            "revision_cv_ceiling_basis": "DECISION_INVARIANT_NAV_RANGE",
            "discovery_reference": None,
            "discovery_reference_min": nav_reference,
            "discovery_reference_max": optimistic_ref,
            "discovery_spread_min": nav_reference - price,
            "discovery_spread_max": optimistic_spread,
            "model_zone_min": nav_valuation["model_position"]["model_zone"],
            "model_zone_max": optimistic["model_position"]["model_zone"],
            "valuation_sensitive": (
                nav_valuation["model_position"]["model_zone"] == "SUPPORT"
            ),
        }

    nav_applicable = contract_fact.get("nav_floor_applicable")
    if nav_applicable is None:
        return _insufficient(
            market_row,
            "NAV_CLAUSE_REQUIRED",
            mechanism_available=True,
            permanent_revision_blocker=False,
            stock_price=stock_price,
            audited_nav=nav,
            nav_cv_ceiling=nav_cv_ceiling,
            no_nav_decision="KEEP",
            nav_branch_decision=nav_decision,
            nav_branch_reason=nav_reason,
            optimistic_reference=optimistic_ref,
            optimistic_spread=optimistic_spread,
        )

    if not nav_applicable:
        return {
            "economic_status": "KEEP",
            "reason": "POSITIVE_REVISION_ECONOMIC_SPACE",
            "mechanism_available": True,
            "permanent_revision_blocker": False,
            "current_price": price,
            "current_cv": current_cv,
            "stock_price": stock_price,
            "audited_nav": nav,
            "nav_floor_decision_sensitive": True,
            "nav_floor_applicable": False,
            "nav_clause_evidence_source": contract_fact.get("nav_clause_evidence_source"),
            "revision_cv_ceiling": 100.0,
            "revision_cv_ceiling_basis": "NAV_CLAUSE_NOT_APPLICABLE",
            "neutral_reference": float(optimistic["valuation"]["neutral_reference"]),
            "discovery_reference": optimistic_ref,
            "discovery_spread": optimistic_spread,
            "model_zone": optimistic["model_position"]["model_zone"],
            "valuation_sensitive": False,
        }

    if nav_decision == "UNKNOWN":
        return _insufficient(
            market_row,
            "NAV_BRANCH_OUTSIDE_MODEL_SUPPORT",
            mechanism_available=True,
            permanent_revision_blocker=False,
            nav_floor_applicable=True,
            nav_clause_evidence_source=contract_fact.get("nav_clause_evidence_source"),
            nav_cv_ceiling=nav_cv_ceiling,
        )

    if nav_cv_ceiling <= current_cv:
        return _drop(
            market_row,
            "NO_INCREMENTAL_REVISION_VALUE",
            mechanism_available=True,
            permanent_revision_blocker=False,
            stock_price=stock_price,
            audited_nav=nav,
            nav_floor_decision_sensitive=True,
            nav_floor_applicable=True,
            nav_clause_evidence_source=contract_fact.get("nav_clause_evidence_source"),
            revision_cv_ceiling=nav_cv_ceiling,
            revision_cv_ceiling_basis="AUDITED_NAV_FLOOR",
        )

    assert nav_valuation is not None
    nav_reference = float(nav_valuation["valuation"]["discovery_reference"])
    nav_spread = nav_reference - price
    return _drop(
        market_row,
        "NO_POSITIVE_ECONOMIC_SPACE",
        mechanism_available=True,
        permanent_revision_blocker=False,
        stock_price=stock_price,
        audited_nav=nav,
        nav_floor_decision_sensitive=True,
        nav_floor_applicable=True,
        nav_clause_evidence_source=contract_fact.get("nav_clause_evidence_source"),
        revision_cv_ceiling=nav_cv_ceiling,
        revision_cv_ceiling_basis="AUDITED_NAV_FLOOR",
        neutral_reference=float(nav_valuation["valuation"]["neutral_reference"]),
        discovery_reference=nav_reference,
        discovery_spread=nav_spread,
        model_zone=nav_valuation["model_position"]["model_zone"],
        valuation_sensitive=(
            nav_valuation["model_position"]["model_zone"] == "SUPPORT"
        ),
    )


def judge_market(
    market_input: dict[str, Any],
    contract_facts: dict[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    facts = {
        str(row["bond_code"]).zfill(6): row
        for row in contract_facts["rows"]
    }
    rows = []
    for market_row in market_input["rows"]:
        code = str(market_row["bond_code"]).zfill(6)
        result = judge_one(market_row, facts.get(code), manifest_path)
        rows.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            **result,
        })

    return {
        "judgment_version": JUDGMENT_VERSION,
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "contract_facts_version": contract_facts["contract_facts_version"],
        "rows": rows,
        "summary": {
            "KEEP": sum(row["economic_status"] == "KEEP" for row in rows),
            "DROP": sum(row["economic_status"] == "DROP" for row in rows),
            "INSUFFICIENT_DATA": sum(
                row["economic_status"] == "INSUFFICIENT_DATA" for row in rows
            ),
        },
    }
