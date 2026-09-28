"""Incremental Change Set + Notification Policy.

Research Action and Notification are deliberately independent axes.
This module is deterministic and does not send messages.
"""

from __future__ import annotations

import hashlib
from typing import Any

CHANGE_POLICY_VERSION = "incremental-change-notification-v2"

LEVEL_RANK = {"SILENT": 0, "DAILY_DIGEST": 1, "IMMEDIATE": 2}


def _id(prefix: str, *parts: Any) -> str:
    raw = "|".join(str(x) for x in parts)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def market_change(
    *,
    bond_code: str,
    bond_name: str,
    path_id: str,
    snapshot_id: str,
    previous_economic_status: str,
    current_economic_status: str,
    previous_metrics: dict[str, Any],
    current_metrics: dict[str, Any],
    research_action: str,
    material_metric_signal: bool = False,
    previous_research_status: str | None = None,
    previous_research_attention: bool = False,
) -> dict[str, Any]:
    if previous_economic_status != current_economic_status:
        change_type = (
            "ECONOMIC_ENTERED"
            if current_economic_status == "KEEP"
            else "ECONOMIC_EXITED"
        )
        impact = "STATE_CHANGE"
    elif previous_metrics != current_metrics:
        change_type = "ECONOMIC_METRIC_CHANGED"
        impact = "METRIC_ONLY"
    else:
        change_type = "NO_CHANGE"
        impact = "NONE"

    change_id = _id(
        "CHG_",
        bond_code,
        path_id,
        snapshot_id,
        change_type,
        previous_economic_status,
        current_economic_status,
        previous_metrics,
        current_metrics,
    )
    return {
        "change_policy_version": CHANGE_POLICY_VERSION,
        "change_id": change_id,
        "change_source": "MARKET",
        "source_event_update_id": None,
        "market_snapshot_id": snapshot_id,
        "bond_code": str(bond_code).zfill(6),
        "bond_name": bond_name,
        "scope_type": "PATH",
        "scope_id": path_id,
        "change_type": change_type,
        "impact": impact,
        "previous_value": {
            "economic_status": previous_economic_status,
            "metrics": previous_metrics,
            "research_status": previous_research_status,
        },
        "current_value": {
            "economic_status": current_economic_status,
            "metrics": current_metrics,
        },
        "research_action": research_action,
        "material_metric_signal": bool(material_metric_signal),
        "previous_research_status": previous_research_status,
        "previous_research_attention": bool(previous_research_attention),
        "route_notification_hint": None,
    }


def event_changes(
    *,
    bond_code: str,
    bond_name: str,
    event: dict[str, Any],
    routing: dict[str, Any],
) -> list[dict[str, Any]]:
    event_update_id = str(event.get("event_update_id") or "")
    event_family = event.get("event_family")
    rows: list[dict[str, Any]] = []
    for route in routing.get("routes", []):
        scope = str(route.get("scope") or "")
        scope_type = "RISK" if scope.endswith("_RISK") else "PATH"
        change_type = (
            "RISK_CHANGED"
            if route.get("impact") == "RISK_CHANGE"
            else "MATERIAL_EVENT"
            if route.get("impact") == "MATERIAL_CHANGE"
            else "PATH_STATE_CHANGED"
            if route.get("impact") == "STATE_CHANGE"
            else "FACT_UPDATED"
        )
        rows.append(
            {
                "change_policy_version": CHANGE_POLICY_VERSION,
                "change_id": _id(
                    "CHG_",
                    bond_code,
                    event_update_id,
                    scope,
                    change_type,
                ),
                "change_source": "EVENT",
                "source_event_update_id": event_update_id,
                "event_family": event_family,
                "market_snapshot_id": None,
                "bond_code": str(bond_code).zfill(6),
                "bond_name": bond_name,
                "scope_type": scope_type,
                "scope_id": scope,
                "change_type": change_type,
                "impact": route.get("impact"),
                "previous_value": None,
                "current_value": {
                    "event_family": event_family,
                    "event_update_id": event_update_id,
                },
                "research_action": route.get("research_action"),
                "material_metric_signal": False,
                "route_notification_hint": route.get("notification"),
                "route_reason": route.get("reason"),
            }
        )
    return rows


def deep_research_change(
    *,
    bond_code: str,
    bond_name: str,
    path_id: str,
    event_update_id: str,
    event_family: str | None,
    reason: str | None = None,
) -> dict[str, Any]:
    """User-attention change: this Path has just entered a new Full V2 research task."""
    return {
        "change_policy_version": CHANGE_POLICY_VERSION,
        "change_id": _id(
            "CHG_",
            bond_code,
            event_update_id,
            path_id,
            "DEEP_RESEARCH_TRIGGERED",
        ),
        "change_source": "RESEARCH_TRIGGER",
        "source_event_update_id": event_update_id,
        "event_family": event_family,
        "market_snapshot_id": None,
        "bond_code": str(bond_code).zfill(6),
        "bond_name": bond_name,
        "scope_type": "PATH",
        "scope_id": path_id,
        "change_type": "DEEP_RESEARCH_TRIGGERED",
        "impact": "ATTENTION_CHANGE",
        "previous_value": None,
        "current_value": {
            "event_family": event_family,
            "event_update_id": event_update_id,
            "research_action": "FULL_V2_RESEARCH",
        },
        "research_action": "FULL_V2_RESEARCH",
        "material_metric_signal": False,
        "deep_research_triggered": True,
        "route_notification_hint": "IMMEDIATE",
        "route_reason": reason,
    }


def semantic_candidate_change(
    *,
    bond_code: str,
    bond_name: str,
    event_candidate: dict[str, Any],
) -> dict[str, Any]:
    event_update_id = str(event_candidate.get("event_update_id") or "")
    family = event_candidate.get("event_family")
    return {
        "change_policy_version": CHANGE_POLICY_VERSION,
        "change_id": _id(
            "CHG_", bond_code, event_update_id, "SEMANTIC_CANDIDATE"
        ),
        "change_source": "EVIDENCE",
        "source_event_update_id": event_update_id,
        "event_family": family,
        "market_snapshot_id": None,
        "bond_code": str(bond_code).zfill(6),
        "bond_name": bond_name,
        "scope_type": "BOND",
        "scope_id": "EVENT_AUDIT",
        "change_type": "NEW_EVIDENCE_PENDING_SEMANTIC_AUDIT",
        "impact": "FACT_UPDATE",
        "previous_value": None,
        "current_value": {
            "event_family": family,
            "event_update_id": event_update_id,
        },
        "research_action": "SEMANTIC_AUDIT",
        "material_metric_signal": False,
        "route_notification_hint": "DAILY_DIGEST",
    }


def research_change(
    *,
    bond_code: str,
    bond_name: str,
    path_id: str,
    previous_result_id: str | None,
    current_result_id: str,
    previous_judgment_signature: str | None,
    current_judgment_signature: str,
    previous_confidence: str | None,
    current_confidence: str | None,
) -> dict[str, Any]:
    if (
        previous_judgment_signature is not None
        and previous_judgment_signature != current_judgment_signature
    ):
        change_type = "RESEARCH_JUDGMENT_CHANGED"
        impact = "MATERIAL_CHANGE"
    elif previous_confidence != current_confidence:
        change_type = "RESEARCH_CONFIDENCE_CHANGED"
        impact = "MATERIAL_CHANGE"
    else:
        change_type = "RESEARCH_BASELINE_UPDATED"
        impact = "FACT_UPDATE"

    return {
        "change_policy_version": CHANGE_POLICY_VERSION,
        "change_id": _id(
            "CHG_",
            bond_code,
            path_id,
            previous_result_id,
            current_result_id,
            change_type,
        ),
        "change_source": "RESEARCH",
        "source_event_update_id": None,
        "market_snapshot_id": None,
        "bond_code": str(bond_code).zfill(6),
        "bond_name": bond_name,
        "scope_type": "PATH",
        "scope_id": path_id,
        "change_type": change_type,
        "impact": impact,
        "previous_value": {
            "result_id": previous_result_id,
            "judgment_signature": previous_judgment_signature,
            "confidence": previous_confidence,
        },
        "current_value": {
            "result_id": current_result_id,
            "judgment_signature": current_judgment_signature,
            "confidence": current_confidence,
        },
        "research_action": "COMPLETED",
        "material_metric_signal": False,
        "route_notification_hint": None,
    }


def notification_level(change: dict[str, Any]) -> str:
    """Reminder Policy V2: discovery stays wide; user attention stays narrow."""
    change_type = str(change.get("change_type") or "")

    if change_type == "DEEP_RESEARCH_TRIGGERED":
        return "IMMEDIATE"

    if change_type == "ECONOMIC_EXITED":
        return (
            "IMMEDIATE"
            if bool(change.get("previous_research_attention"))
            else "SILENT"
        )

    # Economic entry, semantic candidates, ordinary event/fact updates and
    # routine research metadata remain auditable in Change Ledger but do not
    # interrupt the user. A later Full V2 trigger gets its own explicit change.
    return "SILENT"


def attach_notification_decision(change: dict[str, Any]) -> dict[str, Any]:
    level = notification_level(change)
    return {
        **change,
        "notification_level": level,
        "notification_required": level != "SILENT",
    }


def coalesce_notification_groups(
    changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group many Path changes from one bond/event into one user notification."""
    groups: dict[str, list[dict[str, Any]]] = {}

    for raw in changes:
        change = attach_notification_decision(raw)
        if not change["notification_required"]:
            continue
        event_id = change.get("source_event_update_id")
        if event_id:
            group_key = f"EVENT:{change['bond_code']}:{event_id}"
        elif change.get("market_snapshot_id"):
            group_key = (
                f"MARKET:{change['bond_code']}:{change['market_snapshot_id']}:"
                f"{change['change_type']}"
            )
        else:
            group_key = (
                f"{change['change_source']}:{change['bond_code']}:"
                f"{change['change_id']}"
            )
        groups.setdefault(group_key, []).append(change)

    output: list[dict[str, Any]] = []
    for key, rows in sorted(groups.items()):
        level = max(
            (row["notification_level"] for row in rows),
            key=lambda x: LEVEL_RANK[x],
        )
        output.append(
            {
                "notification_group_id": _id("NTF_", key),
                "group_key": key,
                "bond_code": rows[0]["bond_code"],
                "bond_name": rows[0]["bond_name"],
                "level": level,
                "change_count": len(rows),
                "change_ids": [row["change_id"] for row in rows],
                "scopes": [row["scope_id"] for row in rows],
                "change_types": [row["change_type"] for row in rows],
                "research_actions": [row.get("research_action") for row in rows],
            }
        )
    return output


def suppress_already_notified(
    groups: list[dict[str, Any]],
    *,
    notified_group_ids: set[str],
) -> list[dict[str, Any]]:
    return [
        row
        for row in groups
        if row["notification_group_id"] not in notified_group_ids
    ]

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]