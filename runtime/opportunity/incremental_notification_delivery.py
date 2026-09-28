"""In-app notification delivery for Incremental Runtime."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage import connect

DELIVERY_VERSION = "incremental-notification-delivery-v2"
IN_APP_CHANNEL = "IN_APP"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _delivery_id(group_id: str, channel: str) -> str:
    raw = f"{group_id}|{channel}"
    return "NDL_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def ensure_in_app_deliveries(*, target_db: Path) -> dict[str, Any]:
    conn = connect(target_db)
    now = _now()
    inserted = 0
    try:
        rows = list(
            conn.execute(
                """SELECT notification_group_id
                   FROM notification_group
                   WHERE status='PENDING'
                   ORDER BY created_at, notification_group_id"""
            )
        )
        for row in rows:
            group_id = str(row["notification_group_id"])
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO notification_delivery
                   (delivery_id,notification_group_id,channel,status,
                    attempted_at,sent_at,error_text)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    _delivery_id(group_id, IN_APP_CHANNEL),
                    group_id,
                    IN_APP_CHANNEL,
                    "PENDING",
                    None,
                    None,
                    None,
                ),
            )
            inserted += conn.total_changes - before
        conn.commit()
        return {
            "delivery_version": DELIVERY_VERSION,
            "status": "PASS",
            "pending_group_count": len(rows),
            "inserted_delivery_count": inserted,
            "checked_at": now,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _decode_json(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return value



REMINDER_PRESENTATION_VERSION = "opportunity-reminder-v2"

PATH_NAME = {
    "MATURITY_CASH": "到期赎回",
    "PUT": "回售",
    "DOWNWARD_REVISION": "下修",
    "CREDIT_RISK": "信用风险",
    "EVENT_AUDIT": "事件审计",
}

EVENT_TITLE = {
    "REVISION:EXPECTED_TRIGGER": "下修条件接近触发",
    "REVISION:TRIGGER": "正式触发下修条件",
    "REVISION:BOARD_PROPOSAL": "董事会提议下修",
    "REVISION:FINAL_K_CHANGE": "最终转股价发生变化",
    "REVISION:REVISION_ACTION": "下修事项出现新进展",
    "PUT:EXPECTED_TRIGGER": "回售条件接近触发",
    "PUT:CONDITION_MET": "回售条件已经满足",
    "PUT:RESULT": "回售结果更新",
    "CREDIT:RATING_UPDATE": "评级报告更新",
    "CREDIT:SHARE_FREEZE": "股权冻结风险更新",
    "CREDIT:DEBT_OVERDUE": "债务逾期风险更新",
    "CREDIT:SUPPORT_OR_ASSET": "资产或支持事项更新",
    "CREDIT:FINANCING_SUPPORT": "融资支持事项更新",
}

PATH_WHY = {
    "MATURITY_CASH": "当前价格与剩余合同现金的关系跨过了经济边界。",
    "PUT": "当前价格与100元回售参考价的关系跨过了经济边界。",
    "DOWNWARD_REVISION": "按当前转股价值与市场价值映射，下修后的理论参考价值与当前价格关系发生了实质变化。",
}

PATH_RISK = {
    "MATURITY_CASH": "正价差不等于无风险收益，仍需结合剩余期限、交易成本和到期兑付稳定性。",
    "PUT": "回售价差不等于已经形成回售权；形成回售权后仍需评估现金兑现风险。",
    "DOWNWARD_REVISION": "经济空间成立不等于公司一定下修，实际结果仍取决于触发进度、董事会行为和最终修正价。",
}

PATH_NEXT = {
    "MATURITY_CASH": "继续观察价格、实际年化收益以及到期兑付相关信息。",
    "PUT": "继续观察普通回售条件是否形成，以及下修等动作是否改变回售路径。",
    "DOWNWARD_REVISION": "继续观察触发进度、董事会提议和最终修正价。",
}

EVENT_NEXT = {
    "REVISION:EXPECTED_TRIGGER": "关注是否正式触发下修条件，以及后续董事会是否提出下修。",
    "REVISION:TRIGGER": "关注董事会是否提出下修，以及股东会和最终修正价。",
    "REVISION:BOARD_PROPOSAL": "关注股东会结果与最终修正后的转股价。",
    "REVISION:FINAL_K_CHANGE": "用新的转股价重新观察转股价值、回售和下修路径。",
    "PUT:EXPECTED_TRIGGER": "关注回售条件是否正式形成。",
    "PUT:CONDITION_MET": "关注回售实施安排、下修应对以及现金兑现能力。",
    "PUT:RESULT": "关注回售完成后的剩余规模和后续路径变化。",
    "CREDIT:RATING_UPDATE": "关注评级结论是否变化，以及是否改变到期赎回或回售的现金兑现判断。",
    "CREDIT:SHARE_FREEZE": "关注冻结范围、后续处置和对发行人现金流或信用能力的实际影响。",
    "CREDIT:DEBT_OVERDUE": "关注逾期解决进展，以及对各现金兑付路径的影响。",
    "CREDIT:SUPPORT_OR_ASSET": "关注该事项是否进一步改变发行人的资产、融资或偿付能力。",
    "CREDIT:FINANCING_SUPPORT": "关注融资是否真正落地，以及对偿付能力的实际改善程度。",
}


def _path_name(scope_id: Any) -> str:
    scope = str(scope_id or "")
    return PATH_NAME.get(scope, scope or "状态")


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _side_metrics(side: Any) -> dict[str, Any]:
    if not isinstance(side, dict):
        return {}
    metrics = side.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def _space_value(scope_id: str, side: Any) -> float | None:
    metrics = _side_metrics(side)
    if scope_id == "MATURITY_CASH":
        for key in ("spread_C_minus_P", "spread_min", "spread_max"):
            value = _float(metrics.get(key))
            if value is not None:
                return value
    elif scope_id == "PUT":
        return _float(metrics.get("spread"))
    elif scope_id == "DOWNWARD_REVISION":
        return _float(metrics.get("discovery_spread"))
    return None


def _price_value(side: Any) -> float | None:
    metrics = _side_metrics(side)
    for key in ("current_price", "current_price_P"):
        value = _float(metrics.get(key))
        if value is not None:
            return value
    return None


def _cv_value(side: Any) -> float | None:
    return _float(_side_metrics(side).get("current_cv"))


def _space_label(scope_id: str, side: Any) -> str:
    value = _space_value(scope_id, side)
    if value is not None:
        return f"{value:+.2f}元"
    status = str(side.get("economic_status") or "") if isinstance(side, dict) else ""
    if status == "DROP":
        return "无正空间"
    if status == "ABSENT":
        return "此前无记录"
    return "—"


def _unique_text(values: list[str]) -> str:
    seen: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.append(value)
    return "；".join(seen)


def _market_change_line(change: dict[str, Any]) -> dict[str, Any]:
    scope_id = str(change.get("scope_id") or "")
    previous = change.get("previous") if isinstance(change.get("previous"), dict) else {}
    current = change.get("current") if isinstance(change.get("current"), dict) else {}
    return {
        "scope_id": scope_id,
        "path_name": _path_name(scope_id),
        "change_type": change.get("change_type"),
        "previous_status": previous.get("economic_status"),
        "current_status": current.get("economic_status"),
        "previous_space_value": _space_value(scope_id, previous),
        "current_space_value": _space_value(scope_id, current),
        "previous_space_text": _space_label(scope_id, previous),
        "current_space_text": _space_label(scope_id, current),
        "current_price": _price_value(current),
        "current_cv": _cv_value(current),
    }


def _market_presentation(changes: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [_market_change_line(change) for change in changes]
    path_names = [line["path_name"] for line in lines]
    change_types = {str(line.get("change_type") or "") for line in lines}
    if change_types == {"ECONOMIC_ENTERED"}:
        title = (
            f"{path_names[0]}机会新进入"
            if len(path_names) == 1
            else f"{'与'.join(path_names)}机会同时出现"
        )
        direction = "ENTERED"
    elif change_types == {"ECONOMIC_EXITED"}:
        title = (
            f"{path_names[0]}机会退出"
            if len(path_names) == 1
            else f"{'与'.join(path_names)}机会同时退出"
        )
        direction = "EXITED"
    else:
        title = "机会状态发生变化"
        direction = "MIXED"

    transition = "；".join(
        f"{line['path_name']}：{line['previous_space_text']} → {line['current_space_text']}"
        for line in lines
    )
    scopes = [str(change.get("scope_id") or "") for change in changes]
    why = _unique_text([PATH_WHY.get(scope, "") for scope in scopes])
    risk = _unique_text([PATH_RISK.get(scope, "") for scope in scopes])
    next_watch = _unique_text([PATH_NEXT.get(scope, "") for scope in scopes])

    return {
        "version": REMINDER_PRESENTATION_VERSION,
        "kind": "MARKET_TRANSITION",
        "direction": direction,
        "title": title,
        "summary": transition,
        "change_lines": lines,
        "why": why,
        "risk": risk,
        "next_watch": next_watch,
    }


def _event_context(conn: Any, event_update_id: str | None) -> dict[str, Any]:
    if not event_update_id:
        return {}
    row = conn.execute(
        """SELECT event_family_id,occurred_at,materiality_status,payload_json
           FROM event_update WHERE event_update_id=?""",
        (event_update_id,),
    ).fetchone()
    if row is None:
        return {}
    payload = _decode_json(row["payload_json"])
    payload = payload if isinstance(payload, dict) else {}
    family = str(payload.get("event_family") or "")
    if not family:
        family_id = str(row["event_family_id"] or "")
        family = family_id.split(":", 1)[1] if ":" in family_id else family_id
    documents = payload.get("supporting_documents")
    documents = documents if isinstance(documents, list) else []
    first_document = documents[0] if documents and isinstance(documents[0], dict) else {}
    return {
        "event_update_id": event_update_id,
        "event_family": family,
        "occurred_at": row["occurred_at"],
        "materiality_status": row["materiality_status"],
        "fact_summary": payload.get("fact_summary"),
        "source_title": first_document.get("title"),
        "source_url": first_document.get("url"),
    }


def _event_presentation(
    changes: list[dict[str, Any]],
    event: dict[str, Any],
) -> dict[str, Any]:
    family = str(event.get("event_family") or "")
    affected = [
        _path_name(change.get("scope_id"))
        for change in changes
        if str(change.get("scope_id") or "") != "EVENT_AUDIT"
    ]
    affected = list(dict.fromkeys(affected))
    summary = str(
        event.get("fact_summary")
        or event.get("source_title")
        or "出现新的正式信息，系统已完成事件识别并路由到相关路径。"
    )
    why = (
        f"这条新信息影响到{'、'.join(affected)}，系统会按各路径独立判断是否需要重研。"
        if affected
        else "这条新信息已经进入正式事件链，后续只对真正受影响的路径继续处理。"
    )
    if family.startswith("CREDIT:"):
        risk = "信用类新事实不自动等于信用结论恶化，需区分事实更新、风险变化和是否真正改变现金兑现能力。"
    elif family.startswith("REVISION:"):
        risk = "下修流程节点本身不等于最终收益，仍需结合最终修正价和市场价格判断经济结果。"
    elif family.startswith("PUT:"):
        risk = "回售流程节点不等于已经获得无风险现金，仍需确认权利是否形成及现金兑现能力。"
    else:
        risk = "新事实是否真正改变机会，需要以对应路径的经济判断和研究结果为准。"
    return {
        "version": REMINDER_PRESENTATION_VERSION,
        "kind": "EVENT_UPDATE",
        "direction": "EVENT",
        "title": EVENT_TITLE.get(family, "重要信息更新"),
        "summary": summary,
        "change_lines": [],
        "why": why,
        "risk": risk,
        "next_watch": EVENT_NEXT.get(
            family,
            "关注该事件的后续公告，以及它是否进一步改变路径状态或经济性。",
        ),
        "event": event,
    }


def build_reminder_presentation(
    *,
    conn: Any,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    event_update_id = next(
        (
            str(change.get("source_event_update_id"))
            for change in changes
            if change.get("source_event_update_id")
        ),
        None,
    )
    if event_update_id:
        return _event_presentation(
            changes,
            _event_context(conn, event_update_id),
        )
    return _market_presentation(changes)


def build_notification_feed(
    *,
    target_db: Path,
    include_sent: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    ensure_in_app_deliveries(target_db=target_db)
    conn = connect(target_db)
    try:
        statuses = ["PENDING", "SENT"] if include_sent else ["PENDING"]
        placeholders = ",".join("?" for _ in statuses)
        rows = list(
            conn.execute(
                f"""SELECT ng.notification_group_id,ng.group_key,ng.bond_code,
                           b.bond_name,ng.level,ng.status,ng.created_at,ng.sent_at,
                           nd.delivery_id,nd.status delivery_status,nd.sent_at delivery_sent_at
                    FROM notification_group ng
                    JOIN bond_master b ON b.bond_code=ng.bond_code
                    LEFT JOIN notification_delivery nd
                      ON nd.notification_group_id=ng.notification_group_id
                     AND nd.channel=?
                    WHERE ng.status IN ({placeholders})
                    ORDER BY CASE ng.level WHEN 'IMMEDIATE' THEN 0 ELSE 1 END,
                             ng.created_at DESC, ng.notification_group_id
                    LIMIT ?""",
                (IN_APP_CHANNEL, *statuses, max(1, int(limit))),
            )
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            changes = [
                {
                    "change_id": x["change_id"],
                    "source_event_update_id": x["source_event_update_id"],
                    "scope_type": x["scope_type"],
                    "scope_id": x["scope_id"],
                    "change_type": x["change_type"],
                    "impact": x["impact"],
                    "research_action": x["research_action"],
                    "notification_level": x["notification_level"],
                    "previous": _decode_json(x["previous_json"]),
                    "current": _decode_json(x["current_json"]),
                    "detected_at": x["detected_at"],
                }
                for x in conn.execute(
                    """SELECT cl.change_id,cl.source_event_update_id,
                              cl.scope_type,cl.scope_id,cl.change_type,
                              cl.impact,cl.research_action,cl.notification_level,
                              cl.previous_json,cl.current_json,cl.detected_at
                       FROM notification_change_link ncl
                       JOIN change_ledger cl ON cl.change_id=ncl.change_id
                       WHERE ncl.notification_group_id=?
                       ORDER BY cl.detected_at,cl.change_id""",
                    (row["notification_group_id"],),
                )
            ]
            items.append(
                {
                    "notification_group_id": row["notification_group_id"],
                    "group_key": row["group_key"],
                    "bond_code": row["bond_code"],
                    "bond_name": row["bond_name"],
                    "level": row["level"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "sent_at": row["sent_at"],
                    "delivery": {
                        "channel": IN_APP_CHANNEL,
                        "delivery_id": row["delivery_id"],
                        "status": row["delivery_status"] or "PENDING",
                        "sent_at": row["delivery_sent_at"],
                    },
                    "change_count": len(changes),
                    "changes": changes,
                    "presentation": build_reminder_presentation(
                        conn=conn,
                        changes=changes,
                    ),
                }
            )

        counts = {
            str(row["level"]): int(row["n"])
            for row in conn.execute(
                """SELECT level,COUNT(*) n FROM notification_group
                   WHERE status='PENDING' GROUP BY level"""
            )
        }
        return {
            "delivery_version": DELIVERY_VERSION,
            "status": "PASS",
            "channel": IN_APP_CHANNEL,
            "pending_count": sum(counts.values()),
            "pending_by_level": {
                "IMMEDIATE": counts.get("IMMEDIATE", 0),
                "DAILY_DIGEST": counts.get("DAILY_DIGEST", 0),
            },
            "item_count": len(items),
            "items": items,
        }
    finally:
        conn.close()


def acknowledge_notification(
    *,
    target_db: Path,
    notification_group_id: str,
) -> dict[str, Any]:
    ensure_in_app_deliveries(target_db=target_db)
    conn = connect(target_db)
    now = _now()
    try:
        group = conn.execute(
            """SELECT notification_group_id,status
               FROM notification_group WHERE notification_group_id=?""",
            (notification_group_id,),
        ).fetchone()
        if group is None:
            raise KeyError(f"notification group not found: {notification_group_id}")

        delivery_id = _delivery_id(notification_group_id, IN_APP_CHANNEL)
        conn.execute(
            """UPDATE notification_delivery
               SET status='SENT',attempted_at=?,sent_at=?,error_text=NULL
               WHERE delivery_id=?""",
            (now, now, delivery_id),
        )

        outstanding = conn.execute(
            """SELECT COUNT(*) n FROM notification_delivery
               WHERE notification_group_id=?
                 AND status NOT IN ('SENT','SKIPPED')""",
            (notification_group_id,),
        ).fetchone()["n"]
        if int(outstanding) == 0:
            conn.execute(
                """UPDATE notification_group
                   SET status='SENT',sent_at=?
                   WHERE notification_group_id=?""",
                (now, notification_group_id),
            )
        conn.commit()
        return {
            "delivery_version": DELIVERY_VERSION,
            "status": "PASS",
            "notification_group_id": notification_group_id,
            "delivery_id": delivery_id,
            "channel": IN_APP_CHANNEL,
            "acknowledged_at": now,
            "group_status": (
                conn.execute(
                    "SELECT status FROM notification_group WHERE notification_group_id=?",
                    (notification_group_id,),
                ).fetchone()["status"]
            ),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
