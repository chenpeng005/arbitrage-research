"""Deterministic Event Canonicalization + Cross-Path Impact Router.

Evidence Document != Business Event.
This module only performs title/state-delta classification that is safe to do
without AI. Ambiguous documents are explicitly routed to SEMANTIC_AUDIT.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

EVENT_ROUTER_VERSION = "incremental-event-router-v2-token-gate"

ACTIVE_SCOPES = (
    "MATURITY_CASH",
    "PUT",
    "DOWNWARD_REVISION",
    "CREDIT_RISK",
)


def _evidence_id(doc: dict[str, Any]) -> str:
    url = str(doc.get("url") or doc.get("detail_url") or "")
    m = re.search(r"/(AN\d+)\.html", url)
    if m:
        return m.group(1)
    raw = "|".join(
        [
            str(doc.get("notice_date") or doc.get("published_at") or ""),
            str(doc.get("title") or ""),
            url,
        ]
    )
    return "EVID_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _event_update_id(
    bond_code: str,
    event_family: str,
    evidence_id: str,
) -> str:
    raw = f"{str(bond_code).zfill(6)}|{event_family}|{evidence_id}"
    return "EVU_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _family_id(bond_code: str, event_family: str) -> str:
    return f"{str(bond_code).zfill(6)}:{event_family}"


def _hard_credit_family(title: str) -> str:
    if re.search(r"债务逾期", title):
        return "CREDIT:DEBT_OVERDUE"
    if re.search(r"担保逾期", title):
        return "CREDIT:GUARANTEE_OVERDUE"
    if re.search(r"冻结|轮候冻结", title):
        return "CREDIT:SHARE_FREEZE"
    if re.search(r"预重整|重整|破产", title):
        return "CREDIT:RESTRUCTURING"
    if re.search(r"违约", title):
        return "CREDIT:DEFAULT"
    if re.search(r"持续经营", title):
        return "CREDIT:GOING_CONCERN"
    return "CREDIT:HARD_OTHER"


def _put_family(kind: str) -> str:
    if kind == "PUT_EXPECTED_TRIGGER":
        return "PUT:EXPECTED_TRIGGER"
    if kind == "PUT_TRIGGER":
        return "PUT:CONDITION_MET"
    if kind == "PUT_RESULT":
        return "PUT:RESULT"
    return "PUT:OTHER"


def _revision_family(kind: str, title: str) -> str:
    if kind == "NO_REVISION":
        return "REVISION:NO_REVISION_CYCLE"
    if kind == "EXPECTED_TRIGGER":
        return "REVISION:EXPECTED_TRIGGER"
    if kind == "TRIGGER":
        return "REVISION:CONDITION_MET"
    if kind == "CONVERSION_PRICE_EVENT":
        return "REVISION:FINAL_K_CHANGE"
    if kind == "REVISION_ACTION":
        if "董事会提议" in title:
            return "REVISION:BOARD_PROPOSAL"
        if re.search(r"不向下修正|不下修", title):
            return "REVISION:NO_REVISION_CYCLE"
        if re.search(r"向下修正.*转股价格|转股价格.*向下修正", title):
            return "REVISION:FINAL_K_CHANGE"
        return "REVISION:GOVERNANCE_ACTION"
    return "REVISION:OTHER"


def canonicalize_evidence_document(
    *,
    bond_code: str,
    bond_name: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    title = str(document.get("title") or "")
    kind = str(document.get("event_kind") or "OTHER")
    evidence_id = _evidence_id(document)
    notice_date = str(document.get("notice_date") or document.get("published_at") or "")
    url = str(document.get("url") or document.get("detail_url") or "")

    event_family: str | None = None
    status = "DETERMINISTIC_EVENT"
    requires_semantic = False
    reason = "TITLE_CLASSIFIED"

    if kind == "HARD_CREDIT_EVENT":
        event_family = _hard_credit_family(title)
        if "进展" in title:
            status = "SEMANTIC_REQUIRED"
            requires_semantic = True
            reason = "ONGOING_EVENT_PROGRESS_NEEDS_DELTA_AUDIT"
    elif kind in {
        "NO_REVISION",
        "EXPECTED_TRIGGER",
        "TRIGGER",
        "REVISION_ACTION",
        "CONVERSION_PRICE_EVENT",
    }:
        event_family = _revision_family(kind, title)
    elif kind in {"PUT_EXPECTED_TRIGGER", "PUT_TRIGGER", "PUT_RESULT"}:
        event_family = _put_family(kind)
    elif kind == "FINANCIAL_REPORT":
        # Full report and summary are Evidence Documents for the same reporting
        # event. Period identity can be refined later from metadata/content.
        event_family = "FINANCIAL:PERIODIC_REPORT"
    elif kind == "RATING_REPORT":
        event_family = "CREDIT:RATING_UPDATE"
        status = "SEMANTIC_REQUIRED"
        requires_semantic = True
        reason = "RATING_DIRECTION_NOT_KNOWN_FROM_TITLE"
    elif kind == "TRUSTEE_REPORT":
        # A trustee report frequently repeats old credit events. It is not a
        # new business Event until content comparison proves new facts.
        event_family = None
        status = "DOCUMENT_ONLY"
        requires_semantic = True
        reason = "TRUSTEE_REPORT_MAY_REPEAT_EXISTING_EVENTS"
    elif kind == "FINANCING_SUPPORT":
        event_family = "CREDIT:FINANCING_SUPPORT"
        status = "SEMANTIC_REQUIRED"
        requires_semantic = True
        reason = "SUPPORT_MATERIALITY_REQUIRES_CONTENT"
    elif kind == "SUPPORT_OR_ASSET":
        event_family = "CREDIT:SUPPORT_OR_ASSET"
        status = "SEMANTIC_REQUIRED"
        requires_semantic = True
        reason = "SUPPORT_MATERIALITY_REQUIRES_CONTENT"
    else:
        status = "SEMANTIC_REQUIRED"
        requires_semantic = True
        reason = "UNCLASSIFIED_DOCUMENT"

    event_update_id = (
        _event_update_id(bond_code, event_family, evidence_id)
        if event_family
        else None
    )
    return {
        "event_router_version": EVENT_ROUTER_VERSION,
        "bond_code": str(bond_code).zfill(6),
        "bond_name": bond_name,
        "evidence_id": evidence_id,
        "notice_date": notice_date,
        "title": title,
        "url": url,
        "source_event_kind": kind,
        "canonicalization_status": status,
        "requires_semantic_audit": requires_semantic,
        "event_family": event_family,
        "event_family_id": (
            _family_id(bond_code, event_family) if event_family else None
        ),
        "event_update_id": event_update_id,
        "reason": reason,
    }


def canonicalize_document_set(
    *,
    bond_code: str,
    bond_name: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    docs = [
        canonicalize_evidence_document(
            bond_code=bond_code,
            bond_name=bond_name,
            document=doc,
        )
        for doc in documents
    ]

    unique: dict[str, dict[str, Any]] = {}
    duplicate_evidence_ids: list[str] = []
    for doc in docs:
        eid = str(doc["evidence_id"])
        if eid in unique:
            duplicate_evidence_ids.append(eid)
            continue
        unique[eid] = doc

    family_documents: dict[str, list[dict[str, Any]]] = defaultdict(list)
    document_only: list[dict[str, Any]] = []
    semantic_required: list[dict[str, Any]] = []
    for doc in unique.values():
        if doc.get("requires_semantic_audit"):
            semantic_required.append(doc)
        family = doc.get("event_family")
        if family:
            family_documents[str(family)].append(doc)
        else:
            document_only.append(doc)

    event_families = []
    for family, family_docs in sorted(family_documents.items()):
        # Evidence documents of one family/date support one candidate update.
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for doc in family_docs:
            by_date[str(doc.get("notice_date") or "UNKNOWN")].append(doc)

        confirmed_updates: list[dict[str, Any]] = []
        semantic_candidates: list[dict[str, Any]] = []
        for notice_date, supporting in sorted(by_date.items()):
            supporting.sort(key=lambda x: str(x.get("evidence_id") or ""))
            evidence_ids = [str(x["evidence_id"]) for x in supporting]
            # Business update identity is stable by family/date. Supporting
            # Evidence may arrive later without creating a fake new Event.
            update_id = _event_update_id(bond_code, family, notice_date)
            requires_semantic = any(
                bool(x.get("requires_semantic_audit")) for x in supporting
            )
            update = {
                "event_update_id": update_id,
                "event_family": family,
                "event_family_id": _family_id(bond_code, family),
                "notice_date": notice_date,
                "canonicalization_status": (
                    "SEMANTIC_REQUIRED"
                    if requires_semantic
                    else "CONFIRMED_EVENT_UPDATE"
                ),
                "requires_semantic_audit": requires_semantic,
                "supporting_evidence_ids": evidence_ids,
                "supporting_documents": supporting,
            }
            if requires_semantic:
                semantic_candidates.append(update)
            else:
                confirmed_updates.append(update)

        versions = [
            {**update, "event_version": idx}
            for idx, update in enumerate(confirmed_updates, start=1)
        ]
        candidate_versions = [
            {**update, "candidate_version": idx}
            for idx, update in enumerate(semantic_candidates, start=1)
        ]

        if versions:
            latest = versions[-1]
            watermark_raw = "|".join(
                str(x["event_update_id"]) for x in versions
            )
            watermark = "EWM_" + hashlib.sha256(
                watermark_raw.encode("utf-8")
            ).hexdigest()[:20]
            latest_id = latest["event_update_id"]
            latest_date = latest["notice_date"]
        else:
            watermark = "EWM_EMPTY"
            latest_id = None
            latest_date = None

        if candidate_versions:
            candidate_raw = "|".join(
                str(x["event_update_id"]) for x in candidate_versions
            )
            candidate_watermark = "ECM_" + hashlib.sha256(
                candidate_raw.encode("utf-8")
            ).hexdigest()[:20]
        else:
            candidate_watermark = "ECM_EMPTY"

        event_families.append(
            {
                "event_family": family,
                "event_family_id": _family_id(bond_code, family),
                "document_count": len(family_docs),
                "confirmed_update_count": len(versions),
                "semantic_candidate_count": len(candidate_versions),
                "latest_event_version": len(versions),
                "latest_event_update_id": latest_id,
                "latest_notice_date": latest_date,
                "event_watermark": watermark,
                "semantic_candidate_watermark": candidate_watermark,
                "updates": versions,
                "semantic_update_candidates": candidate_versions,
            }
        )

    return {
        "event_router_version": EVENT_ROUTER_VERSION,
        "bond_code": str(bond_code).zfill(6),
        "bond_name": bond_name,
        "input_document_count": len(documents),
        "unique_document_count": len(unique),
        "duplicate_evidence_ids": duplicate_evidence_ids,
        "event_family_count": len(event_families),
        "event_families": event_families,
        "document_only": document_only,
        "semantic_required": semantic_required,
    }


def engineering_event(
    *,
    bond_code: str,
    bond_name: str,
    event_family: str,
    occurred_at: str,
    event_identity: str,
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_evidence_id = f"ENGINEERING:{event_identity}"
    return {
        "event_router_version": EVENT_ROUTER_VERSION,
        "bond_code": str(bond_code).zfill(6),
        "bond_name": bond_name,
        "evidence_id": raw_evidence_id,
        "notice_date": occurred_at,
        "title": event_identity,
        "url": None,
        "source_event_kind": "ENGINEERING_STATE_EVENT",
        "canonicalization_status": "DETERMINISTIC_EVENT",
        "requires_semantic_audit": False,
        "event_family": event_family,
        "event_family_id": _family_id(bond_code, event_family),
        "event_update_id": _event_update_id(
            bond_code, event_family, raw_evidence_id
        ),
        "reason": "ENGINEERING_STATE_DELTA",
        "facts": facts or {},
    }


_HARD_CREDIT = {
    "CREDIT:DEBT_OVERDUE",
    "CREDIT:GUARANTEE_OVERDUE",
    "CREDIT:SHARE_FREEZE",
    "CREDIT:RESTRUCTURING",
    "CREDIT:DEFAULT",
    "CREDIT:GOING_CONCERN",
    "CREDIT:HARD_OTHER",
}


def route_event(event: dict[str, Any]) -> dict[str, Any]:
    family = event.get("event_family")
    if event.get("requires_semantic_audit"):
        return {
            "event_update_id": event.get("event_update_id"),
            "event_family": family,
            "status": "SEMANTIC_AUDIT_REQUIRED",
            "routes": [],
            "reason": "EVENT_UPDATE_NOT_YET_CONFIRMED",
        }
    if not family:
        return {
            "event_update_id": event.get("event_update_id"),
            "event_family": None,
            "status": "SEMANTIC_AUDIT_REQUIRED",
            "routes": [],
        }

    routes: list[dict[str, Any]] = []

    def add(
        scope: str,
        impact: str,
        research_action: str,
        notification: str,
        reason: str,
    ) -> None:
        routes.append(
            {
                "scope": scope,
                "impact": impact,
                "research_action": research_action,
                "notification": notification,
                "reason": reason,
            }
        )

    if family in _HARD_CREDIT:
        add(
            "MATURITY_CASH",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "IMMEDIATE",
            "PAYMENT_STABILITY_INVALIDATING_EVENT",
        )
        add(
            "PUT",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "IMMEDIATE",
            "FUTURE_PUT_CASH_PAYMENT_RISK_CHANGED",
        )
        add(
            "DOWNWARD_REVISION",
            "FACT_UPDATE",
            "SEMANTIC_AUDIT",
            "DAILY_DIGEST",
            "ISSUER_OBJECTIVE_MAY_CHANGE",
        )
        add(
            "CREDIT_RISK",
            "RISK_CHANGE",
            "NONE",
            "IMMEDIATE",
            "NEW_HARD_CREDIT_EVENT",
        )

    elif family == "REVISION:NO_REVISION_CYCLE":
        add(
            "DOWNWARD_REVISION",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "IMMEDIATE",
            "GOVERNANCE_DECISION_INVALIDATES_REVISION_BASELINE",
        )
        add(
            "PUT",
            "FACT_UPDATE",
            "SEMANTIC_AUDIT",
            "DAILY_DIGEST",
            "CHECK_CROSS_PATH_COUNT_OR_K_EFFECT",
        )

    elif family == "REVISION:EXPECTED_TRIGGER":
        add(
            "DOWNWARD_REVISION",
            "STATE_CHANGE",
            "NONE",
            "SILENT",
            "REVISION_EXPECTED_TRIGGER_MONITOR_ONLY",
        )

    elif family in {
        "REVISION:CONDITION_MET",
        "REVISION:BOARD_PROPOSAL",
        "REVISION:GOVERNANCE_ACTION",
    }:
        add(
            "DOWNWARD_REVISION",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "IMMEDIATE",
            "REVISION_GOVERNANCE_NODE_CHANGED",
        )

    elif family == "REVISION:FINAL_K_CHANGE":
        add(
            "DOWNWARD_REVISION",
            "STATE_CHANGE",
            "FULL_V2_RESEARCH",
            "IMMEDIATE",
            "FINAL_CONVERSION_PRICE_CHANGED",
        )
        add(
            "PUT",
            "STATE_CHANGE",
            "FULL_V2_RESEARCH",
            "DAILY_DIGEST",
            "PUT_TRIGGER_LINE_AND_COUNT_CYCLE_CHANGED",
        )

    elif family in {"PUT:WINDOW_ENTERED", "PUT:EXPECTED_TRIGGER"}:
        add(
            "PUT",
            "STATE_CHANGE",
            "FULL_V2_RESEARCH",
            "DAILY_DIGEST",
            "PUT_RIGHT_FORMATION_MOVED_TO_REAL_EVENT_NODE",
        )

    elif family == "PUT:CONDITION_MET":
        add(
            "PUT",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "IMMEDIATE",
            "PUT_CONDITION_FORMALLY_MET",
        )
        add(
            "DOWNWARD_REVISION",
            "FACT_UPDATE",
            "SEMANTIC_AUDIT",
            "DAILY_DIGEST",
            "PUT_PRESSURE_MAY_CHANGE_ISSUER_REVISION_OBJECTIVE",
        )

    elif family == "PUT:RESULT":
        add(
            "PUT",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "IMMEDIATE",
            "PUT_EXECUTION_RESULT_CHANGED_PATH_STATE",
        )
        add(
            "MATURITY_CASH",
            "FACT_UPDATE",
            "SEMANTIC_AUDIT",
            "DAILY_DIGEST",
            "PUT_RESULT_MAY_CHANGE_REMAINING_MATURITY_OBLIGATION",
        )

    elif family == "FINANCIAL:PERIODIC_REPORT":
        add(
            "MATURITY_CASH",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "DAILY_DIGEST",
            "PAYMENT_CAPACITY_BASELINE_REFRESH",
        )
        add(
            "PUT",
            "MATERIAL_CHANGE",
            "FULL_V2_RESEARCH",
            "DAILY_DIGEST",
            "FUTURE_PAYMENT_CAPACITY_BASELINE_REFRESH",
        )
        add(
            "DOWNWARD_REVISION",
            "FACT_UPDATE",
            "SEMANTIC_AUDIT",
            "DAILY_DIGEST",
            "FINANCIAL_STATE_MAY_AFFECT_ISSUER_OBJECTIVE",
        )

    elif family == "CREDIT:RATING_UPDATE":
        if str(event.get("materiality_status") or "") == "FACT_UPDATE":
            for scope in ("MATURITY_CASH", "PUT", "CREDIT_RISK"):
                add(
                    scope,
                    "FACT_UPDATE",
                    "NONE",
                    "SILENT",
                    "EVENT_LEVEL_FACT_UPDATE_NO_SCOPE_AI",
                )
        else:
            add(
                "MATURITY_CASH",
                "FACT_UPDATE",
                "SEMANTIC_AUDIT",
                "DAILY_DIGEST",
                "RATING_DIRECTION_AND_MATERIALITY_REQUIRED",
            )
            add(
                "PUT",
                "FACT_UPDATE",
                "SEMANTIC_AUDIT",
                "DAILY_DIGEST",
                "RATING_DIRECTION_AND_MATERIALITY_REQUIRED",
            )
            add(
                "CREDIT_RISK",
                "FACT_UPDATE",
                "SEMANTIC_AUDIT",
                "DAILY_DIGEST",
                "RATING_DIRECTION_AND_MATERIALITY_REQUIRED",
            )

    elif family in {"CREDIT:FINANCING_SUPPORT", "CREDIT:SUPPORT_OR_ASSET"}:
        if str(event.get("materiality_status") or "") == "FACT_UPDATE":
            for scope in ("MATURITY_CASH", "PUT"):
                add(
                    scope,
                    "FACT_UPDATE",
                    "NONE",
                    "SILENT",
                    "EVENT_LEVEL_FACT_UPDATE_NO_SCOPE_AI",
                )
        else:
            add(
                "MATURITY_CASH",
                "FACT_UPDATE",
                "SEMANTIC_AUDIT",
                "DAILY_DIGEST",
                "SUPPORT_REALIZATION_AND_MATERIALITY_REQUIRED",
            )
            add(
                "PUT",
                "FACT_UPDATE",
                "SEMANTIC_AUDIT",
                "DAILY_DIGEST",
                "SUPPORT_REALIZATION_AND_MATERIALITY_REQUIRED",
            )

    else:
        return {
            "event_update_id": event.get("event_update_id"),
            "event_family": family,
            "status": "SEMANTIC_AUDIT_REQUIRED",
            "routes": [],
        }

    return {
        "event_update_id": event.get("event_update_id"),
        "event_family": family,
        "status": "ROUTED",
        "routes": routes,
    }

