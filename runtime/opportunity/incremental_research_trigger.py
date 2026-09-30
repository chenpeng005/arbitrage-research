"""Event-driven research trigger planner for Incremental Runtime.

Pure/deterministic planner. It does not mutate formal Trigger State yet.
"""

from __future__ import annotations

from typing import Any

INCREMENTAL_TRIGGER_VERSION = "incremental-research-trigger-v1-experimental"
OPPORTUNITY_PATHS = {"MATURITY_CASH", "PUT", "DOWNWARD_REVISION"}


def plan_event_research_actions(
    *,
    event: dict[str, Any],
    routing: dict[str, Any],
    economic_status_by_scope: dict[str, str],
    previously_emitted_keys: set[str] | None = None,
) -> dict[str, Any]:
    previously_emitted_keys = previously_emitted_keys or set()
    event_update_id = str(event.get("event_update_id") or "")
    event_family = event.get("event_family")

    if not event_update_id:
        raise ValueError("event_update_id is required")

    if routing.get("status") != "ROUTED":
        return {
            "incremental_trigger_version": INCREMENTAL_TRIGGER_VERSION,
            "event_update_id": event_update_id,
            "event_family": event_family,
            "status": "NO_CONFIRMED_EVENT_ROUTE",
            "new_action_count": 0,
            "new_actions": [],
            "all_actions": [],
        }

    all_actions: list[dict[str, Any]] = []
    new_actions: list[dict[str, Any]] = []

    for route in routing.get("routes", []):
        scope = str(route.get("scope") or "")
        research_action = str(route.get("research_action") or "NONE")
        economic_status = economic_status_by_scope.get(scope)

        trigger_key = (
            f"EVENT:{event_update_id}:{scope}:{research_action}"
        )
        duplicate = trigger_key in previously_emitted_keys

        task_kind = "NONE"
        task_status = "NO_RESEARCH"
        suppression_reason = None

        if research_action == "FULL_V2_RESEARCH":
            if scope in OPPORTUNITY_PATHS and economic_status != "KEEP":
                task_status = "SUPPRESSED"
                suppression_reason = "ECONOMIC_PATH_NOT_KEEP"
            else:
                task_kind = "PATH_RESEARCH"
                task_status = "PENDING"
        elif research_action == "SEMANTIC_AUDIT":
            # Semantic audit is a lightweight event/path impact task, not a full
            # V2 Path Research task.
            task_kind = "EVENT_SEMANTIC_AUDIT"
            task_status = "PENDING"
        elif research_action == "NONE":
            task_kind = "NONE"
            task_status = "NO_RESEARCH"
        else:
            raise ValueError(
                f"unsupported research_action={research_action!r}"
            )

        action = {
            "event_update_id": event_update_id,
            "event_family": event_family,
            "scope": scope,
            "impact": route.get("impact"),
            "research_action": research_action,
            "notification": route.get("notification"),
            "route_reason": route.get("reason"),
            "economic_status": economic_status,
            "trigger_key": trigger_key,
            "task_kind": task_kind,
            "task_status": (
                "ALREADY_EMITTED" if duplicate and task_status == "PENDING"
                else task_status
            ),
            "suppression_reason": suppression_reason,
            "duplicate": duplicate,
        }
        all_actions.append(action)
        if task_status == "PENDING" and not duplicate:
            new_actions.append(action)

    return {
        "incremental_trigger_version": INCREMENTAL_TRIGGER_VERSION,
        "event_update_id": event_update_id,
        "event_family": event_family,
        "status": "PASS",
        "new_action_count": len(new_actions),
        "new_actions": new_actions,
        "all_actions": all_actions,
    }

